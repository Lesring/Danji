import os

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.fixtures import load_demo
from app.models import (
    AuditLog,
    BusinessEvent,
    Customer,
    CustomerIntake,
    ExtractionLog,
    Order,
    ProductSupply,
    Risk,
    Source,
    SupplyConstraint,
    Task,
    TaskSuggestion,
    now_iso,
)
from app.services.extract import run_extraction
from app.services.intake import preview_customer_message
from app.services.supply import supply_key

_CLEARED = "workbench_cleared"
_DEMO_REVISION = "demo_revision"

_WIPE_MODELS = (
    ExtractionLog,
    BusinessEvent,
    Risk,
    TaskSuggestion,
    Task,
    SupplyConstraint,
    Source,
    CustomerIntake,
    AuditLog,
    Order,
    Customer,
)


def _ensure_meta(db: Session) -> None:
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS app_meta (key VARCHAR(40) PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"
        )
    )


def _meta_value(db: Session, key: str) -> str:
    _ensure_meta(db)
    row = db.execute(text("SELECT value FROM app_meta WHERE key = :key"), {"key": key}).first()
    return row[0] if row else ""


def _set_meta_value(db: Session, key: str, value: str) -> None:
    _ensure_meta(db)
    db.execute(
        text(
            "INSERT INTO app_meta (key, value) VALUES (:key, :value) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        ),
        {"key": key, "value": value},
    )


def demo_revision_target() -> str:
    return str(load_demo().get("demo_revision") or "1")


def workbench_was_cleared(db: Session) -> bool:
    return _meta_value(db, _CLEARED) == "1"


def mark_workbench_cleared(db: Session) -> None:
    _set_meta_value(db, _CLEARED, "1")


def warehouse_catalog() -> dict[str, dict]:
    demo = load_demo()
    rows: dict[str, dict] = {}
    for item in demo["orders"]:
        if item.get("deleted_at"):
            continue
        rows[item["product"]] = {
            "on_hand": item["available_qty"],
            "earliest_completion": item["earliest_completion"],
            "note": item.get("supply_note", ""),
        }
    for item in demo.get("products") or []:
        name = (item.get("product") or "").strip()
        if not name:
            continue
        rows[name] = {
            "on_hand": item["on_hand"],
            "earliest_completion": item["earliest_completion"],
            "note": item.get("note", ""),
        }
    return rows


def restore_warehouse(db: Session) -> None:
    wanted = warehouse_catalog()
    existing = {supply.product: supply for supply in db.scalars(select(ProductSupply))}
    for name, spec in wanted.items():
        supply = existing.get(name)
        if supply is None:
            db.add(
                ProductSupply(
                    product=name,
                    on_hand=spec["on_hand"],
                    earliest_completion=spec["earliest_completion"],
                    note=spec["note"],
                    updated_by="系统",
                )
            )
            continue
        supply.on_hand = spec["on_hand"]
        supply.earliest_completion = spec["earliest_completion"]
        supply.note = spec["note"]
        supply.source_id = None
        supply.updated_by = "系统"
    for name, supply in existing.items():
        if name not in wanted:
            db.delete(supply)
    db.flush()


def _wipe_workbench_data(db: Session) -> None:
    for supply in db.scalars(select(ProductSupply)):
        supply.source_id = None
    db.flush()
    for model in _WIPE_MODELS:
        db.execute(delete(model))
    db.flush()


def clear_workbench(db: Session) -> None:
    _wipe_workbench_data(db)
    mark_workbench_cleared(db)


def prepare_manual_workspace(db: Session) -> None:
    restore_warehouse(db)
    clear_workbench(db)


def seed_if_empty(db: Session) -> None:
    if workbench_was_cleared(db):
        ensure_catalog_supplies(db)
        return
    stored_revision = _meta_value(db, _DEMO_REVISION)
    target_revision = demo_revision_target()
    has_customers = db.scalar(select(Customer.id).limit(1)) is not None
    if has_customers and stored_revision == target_revision:
        return
    if has_customers:
        _wipe_workbench_data(db)
    _apply_demo_seed(db)
    _set_meta_value(db, _DEMO_REVISION, target_revision)


def _apply_demo_seed(db: Session) -> None:
    demo = load_demo()
    customers = {}
    for item in demo["customers"]:
        customer = Customer(name=item["name"], contact=item.get("contact", ""), note=item.get("note", ""))
        db.add(customer)
        db.flush()
        customers[customer.name] = customer
    for item in demo["orders"]:
        customer = customers[item["customer_name"]]
        order = Order(
            order_no=item["order_no"],
            customer_id=customer.id,
            product=item.get("product", ""),
            quantity=item["quantity"],
            delivered_qty=int(item.get("delivered_qty") or 0),
            delivery_date=item["delivery_date"],
            status=item.get("status", "已确认"),
            owner=item.get("owner", "订单负责人"),
            deleted_at=item.get("deleted_at"),
        )
        db.add(order)
        db.flush()
        db.add(
            SupplyConstraint(
                order_id=order.id,
                available_qty=item["available_qty"],
                earliest_completion=item["earliest_completion"],
                note=item.get("supply_note", ""),
            )
        )
        if not item.get("deleted_at") and db.scalar(
            select(ProductSupply).where(ProductSupply.product == supply_key(order))
        ) is None:
            db.add(
                ProductSupply(
                    product=supply_key(order),
                    on_hand=item["available_qty"],
                    earliest_completion=item["earliest_completion"],
                    note=item.get("supply_note", ""),
                    updated_by="系统",
                )
            )
    db.flush()
    ensure_catalog_supplies(db)
    _seed_demo_tasks(db, demo)
    _seed_scenarios(db, demo)
    _seed_intakes(db, demo, customers)
    db.flush()


def _seed_demo_tasks(db: Session, demo: dict) -> None:
    if os.getenv("DANJI_SEED_TASKS", "1").strip() == "0":
        return
    rows = demo.get("seed_tasks") or []
    if not rows:
        return
    orders = {order.order_no: order for order in db.scalars(select(Order))}
    stamp = now_iso()
    for index, item in enumerate(rows):
        order_no = (item.get("order_no") or "").strip()
        order = orders.get(order_no)
        if order is None or order.deleted_at:
            continue
        db.add(
            Task(
                order_id=order.id,
                order_no=order.order_no,
                suggestion_key=f"seed-{order.id}-{index}",
                title=(item.get("title") or "待办")[:200],
                owner=(item.get("owner") or "")[:80],
                due_date=(item.get("due_date") or None),
                status=(item.get("status") or "待处理")[:20],
                reason=(item.get("reason") or "")[:2000],
                created_at=stamp,
                updated_at=stamp,
            )
        )


def _seed_scenarios(db: Session, demo: dict) -> None:
    samples = {item["id"]: item for item in demo.get("samples") or []}
    orders = {order.order_no: order for order in db.scalars(select(Order))}
    for item in demo.get("seed_scenarios") or []:
        order = orders.get((item.get("order_no") or "").strip())
        sample = samples.get(item.get("sample_id"))
        if order is None or order.deleted_at or sample is None:
            continue
        text_body = (sample.get("text") or "").strip()
        if not text_body:
            continue
        source = Source(
            order_id=order.id,
            type=sample.get("type") or "chat",
            title=(sample.get("title") or "演示导入")[:200],
            raw_text=text_body,
            business_time=sample.get("business_time"),
            created_at=now_iso(),
        )
        db.add(source)
        db.flush()
        run_extraction(db, source)


def _seed_intakes(db: Session, demo: dict, customers: dict[str, Customer]) -> None:
    for item in demo.get("seed_intakes") or []:
        customer = customers.get((item.get("customer_name") or "").strip())
        text_body = (item.get("text") or "").strip()
        if customer is None or not text_body:
            continue
        if db.scalar(select(CustomerIntake.id).where(CustomerIntake.customer_id == customer.id).limit(1)):
            continue
        preview_customer_message(db, customer.id, text_body, title=item.get("title") or "客户沟通")


def ensure_catalog_supplies(db: Session) -> None:
    for item in load_demo().get("products") or []:
        name = (item.get("product") or "").strip()
        if not name:
            continue
        if db.scalar(select(ProductSupply).where(ProductSupply.product == name)) is not None:
            continue
        db.add(
            ProductSupply(
                product=name,
                on_hand=item["on_hand"],
                earliest_completion=item["earliest_completion"],
                note=item.get("note", ""),
                updated_by="系统",
            )
        )
    db.flush()

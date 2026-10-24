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
from app.services.supply import supply_key

_CLEARED = "workbench_cleared"


def _ensure_meta(db: Session) -> None:
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS app_meta (key VARCHAR(40) PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"
        )
    )


def workbench_was_cleared(db: Session) -> bool:
    _ensure_meta(db)
    row = db.execute(text("SELECT value FROM app_meta WHERE key = :key"), {"key": _CLEARED}).first()
    return row is not None and row[0] == "1"


def mark_workbench_cleared(db: Session) -> None:
    _ensure_meta(db)
    db.execute(
        text(
            "INSERT INTO app_meta (key, value) VALUES (:key, '1') "
            "ON CONFLICT(key) DO UPDATE SET value = '1'"
        ),
        {"key": _CLEARED},
    )


def warehouse_catalog() -> dict[str, dict]:
    demo = load_demo()
    rows: dict[str, dict] = {}
    for item in demo["orders"]:
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


def clear_workbench(db: Session) -> None:
    for supply in db.scalars(select(ProductSupply)):
        supply.source_id = None
    db.flush()
    for model in (
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
    ):
        db.execute(delete(model))
    db.flush()
    mark_workbench_cleared(db)


def prepare_manual_workspace(db: Session) -> None:
    restore_warehouse(db)
    clear_workbench(db)


def seed_if_empty(db: Session) -> None:
    exists = db.scalar(select(Customer.id).limit(1))
    if exists:
        return
    if workbench_was_cleared(db):
        ensure_catalog_supplies(db)
        return
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
            delivery_date=item["delivery_date"],
            status=item.get("status", "已确认"),
            owner=item.get("owner", "订单负责人"),
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
        if db.scalar(select(ProductSupply).where(ProductSupply.product == supply_key(order))) is None:
            db.add(
                ProductSupply(
                    product=supply_key(order),
                    on_hand=item["available_qty"],
                    earliest_completion=item["earliest_completion"],
                    note=item.get("supply_note", ""),
                    updated_by="系统",
                )
            )
    ensure_catalog_supplies(db)
    _seed_demo_tasks(db, demo)
    db.flush()


def _seed_demo_tasks(db: Session, demo: dict) -> None:
    if os.getenv("DANJI_SEED_TASKS", "1").strip() == "0":
        return
    rows = demo.get("seed_tasks") or []
    if not rows:
        return
    if db.scalar(select(Task.id).limit(1)) is not None:
        return
    orders = {order.order_no: order for order in db.scalars(select(Order))}
    stamp = now_iso()
    for item in rows:
        order_no = (item.get("order_no") or "").strip()
        order = orders.get(order_no)
        if order is None:
            continue
        db.add(
            Task(
                order_id=order.id,
                order_no=order.order_no,
                suggestion_key=f"seed-{order.id}-{len(item.get('title') or '')}",
                title=(item.get("title") or "待办")[:200],
                owner=(item.get("owner") or "")[:80],
                due_date=(item.get("due_date") or None),
                status=(item.get("status") or "待处理")[:20],
                reason=(item.get("reason") or "")[:2000],
                created_at=stamp,
                updated_at=stamp,
            )
        )


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

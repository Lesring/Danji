import re
from datetime import date

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    BusinessEvent,
    Customer,
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
from app.schemas import CustomerIn, OrderFields, OrderIn
from app.services.apply import OPERATOR
from app.services.supply import ensure_product_supply, get_product_supply, recompute_product
from app.util import dumps, parse_date

KEPT_TASK_STATUSES = {"已完成", "已关闭"}


def add_customer(db: Session, payload: CustomerIn) -> tuple[Customer, list[Order]]:
    customer = Customer(name=_customer_name(payload.name), contact=payload.contact.strip(), note=payload.note.strip())
    db.add(customer)
    db.flush()
    payloads = list(payload.orders or [])
    if payload.order is not None and not payloads:
        payloads = [payload.order]
    return customer, [add_order(db, item, customer) for item in payloads]


def add_order_for_customer(db: Session, payload: OrderIn) -> tuple[Customer, Order]:
    customer = db.get(Customer, payload.customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    order = add_order(db, payload, customer)
    return customer, order


def order_count(db: Session, customer_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.customer_id == customer_id, Order.deleted_at.is_(None))
        )
        or 0
    )


def add_order(db: Session, payload: OrderFields, customer: Customer) -> Order:
    fields = _order_fields(payload)
    existing = db.scalar(select(Order).where(Order.order_no == fields["order_no"]))
    if existing is not None:
        if existing.deleted_at:
            raise HTTPException(status_code=409, detail="订单号在回收站中，恢复或彻底删除后才能再用")
        raise HTTPException(status_code=409, detail="订单号已存在")
    order = Order(
        order_no=fields["order_no"],
        customer_id=customer.id,
        product=fields["product"],
        quantity=fields["quantity"],
        delivery_date=fields["delivery_date"],
        status="已确认",
        owner=fields["owner"],
    )
    db.add(order)
    db.flush()
    supply = ensure_product_supply(
        db,
        order,
        on_hand=fields["available_qty"],
        earliest=fields["earliest_completion"],
        note=fields["supply_note"],
        overwrite=False,
    )
    db.add(
        SupplyConstraint(
            order_id=order.id,
            available_qty=supply.on_hand,
            earliest_completion=supply.earliest_completion,
            note=supply.note,
        )
    )
    db.flush()
    recompute_product(db, order, None)
    return order


def next_order_no(db: Session) -> str:
    highest = 0
    for order_no in db.scalars(select(Order.order_no)):
        matched = re.fullmatch(r"ORD-(\d+)", order_no or "")
        if matched:
            highest = max(highest, int(matched.group(1)))
    candidate = highest + 1
    while db.scalar(select(Order.id).where(Order.order_no == f"ORD-{candidate:03d}")) is not None:
        candidate += 1
    return f"ORD-{candidate:03d}"


def create_suggested_order(
    db: Session,
    *,
    customer_id: int,
    product: str,
    quantity: int,
    delivery_date: str,
    owner: str,
    on_hand: int | None,
    earliest: str | None,
    operator: str,
    source_id: int | None,
) -> Order:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    name = product.strip()
    if not name:
        raise HTTPException(status_code=400, detail="商品名称不能为空")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="数量必须是正整数")
    delivery = parse_date(delivery_date) or date.today().isoformat()
    stated_earliest = parse_date(earliest) if earliest else None
    earliest_value = stated_earliest or delivery
    supply = get_product_supply(db, name)
    created_supply = supply is None
    if supply is None:
        stated_on_hand = on_hand is not None
        hand = on_hand if stated_on_hand else 0
        if hand < 0:
            raise HTTPException(status_code=400, detail="在手数量不能为负数")
        details: list[str] = []
        if not stated_on_hand:
            details.append("对话未说明在手数量，暂记 0")
        if stated_earliest is None:
            details.append("最早可完工日期未说明，暂用订单交期或今天")
        if details:
            note = "待核实：" + "；".join(details)
        else:
            note = "对话已说明在手数量和最早可完工日期"
        supply = ProductSupply(
            product=name,
            on_hand=hand,
            earliest_completion=earliest_value,
            note=note,
            source_id=source_id,
            updated_at=now_iso(),
            updated_by=operator or "演示用户",
        )
        db.add(supply)
        db.flush()
    order = Order(
        order_no=next_order_no(db),
        customer_id=customer.id,
        product=name,
        quantity=quantity,
        delivered_qty=0,
        delivery_date=delivery,
        status="已确认",
        owner=owner.strip() or "订单负责人",
    )
    db.add(order)
    db.flush()
    db.add(
        SupplyConstraint(
            order_id=order.id,
            available_qty=supply.on_hand,
            earliest_completion=supply.earliest_completion,
            note=supply.note,
            source_id=source_id if created_supply else None,
        )
    )
    db.add(
        AuditLog(
            object_type="order",
            object_id=order.id,
            action="create_from_draft",
            before_json=dumps({"customer_id": customer.id, "product": name}),
            after_json=dumps(
                {
                    "order_no": order.order_no,
                    "product": name,
                    "quantity": quantity,
                    "delivery_date": delivery,
                    "on_hand": supply.on_hand,
                    "earliest_completion": supply.earliest_completion,
                }
            ),
            operator=operator or "演示用户",
            note="确认后为该客户新增商品订单",
            created_at=now_iso(),
        )
    )
    db.flush()
    recompute_product(db, order, source_id)
    return order


def move_order_to_trash(db: Session, order_id: int) -> Order:
    order = _order_or_404(db, order_id)
    if order.deleted_at:
        raise HTTPException(status_code=409, detail="订单已在回收站")
    order.deleted_at = now_iso()
    _audit_order(db, order, "trash", "移入回收站")
    recompute_product(db, order, None)
    db.commit()
    db.refresh(order)
    return order


def restore_order(db: Session, order_id: int) -> Order:
    order = _order_or_404(db, order_id)
    if not order.deleted_at:
        raise HTTPException(status_code=409, detail="订单不在回收站")
    order.deleted_at = None
    _audit_order(db, order, "restore", "从回收站恢复")
    recompute_product(db, order, None)
    db.commit()
    db.refresh(order)
    return order


def purge_order(db: Session, order_id: int) -> None:
    order = _order_or_404(db, order_id)
    if not order.deleted_at:
        raise HTTPException(status_code=409, detail="请先把订单移入回收站，再彻底删除")
    source_ids = list(db.scalars(select(Source.id).where(Source.order_id == order.id)))
    tasks = list(db.scalars(select(Task).where(Task.order_id == order.id)))
    if source_ids and _other_order_uses_source(db, order.id, source_ids):
        raise HTTPException(
            status_code=409,
            detail=f"其他订单还引用「{order.order_no}」的沟通证据，不能彻底删除。那些订单还在时，证据会保留。",
        )
    kept = [task for task in tasks if task.status in KEPT_TASK_STATUSES]
    removed_ids = [task.id for task in tasks if task.status not in KEPT_TASK_STATUSES]
    _detach_shared_refs(db, order.id, source_ids, removed_ids)
    for task in kept:
        task.order_no = task.order_no or order.order_no
        task.order_id = None
        task.source_id = None
    if source_ids:
        db.execute(delete(ExtractionLog).where(ExtractionLog.source_id.in_(source_ids)))
    db.execute(delete(BusinessEvent).where(BusinessEvent.order_id == order.id))
    db.execute(delete(Risk).where(Risk.order_id == order.id))
    db.execute(delete(TaskSuggestion).where(TaskSuggestion.order_id == order.id))
    if removed_ids:
        db.execute(delete(Task).where(Task.id.in_(removed_ids)))
    db.execute(delete(SupplyConstraint).where(SupplyConstraint.order_id == order.id))
    if source_ids:
        db.execute(delete(Source).where(Source.id.in_(source_ids)))
    db.delete(order)
    db.commit()


def _other_order_uses_source(db: Session, order_id: int, source_ids: list[int]) -> bool:
    referenced = db.scalar(
        select(BusinessEvent.id).where(
            BusinessEvent.source_id.in_(source_ids),
            BusinessEvent.order_id != order_id,
        )
    )
    return referenced is not None


def _detach_shared_refs(db: Session, order_id: int, source_ids: list[int], removed_task_ids: list[int]) -> None:
    if source_ids:
        for supply in db.scalars(select(ProductSupply).where(ProductSupply.source_id.in_(source_ids))):
            supply.source_id = None
        for constraint in db.scalars(
            select(SupplyConstraint).where(
                SupplyConstraint.source_id.in_(source_ids),
                SupplyConstraint.order_id != order_id,
            )
        ):
            constraint.source_id = None
        for task in db.scalars(select(Task).where(Task.source_id.in_(source_ids), Task.order_id != order_id)):
            task.source_id = None
        for suggestion in db.scalars(
            select(TaskSuggestion).where(
                TaskSuggestion.source_id.in_(source_ids),
                TaskSuggestion.order_id != order_id,
            )
        ):
            suggestion.source_id = None
        for risk in db.scalars(select(Risk).where(Risk.source_id.in_(source_ids), Risk.order_id != order_id)):
            risk.source_id = None
    if removed_task_ids:
        for suggestion in db.scalars(
            select(TaskSuggestion).where(
                TaskSuggestion.target_task_id.in_(removed_task_ids),
                TaskSuggestion.order_id != order_id,
            )
        ):
            suggestion.target_task_id = None


def _order_or_404(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order


def _audit_order(db: Session, order: Order, action: str, note: str) -> None:
    db.add(
        AuditLog(
            object_type="order",
            object_id=order.id,
            action=action,
            before_json=dumps({"order_no": order.order_no}),
            after_json=dumps({"deleted_at": order.deleted_at}),
            operator=OPERATOR,
            note=note,
            created_at=now_iso(),
        )
    )


def _customer_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="客户名称不能为空")
    if len(cleaned) > 120:
        raise HTTPException(status_code=400, detail="客户名称过长")
    return cleaned


def _order_fields(payload: OrderFields) -> dict:
    order_no = payload.order_no.strip()
    if not order_no:
        raise HTTPException(status_code=400, detail="订单号不能为空")
    if len(order_no) > 40:
        raise HTTPException(status_code=400, detail="订单号过长")
    if payload.quantity <= 0:
        raise HTTPException(status_code=400, detail="数量必须是正整数")
    if payload.available_qty < 0:
        raise HTTPException(status_code=400, detail="可用库存不能为负数")
    delivery = parse_date(payload.delivery_date)
    earliest = parse_date(payload.earliest_completion)
    if delivery is None:
        raise HTTPException(status_code=400, detail="交期格式应为 YYYY-MM-DD")
    if earliest is None:
        raise HTTPException(status_code=400, detail="最早可完工日期格式应为 YYYY-MM-DD")
    return {
        "order_no": order_no,
        "product": payload.product.strip(),
        "quantity": payload.quantity,
        "delivery_date": delivery,
        "owner": payload.owner.strip() or "订单负责人",
        "available_qty": payload.available_qty,
        "earliest_completion": earliest,
        "supply_note": payload.supply_note.strip(),
    }

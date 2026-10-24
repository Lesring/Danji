from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BusinessEvent, Customer, Order, Source, TaskSuggestion
from app.present import audit_dict, event_dict, order_dict, supply_dict
from app.services.import_summary import list_import_history
from app.schemas import OrderBatchIn, OrderIn, SupplyPatch
from app.services.apply import order_audit_entries, require_active_order
from app.services.records import add_order_for_customer, move_order_to_trash, purge_order, restore_order
from app.services.supply import recompute_product, supply_view, write_supply

router = APIRouter(tags=["orders"])


def pending_count(db: Session, order_id: int) -> int:
    events = db.scalar(
        select(func.count()).select_from(BusinessEvent).where(
            BusinessEvent.order_id == order_id, BusinessEvent.status == "draft"
        )
    ) or 0
    tasks = db.scalar(
        select(func.count()).select_from(TaskSuggestion).where(
            TaskSuggestion.order_id == order_id, TaskSuggestion.status == "draft"
        )
    ) or 0
    return int(events + tasks)


@router.post("/orders")
def create_order(payload: OrderIn, db: Session = Depends(get_db)):
    customer, order = add_order_for_customer(db, payload)
    db.commit()
    return order_dict(order, customer.name, 0)


@router.post("/orders/batch")
def create_orders(payload: OrderBatchIn, db: Session = Depends(get_db)):
    if not payload.orders:
        raise HTTPException(status_code=400, detail="至少要有一张订单")
    customer = db.get(Customer, payload.customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    created = []
    for item in payload.orders:
        _customer, order = add_order_for_customer(
            db,
            OrderIn(customer_id=payload.customer_id, **item.model_dump()),
        )
        created.append(order_dict(order, _customer.name, 0))
    db.commit()
    return {"orders": created}


@router.get("/orders")
def list_orders(
    customer_id: int | None = None,
    product: str | None = None,
    trashed: bool = False,
    db: Session = Depends(get_db),
):
    stmt = select(Order).order_by(Order.id)
    if trashed:
        stmt = stmt.where(Order.deleted_at.is_not(None))
    else:
        stmt = stmt.where(Order.deleted_at.is_(None))
    if customer_id is not None:
        stmt = stmt.where(Order.customer_id == customer_id)
    cleaned_product = (product or "").strip()
    if cleaned_product:
        stmt = stmt.where(Order.product == cleaned_product)
    rows = []
    for order in db.scalars(stmt):
        customer = db.get(Customer, order.customer_id)
        rows.append(order_dict(order, customer.name if customer else "", pending_count(db, order.id)))
    return rows


@router.delete("/orders/{order_id}")
def trash_order(order_id: int, db: Session = Depends(get_db)):
    order = move_order_to_trash(db, order_id)
    customer = db.get(Customer, order.customer_id)
    return order_dict(order, customer.name if customer else "", pending_count(db, order.id))


@router.post("/orders/{order_id}/restore")
def restore_trashed_order(order_id: int, db: Session = Depends(get_db)):
    order = restore_order(db, order_id)
    customer = db.get(Customer, order.customer_id)
    return order_dict(order, customer.name if customer else "", pending_count(db, order.id))


@router.delete("/orders/{order_id}/purge")
def purge_trashed_order(order_id: int, db: Session = Depends(get_db)):
    purge_order(db, order_id)
    return {"id": order_id, "purged": True}


@router.patch("/orders/{order_id}/supply")
def patch_supply(order_id: int, payload: SupplyPatch, db: Session = Depends(get_db)):
    order = require_active_order(db, order_id)
    write_supply(
        db,
        order,
        on_hand=payload.on_hand,
        earliest=payload.earliest_completion,
        operator=payload.operator,
    )
    recompute_product(db, order, None)
    db.commit()
    return supply_dict(supply_view(db, order))


@router.get("/orders/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    customer = db.get(Customer, order.customer_id)
    supply = supply_view(db, order)
    events = db.scalars(select(BusinessEvent).where(BusinessEvent.order_id == order.id).order_by(BusinessEvent.id)).all()
    sources = {item.id: item for item in db.scalars(select(Source).where(Source.order_id == order.id))}
    audits = order_audit_entries(db, order.id)
    return {
        "order": order_dict(order, customer.name if customer else "", pending_count(db, order.id)),
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "contact": customer.contact,
            "note": customer.note,
        }
        if customer
        else None,
        "supply": supply_dict(supply),
        "timeline": [event_dict(event, sources.get(event.source_id)) for event in events],
        "audits": [audit_dict(item) for item in audits],
        "imports": list_import_history(db, order, customer.name if customer else ""),
    }

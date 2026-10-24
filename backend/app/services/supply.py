from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Customer, Order, ProductSupply, SupplyConstraint, now_iso
from app.util import dumps, parse_date

# 待确认、已确认占用共用在手。待确认取消、已取消、已关闭以及回收站中的订单不占用。
RELEASED_STATUSES = {"待确认取消", "已取消", "已关闭"}


def supply_key(order: Order) -> str:
    name = (order.product or "").strip()
    if name:
        return name
    return f"未命名商品·{order.order_no}"


def order_reserves(order: Order) -> bool:
    return order.deleted_at is None and order.status not in RELEASED_STATUSES


def outstanding_qty(order: Order) -> int:
    delivered = int(order.delivered_qty or 0)
    if delivered < 0:
        delivered = 0
    return max(0, int(order.quantity) - delivered)


def get_product_supply(db: Session, product: str) -> ProductSupply | None:
    return db.scalar(select(ProductSupply).where(ProductSupply.product == product))


def referencing_orders(db: Session, product: str) -> list[Order]:
    if not product:
        return []
    return [order for order in db.scalars(select(Order).order_by(Order.id)) if supply_key(order) == product]


def orders_for_product(db: Session, product: str) -> list[Order]:
    if not product:
        return []
    return [
        order
        for order in db.scalars(select(Order).where(Order.deleted_at.is_(None)).order_by(Order.id))
        if supply_key(order) == product
    ]


def customer_open_order(db: Session, customer_id: int, product: str) -> Order | None:
    name = (product or "").strip()
    if not name:
        return None
    for order in db.scalars(
        select(Order).where(Order.customer_id == customer_id, Order.deleted_at.is_(None)).order_by(Order.id)
    ):
        if (order.product or "").strip() == name:
            return order
    return None


def ensure_product_supply(
    db: Session,
    order: Order,
    *,
    on_hand: int | None = None,
    earliest: str | None = None,
    note: str | None = None,
    overwrite: bool = False,
) -> ProductSupply:
    supply = get_product_supply(db, supply_key(order))
    if supply is None:
        if on_hand is None or earliest is None:
            constraint = db.scalar(select(SupplyConstraint).where(SupplyConstraint.order_id == order.id))
            if constraint is None:
                raise HTTPException(status_code=400, detail="订单缺少供应记录")
            on_hand = constraint.available_qty
            earliest = constraint.earliest_completion
            if note is None:
                note = constraint.note
        supply = ProductSupply(
            product=supply_key(order),
            on_hand=on_hand,
            earliest_completion=earliest,
            note=note or "",
            updated_at=now_iso(),
            updated_by="系统",
        )
        db.add(supply)
        db.flush()
        return supply
    if overwrite:
        if on_hand is not None:
            supply.on_hand = on_hand
        if earliest is not None:
            supply.earliest_completion = earliest
        if note is not None:
            supply.note = note
        supply.updated_at = now_iso()
    return supply


def backfill_product_supplies(db: Session) -> None:
    for order in db.scalars(select(Order)):
        if get_product_supply(db, supply_key(order)) is not None:
            continue
        constraint = db.scalar(select(SupplyConstraint).where(SupplyConstraint.order_id == order.id))
        if constraint is None:
            continue
        db.add(
            ProductSupply(
                product=supply_key(order),
                on_hand=constraint.available_qty,
                earliest_completion=constraint.earliest_completion,
                note=constraint.note,
                updated_at=now_iso(),
                updated_by="系统",
            )
        )
    db.flush()


def _reserved(db: Session, product: str, exclude_order_id: int | None) -> int:
    total = 0
    for order in orders_for_product(db, product):
        if exclude_order_id is not None and order.id == exclude_order_id:
            continue
        if order_reserves(order):
            total += outstanding_qty(order)
    return total


def supply_view(db: Session, order: Order) -> dict | None:
    supply = ensure_product_supply(db, order)
    others = _reserved(db, supply.product, exclude_order_id=order.id)
    own = outstanding_qty(order) if order_reserves(order) else 0
    # 在手是该商品的共用库存。本单可承诺上限不把本单再减一次。
    available = supply.on_hand - others
    committable = available - own
    delivered = int(order.delivered_qty or 0)
    if delivered > 0:
        note = (
            f"在手 {supply.on_hand} 件，未关闭订单占用 {others + own} 件"
            f"（本单已交付 {delivered} 件，未交 {own} 件），还可承诺 {committable} 件。"
            f"本单最多可承诺 {available} 件。最早可完工日期 {supply.earliest_completion}。"
        )
    else:
        note = (
            f"在手 {supply.on_hand} 件，未关闭订单占用 {others + own} 件，还可承诺 {committable} 件。"
            f"本单最多可承诺 {available} 件。最早可完工日期 {supply.earliest_completion}。"
        )
    if supply.note:
        note = f"{note} {supply.note}"
    if supply.updated_at:
        note = f"{note} {supply.updated_by}于 {supply.updated_at.replace('T', ' ')} 更新。"
    return {
        "product": supply.product,
        "on_hand": supply.on_hand,
        "reserved": others + own,
        "committable": committable,
        "available_qty": available,
        "earliest_completion": supply.earliest_completion,
        "note": note,
        "updated_at": supply.updated_at,
        "updated_by": supply.updated_by,
        "reserves": order_reserves(order),
    }


def write_supply(
    db: Session,
    order: Order,
    *,
    on_hand: int | None = None,
    earliest: str | None = None,
    note: str | None = None,
    operator: str,
    source_id: int | None = None,
) -> ProductSupply:
    supply = ensure_product_supply(db, order)
    before = {"on_hand": supply.on_hand, "earliest_completion": supply.earliest_completion, "note": supply.note}
    changed = False
    if on_hand is not None:
        if on_hand < 0:
            raise HTTPException(status_code=400, detail="在手数量不能为负数")
        if on_hand != supply.on_hand:
            supply.on_hand = on_hand
            changed = True
    if earliest is not None:
        parsed = parse_date(earliest)
        if parsed is None:
            raise HTTPException(status_code=400, detail="最早可完工日期格式应为 YYYY-MM-DD")
        if parsed != supply.earliest_completion:
            supply.earliest_completion = parsed
            changed = True
    if note is not None:
        cleaned = note.strip()
        if cleaned != (supply.note or ""):
            supply.note = cleaned
            changed = True
    if not changed:
        return supply
    supply.updated_at = now_iso()
    supply.updated_by = operator or "演示用户"
    if source_id is not None:
        supply.source_id = source_id
    db.add(
        AuditLog(
            object_type="product_supply",
            object_id=supply.id,
            action="update_supply",
            before_json=dumps(before),
            after_json=dumps(
                {
                    "on_hand": supply.on_hand,
                    "earliest_completion": supply.earliest_completion,
                    "note": supply.note,
                }
            ),
            operator=supply.updated_by,
            note="更新商品在手数量、最早可完工日期或备注",
            created_at=supply.updated_at,
        )
    )
    _mirror_constraints(db, supply)
    db.flush()
    return supply


def _mirror_constraints(db: Session, supply: ProductSupply) -> None:
    for order in db.scalars(select(Order)):
        if supply_key(order) != supply.product:
            continue
        row = db.scalar(select(SupplyConstraint).where(SupplyConstraint.order_id == order.id))
        if row is None:
            continue
        row.available_qty = supply.on_hand
        row.earliest_completion = supply.earliest_completion


def recompute_product(db: Session, order: Order, source_id: int | None = None) -> None:
    from app.services.extract import refresh_product

    refresh_product(db, supply_key(order), source_id)


def product_supply_dict(db: Session, supply: ProductSupply) -> dict:
    reserved = _reserved(db, supply.product, None)
    active = orders_for_product(db, supply.product)
    return {
        "product": supply.product,
        "on_hand": supply.on_hand,
        "reserved": reserved,
        "committable": supply.on_hand - reserved,
        "earliest_completion": supply.earliest_completion,
        "note": supply.note or "",
        "updated_at": supply.updated_at,
        "updated_by": supply.updated_by,
        "order_id": active[0].id if active else None,
    }


def list_product_supplies(db: Session) -> list[dict]:
    supplies = db.scalars(select(ProductSupply).order_by(ProductSupply.id)).all()
    return [product_supply_dict(db, supply) for supply in supplies]


def create_product_supply(
    db: Session,
    *,
    product: str,
    on_hand: int,
    earliest: str,
    note: str,
    operator: str,
) -> ProductSupply:
    name = product.strip()
    if not name:
        raise HTTPException(status_code=400, detail="商品名称不能为空")
    if len(name) > 120:
        raise HTTPException(status_code=400, detail="商品名称过长")
    if get_product_supply(db, name) is not None:
        raise HTTPException(status_code=409, detail="这个商品已经在仓库里")
    parsed = _require_supply_values(on_hand, earliest)
    supply = ProductSupply(
        product=name,
        on_hand=on_hand,
        earliest_completion=parsed,
        note=(note or "").strip(),
        updated_at=now_iso(),
        updated_by=operator or "演示用户",
    )
    db.add(supply)
    db.flush()
    db.add(
        AuditLog(
            object_type="product_supply",
            object_id=supply.id,
            action="create_supply",
            before_json=dumps({}),
            after_json=dumps(
                {
                    "product": supply.product,
                    "on_hand": supply.on_hand,
                    "earliest_completion": supply.earliest_completion,
                    "note": supply.note,
                }
            ),
            operator=supply.updated_by,
            note="在仓库新增商品",
            created_at=supply.updated_at,
        )
    )
    db.flush()
    return supply


def update_listed_supply(
    db: Session,
    supply: ProductSupply,
    *,
    on_hand: int,
    earliest: str,
    operator: str,
    note: str | None = None,
) -> ProductSupply:
    parsed = _require_supply_values(on_hand, earliest)
    cleaned = None if note is None else note.strip()
    note_changed = cleaned is not None and cleaned != (supply.note or "")
    if on_hand == supply.on_hand and parsed == supply.earliest_completion and not note_changed:
        return supply
    before = {"on_hand": supply.on_hand, "earliest_completion": supply.earliest_completion, "note": supply.note}
    supply.on_hand = on_hand
    supply.earliest_completion = parsed
    if cleaned is not None:
        supply.note = cleaned
    supply.updated_at = now_iso()
    supply.updated_by = operator or "演示用户"
    db.add(
        AuditLog(
            object_type="product_supply",
            object_id=supply.id,
            action="update_supply",
            before_json=dumps(before),
            after_json=dumps(
                {
                    "on_hand": supply.on_hand,
                    "earliest_completion": supply.earliest_completion,
                    "note": supply.note,
                }
            ),
            operator=supply.updated_by,
            note="更新商品在手数量、最早可完工日期或备注",
            created_at=supply.updated_at,
        )
    )
    _mirror_constraints(db, supply)
    db.flush()
    return supply


def save_warehouse_item(
    db: Session,
    *,
    product: str,
    on_hand: int,
    earliest: str,
    note: str | None,
    name: str | None,
    operator: str,
) -> ProductSupply:
    current_name = product.strip()
    supply = get_product_supply(db, current_name)
    if supply is None:
        raise HTTPException(status_code=404, detail="没有这个商品的供应记录")
    requested = supply.product if name is None else name.strip()
    if not requested:
        raise HTTPException(status_code=400, detail="商品名称不能为空")
    if len(requested) > 120:
        raise HTTPException(status_code=400, detail="商品名称过长")
    if requested != supply.product:
        users = referencing_orders(db, supply.product)
        if users:
            raise HTTPException(status_code=409, detail=_usage_message(db, supply.product, users, "修改商品名称"))
        if get_product_supply(db, requested) is not None:
            raise HTTPException(status_code=409, detail="这个商品已经在仓库里")
        before_name = supply.product
        supply.product = requested
        supply.updated_at = now_iso()
        supply.updated_by = operator or "演示用户"
        db.add(
            AuditLog(
                object_type="product_supply",
                object_id=supply.id,
                action="rename_supply",
                before_json=dumps({"product": before_name}),
                after_json=dumps({"product": supply.product}),
                operator=supply.updated_by,
                note="修改仓库商品名称",
                created_at=supply.updated_at,
            )
        )
    orders = orders_for_product(db, supply.product)
    before_hand = supply.on_hand
    before_earliest = supply.earliest_completion
    if orders:
        write_supply(
            db,
            orders[0],
            on_hand=on_hand,
            earliest=earliest,
            note=note,
            operator=operator,
        )
        if supply.on_hand != before_hand or supply.earliest_completion != before_earliest:
            recompute_product(db, orders[0], None)
    else:
        update_listed_supply(
            db,
            supply,
            on_hand=on_hand,
            earliest=earliest,
            operator=operator,
            note=note,
        )
    db.flush()
    return supply


def delete_product_supply(db: Session, product: str) -> None:
    name = product.strip()
    supply = get_product_supply(db, name)
    if supply is None:
        raise HTTPException(status_code=404, detail="没有这个商品的供应记录")
    users = referencing_orders(db, supply.product)
    if users:
        raise HTTPException(status_code=409, detail=_usage_message(db, supply.product, users, "删除"))
    deleted_at = now_iso()
    db.add(
        AuditLog(
            object_type="product_supply",
            object_id=supply.id,
            action="delete_supply",
            before_json=dumps(
                {
                    "product": supply.product,
                    "on_hand": supply.on_hand,
                    "earliest_completion": supply.earliest_completion,
                    "note": supply.note,
                }
            ),
            after_json=dumps({}),
            operator="演示用户",
            note="从仓库删除未被订单使用的商品",
            created_at=deleted_at,
        )
    )
    db.delete(supply)
    db.flush()


def _usage_message(db: Session, product: str, orders: list[Order], action: str) -> str:
    shown = []
    for order in orders[:6]:
        customer = db.get(Customer, order.customer_id)
        who = customer.name if customer else "未知客户"
        state = "回收站" if order.deleted_at else order.status
        shown.append(f"{order.order_no} {who}（{state}）")
    listed = "、".join(shown)
    if len(orders) > 6:
        listed = f"{listed}，等 {len(orders)} 张"
    return f"还有订单在用「{product}」：{listed}，不能{action}。"


def _require_supply_values(on_hand: int, earliest: str) -> str:
    if on_hand < 0:
        raise HTTPException(status_code=400, detail="在手数量不能为负数")
    parsed = parse_date(earliest)
    if parsed is None:
        raise HTTPException(status_code=400, detail="最早可完工日期格式应为 YYYY-MM-DD")
    return parsed

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BusinessEvent, Customer, CustomerIntake, Order, Source
from app.services.additional import decode_additional
from app.util import loads

EVENT_LABELS = {
    "quantity_change": "数量变更",
    "delivery_change": "交期变更",
    "commitment": "承诺",
    "cancellation": "取消",
    "owner_unclear": "责任人不明",
    "inventory_fact": "库存事实",
    "production_constraint": "生产约束",
    "partial_delivery": "部分交付",
    "additional_order": "另一商品",
    "offline_meeting": "线下洽谈",
    "clarification": "待澄清",
    "repeat_spec": "沿用上次规格",
}

FIELD_PHRASE = {
    "quantity": "订单数量",
    "delivery_date": "订单交期",
    "available_qty": "在手数量",
    "earliest_completion": "最早可完工日期",
    "delivered_qty": "已交付数量",
    "status": "订单状态",
}


def store_source_summary(
    db: Session,
    source: Source,
    order: Order,
    events: list,
    risks: list,
    *,
    on_hand: int | None,
    earliest: str,
) -> None:
    customer = db.get(Customer, order.customer_id)
    source.summary_text = render_order_import(
        customer=customer.name if customer else "",
        product=order.product,
        quantity=order.quantity,
        delivery_date=order.delivery_date,
        on_hand=on_hand,
        earliest=earliest,
        events=events,
        risks=risks,
    )


def render_order_import(
    *,
    customer: str,
    product: str,
    quantity: int,
    delivery_date: str,
    on_hand: int | None,
    earliest: str,
    events: list,
    risks: list,
) -> str:
    lines = [
        f"客户：{customer or '未填写'}",
        f"商品：{product or '未填写'}",
        f"订单数量：{quantity} 件",
        f"订单交期：{delivery_date or '未填写'}",
    ]
    if on_hand is not None:
        lines.append(f"在手数量：{on_hand} 件")
    if earliest:
        lines.append(f"最早可完工日期：{earliest}")
    lines.append("")
    lines.append("处理摘要：")
    body = [_event_line(event) for event in events]
    body.extend(f"冲突：{risk.message}" for risk in risks if getattr(risk, "message", ""))
    body = [line for line in body if line]
    if not body:
        body.append("这次导入没有识别出需要确认的变更。")
    lines.extend(body)
    return "\n".join(lines).strip()


def render_customer_import(customer: str, events: list, suggestions: list[dict]) -> str:
    lines = [
        f"客户：{customer or '未填写'}",
        "这次导入没有挂在具体订单上。",
        "",
        "处理摘要：",
    ]
    body = [_event_line(event) for event in events]
    for item in suggestions:
        title = (item.get("title") or "待办").strip()
        reason = _narrative(item.get("reason") or "")
        line = f"待办：{title}"
        if reason:
            line = f"{line}。{reason}"
        body.append(line)
    body = [line for line in body if line]
    if not body:
        body.append("没有识别出需要确认的事项。")
    lines.extend(body)
    return "\n".join(lines).strip()


def list_import_history(db: Session, order: Order, customer_name: str) -> list[dict]:
    items: list[dict] = []
    sources = db.scalars(select(Source).where(Source.order_id == order.id)).all()
    for source in sources:
        summary = (source.summary_text or "").strip()
        if not summary:
            events = db.scalars(
                select(BusinessEvent).where(BusinessEvent.source_id == source.id).order_by(BusinessEvent.id)
            ).all()
            summary = render_order_import(
                customer=customer_name,
                product=order.product,
                quantity=order.quantity,
                delivery_date=order.delivery_date,
                on_hand=None,
                earliest="",
                events=list(events),
                risks=[],
            )
        items.append(
            {
                "id": source.id,
                "scope": "order",
                "title": source.title or "",
                "created_at": source.created_at,
                "summary": summary,
            }
        )
    intakes = db.scalars(select(CustomerIntake).where(CustomerIntake.customer_id == order.customer_id)).all()
    for record in intakes:
        summary = (record.summary_text or "").strip() or _summary_from_intake(record, customer_name)
        items.append(
            {
                "id": record.id,
                "scope": "customer",
                "title": record.title or "客户沟通",
                "created_at": record.created_at,
                "summary": summary,
            }
        )
    items.sort(key=lambda item: (item["created_at"] or "", item["id"]), reverse=True)
    return items


def _summary_from_intake(record: CustomerIntake, customer_name: str) -> str:
    payload = loads(record.result_json, {})
    events = payload.get("events") or []
    suggestions = payload.get("task_suggestions") or []
    return render_customer_import(customer_name, events, suggestions)


def _event_line(event) -> str:
    event_type = getattr(event, "event_type", None) or event.get("event_type", "")
    label = EVENT_LABELS.get(event_type, event_type or "导入")
    explanation = _narrative(_value(event, "explanation"))
    if event_type == "additional_order":
        spec = decode_additional(_value(event, "proposed_value"))
        if spec:
            bits = [f"商品 {spec['product']}", f"订单数量 {spec['quantity']} 件"]
            if spec["delivery_date"]:
                bits.append(f"要求交期 {spec['delivery_date']}")
            return _join(label, ["，".join(bits), explanation])
        return _join(label, [explanation])
    field = _value(event, "field_name")
    proposed = _value(event, "proposed_value")
    change = ""
    if field and proposed:
        if event_type == "delivery_change":
            phrase = "要求交期"
        else:
            phrase = FIELD_PHRASE.get(field, "")
        if phrase:
            current = _value(event, "current_value")
            change = f"{phrase} {current} → {proposed}" if current else f"{phrase} {proposed}"
    return _join(label, [change, explanation])


def _value(event, name: str) -> str:
    if isinstance(event, dict):
        raw = event.get(name)
    else:
        raw = getattr(event, name, None)
    if raw is None:
        return ""
    return str(raw).strip()


def _narrative(text: str) -> str:
    cleaned = (text or "").strip()
    marker = "依据："
    if marker in cleaned:
        cleaned = cleaned.split(marker, 1)[0].strip()
    return cleaned


def _join(label: str, parts: list[str]) -> str:
    body = "。".join(part.strip("。") for part in parts if part and part.strip())
    if not body:
        return label
    return f"{label}：{body}。"

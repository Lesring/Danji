from datetime import date

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.client import ExtractError, complete_json, parse_model_json
from app.models import (
    BusinessEvent,
    ExtractionLog,
    Order,
    Risk,
    Source,
    Task,
    TaskSuggestion,
    now_iso,
)
from app.services.additional import decode_additional, encode_additional
from app.services.fixture_extract import extract_fixture_events, merge_cooperation
from app.services.import_summary import store_source_summary
from app.services.ingest import locate_cell
from app.services.rules import Analysis, EventIn, OpenTask, OrderSnap, analyze
from app.services.supply import customer_open_order, get_product_supply, orders_for_product, outstanding_qty, supply_view
from app.util import dumps, loads, parse_date, parse_int

EVENT_TYPES = {
    "quantity_change",
    "delivery_change",
    "commitment",
    "cancellation",
    "owner_unclear",
    "inventory_fact",
    "production_constraint",
    "partial_delivery",
    "additional_order",
    "offline_meeting",
    "clarification",
    "repeat_spec",
}
ORIGINS = {"客户期望", "销售承诺", "生产约束", "库存事实", "内部沟通", ""}


class LlmEvent(BaseModel):
    event_type: str
    field_name: str | None = None
    current_value: str | None = None
    proposed_value: str | None = None
    proposal_origin: str = ""
    evidence_excerpt: str = ""
    confidence: float = 0.5
    explanation: str = ""
    needs_review: bool = True
    order_no: str | None = None
    suggestion_key: str | None = None


class LlmAdditional(BaseModel):
    product: str = ""
    quantity: str | int | None = None
    delivery_date: str | None = None
    on_hand: str | int | None = None
    earliest_completion: str | None = None
    evidence_excerpt: str = ""
    explanation: str = ""
    confidence: float = 0.5


class LlmPayload(BaseModel):
    events: list[LlmEvent] = Field(default_factory=list)
    additional_products: list[LlmAdditional] = Field(default_factory=list)


def _customer_history(db: Session, customer_id: int) -> list[dict]:
    rows = db.scalars(
        select(Order).where(Order.customer_id == customer_id, Order.deleted_at.is_(None)).order_by(Order.id)
    )
    return [
        {
            "order_no": item.order_no,
            "product": item.product,
            "quantity": item.quantity,
            "delivered_qty": item.delivered_qty or 0,
            "delivery_date": item.delivery_date,
        }
        for item in rows
    ]


def excerpt_in_source(excerpt: str, raw: str) -> bool:
    if not excerpt:
        return False

    def norm(value: str) -> str:
        return "".join(value.split())

    return norm(excerpt) in norm(raw)


def run_extraction(db: Session, source: Source) -> dict:
    order = db.get(Order, source.order_id)
    if order is None:
        raise ExtractError("订单不存在", status_code=404)
    view = supply_view(db, order)
    if view is None:
        raise ExtractError("订单缺少供应记录", status_code=400)

    open_tasks = list(
        db.scalars(select(Task).where(Task.order_id == order.id, Task.status.notin_(["已完成", "已关闭"])))
    )
    existing_events = list(
        db.scalars(
            select(BusinessEvent)
            .where(BusinessEvent.order_id == order.id, BusinessEvent.status != "rejected")
            .order_by(BusinessEvent.id)
        )
    )
    snapshot = {
        "order_no": order.order_no,
        "product": order.product,
        "quantity": order.quantity,
        "delivered_qty": order.delivered_qty or 0,
        "outstanding_qty": outstanding_qty(order),
        "delivery_date": order.delivery_date,
        "status": order.status,
        "owner": order.owner,
        "on_hand": view["on_hand"],
        "reserved": view["reserved"],
        "committable": view["committable"],
        "available_qty": view["available_qty"],
        "earliest_completion": view["earliest_completion"],
        "open_tasks": [
            {"id": task.id, "title": task.title, "owner": task.owner, "status": task.status, "key": task.suggestion_key}
            for task in open_tasks
        ],
        "customer_orders": _customer_history(db, order.customer_id),
        "unconfirmed_events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "field_name": event.field_name,
                "proposed_value": event.proposed_value,
                "proposal_origin": event.proposal_origin,
                "status": event.status,
            }
            for event in existing_events
            if event.status == "draft"
        ],
    }
    settings = get_settings()
    mode = settings.extract_mode
    history = snapshot["customer_orders"]
    if mode == "llm":
        raw_output = complete_json(snapshot, source.raw_text)
        payload = parse_model_json(raw_output)
        new_events = merge_cooperation(_events_from_llm(payload, source.raw_text, order), source.raw_text, history)
    elif mode == "fixture":
        new_events = extract_fixture_events(
            source.raw_text,
            order.quantity,
            order.delivery_date,
            view["on_hand"],
            view["earliest_completion"],
            delivered_qty=order.delivered_qty or 0,
            customer_orders=history,
        )
        new_events = _drop_invalid_excerpts(new_events, source.raw_text)
        raw_output = dumps([_event_dict(event) for event in new_events])
    else:
        raise ExtractError("EXTRACT_MODE 只能是 fixture 或 llm。来源已保留。")

    new_events = _drop_same_value(new_events)
    new_events, foreign_events = _route_additional(db, order, new_events)
    for event in new_events:
        if event.order_no and event.order_no != order.order_no:
            event.needs_review = True
            event.explanation = f"订单号 {event.order_no} 与当前订单 {order.order_no} 不一致，未新建订单。" + event.explanation
            event.confidence = min(event.confidence, 0.4)

    saved_new: list[BusinessEvent] = []
    for event in new_events:
        saved_new.append(_persist_event(db, order.id, source, event))
    touched: dict[int, Order] = {}
    for target, event in foreign_events:
        _persist_event(db, target.id, source, event)
        touched[target.id] = target
    db.flush()

    combined = [_from_model(event) for event in existing_events] + [_from_model(event) for event in saved_new]
    analysis = analyze(
        OrderSnap(
            quantity=outstanding_qty(order),
            delivery_date=order.delivery_date,
            status=order.status,
            available_qty=view["available_qty"],
            earliest_completion=view["earliest_completion"],
            track_commitment=view["reserves"],
        ),
        combined,
        [
            OpenTask(
                id=task.id,
                suggestion_key=task.suggestion_key,
                title=task.title,
                status=task.status,
                owner=task.owner,
            )
            for task in open_tasks
        ],
        today=date.today().isoformat(),
    )
    _apply_conflict_notes(existing_events + saved_new, analysis)
    _replace_risks(db, order.id, source.id, existing_events + saved_new, analysis)
    _upsert_suggestions(db, order.id, source.id, existing_events + saved_new, analysis)
    for target in touched.values():
        refresh_order(db, target, source.id)
    store_source_summary(
        db,
        source,
        order,
        list(saved_new) + [event for _target, event in foreign_events],
        [risk for risk in analysis.risks if any(index >= len(existing_events) for index in risk.event_refs)],
        on_hand=view["on_hand"],
        earliest=view["earliest_completion"],
    )
    db.add(
        ExtractionLog(
            source_id=source.id,
            mode=mode,
            snapshot_json=dumps(snapshot),
            raw_output=raw_output,
            created_at=now_iso(),
        )
    )
    db.flush()
    return {"source_id": source.id}


def _events_from_llm(payload: dict, raw_text: str, order: Order) -> list[EventIn]:
    try:
        parsed = LlmPayload.model_validate(payload)
    except ValidationError as exc:
        raise ExtractError("模型返回的事件结构无法校验。来源已保留。", status_code=502) from exc
    events: list[EventIn] = []
    seen_products: set[str] = set()
    for item in parsed.events:
        if item.event_type == "additional_order":
            extra = _additional_from_llm_event(item)
            if extra is None or not excerpt_in_source(extra.evidence_excerpt, raw_text):
                continue
            product = decode_additional(extra.proposed_value)
            if product and product["product"] not in seen_products:
                seen_products.add(product["product"])
                events.append(extra)
            continue
        if item.event_type not in EVENT_TYPES:
            continue
        if item.proposal_origin not in ORIGINS:
            item.proposal_origin = ""
            item.needs_review = True
        if not excerpt_in_source(item.evidence_excerpt, raw_text):
            continue
        proposed = item.proposed_value
        if item.field_name in ("delivery_date", "earliest_completion") and proposed:
            proposed = parse_date(proposed) or proposed
        current = item.current_value
        if item.event_type == "partial_delivery":
            if proposed and "一半" in str(proposed):
                proposed = "一半"
            if not current:
                current = str(order.delivered_qty or 0)
        if item.field_name == "quantity" and not current:
            current = str(order.quantity)
        if item.field_name == "delivery_date" and not current:
            current = order.delivery_date
        events.append(
            EventIn(
                event_type=item.event_type,
                field_name=item.field_name or None,
                current_value=current,
                proposed_value=proposed,
                proposal_origin=item.proposal_origin,
                evidence_excerpt=item.evidence_excerpt,
                explanation=item.explanation,
                confidence=item.confidence,
                needs_review=item.needs_review,
                suggestion_key=item.suggestion_key,
                order_no=None if item.event_type == "partial_delivery" else (item.order_no or None),
            )
        )
    for item in parsed.additional_products:
        extra = _additional_from_parts(
            item.product,
            item.quantity,
            item.delivery_date,
            item.on_hand,
            item.earliest_completion,
            item.evidence_excerpt,
            item.explanation,
            item.confidence,
        )
        if extra is None or not excerpt_in_source(extra.evidence_excerpt, raw_text):
            continue
        product = decode_additional(extra.proposed_value)
        if product is None or product["product"] in seen_products:
            continue
        seen_products.add(product["product"])
        events.append(extra)
    return events


def _optional_hand(value: str | int | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    parsed = parse_int(str(value))
    if parsed is None or parsed < 0:
        return None
    return parsed


def _additional_from_parts(
    product: str,
    quantity: str | int | None,
    delivery_date: str | None,
    on_hand: str | int | None,
    earliest: str | None,
    excerpt: str,
    explanation: str,
    confidence: float,
) -> EventIn | None:
    name = (product or "").strip()
    qty = parse_int(str(quantity)) if quantity is not None and str(quantity).strip() else None
    if not name or qty is None or qty <= 0:
        return None
    delivery = parse_date(delivery_date) if delivery_date else ""
    early = parse_date(earliest) if earliest else ""
    hand = _optional_hand(on_hand)
    return EventIn(
        event_type="additional_order",
        proposed_value=encode_additional(name, qty, delivery or "", hand, early or ""),
        proposal_origin="客户期望",
        evidence_excerpt=excerpt,
        explanation=explanation or f"建议为该客户新增商品订单：{name} {qty} 件。确认前不创建订单，订单号由系统生成。",
        confidence=confidence,
        needs_review=True,
    )


def _additional_from_llm_event(item: LlmEvent) -> EventIn | None:
    spec = decode_additional(item.proposed_value)
    if spec is not None:
        return EventIn(
            event_type="additional_order",
            proposed_value=encode_additional(
                spec["product"],
                spec["quantity"],
                spec["delivery_date"],
                spec["on_hand"],
                spec["earliest_completion"],
            ),
            proposal_origin=item.proposal_origin if item.proposal_origin in ORIGINS else "客户期望",
            evidence_excerpt=item.evidence_excerpt,
            explanation=item.explanation or f"建议为该客户新增商品订单：{spec['product']} {spec['quantity']} 件。",
            confidence=item.confidence,
            needs_review=True,
        )
    return _additional_from_parts(
        item.field_name or "",
        item.proposed_value,
        None,
        None,
        None,
        item.evidence_excerpt,
        item.explanation,
        item.confidence,
    )


def _same_product(left: str, right: str) -> bool:
    return bool((left or "").strip()) and (left or "").strip() == (right or "").strip()


def _linked_changes(order: Order, spec: dict, evidence: str) -> list[EventIn]:
    events: list[EventIn] = []
    if int(spec["quantity"]) != order.quantity:
        events.append(
            EventIn(
                event_type="quantity_change",
                field_name="quantity",
                current_value=str(order.quantity),
                proposed_value=str(int(spec["quantity"])),
                proposal_origin="客户期望",
                evidence_excerpt=evidence,
                explanation=(
                    f"同一客户已有商品「{order.product}」的未删除订单 {order.order_no}，"
                    "数量变更挂在这张订单上，确认前不新建。"
                ),
                confidence=0.9,
            )
        )
    delivery = spec.get("delivery_date") or ""
    if delivery and delivery != order.delivery_date:
        events.append(
            EventIn(
                event_type="delivery_change",
                field_name="delivery_date",
                current_value=order.delivery_date,
                proposed_value=delivery,
                proposal_origin="客户期望",
                evidence_excerpt=evidence,
                explanation=(
                    f"同一客户已有商品「{order.product}」的未删除订单 {order.order_no}，"
                    "交期变更挂在这张订单上，确认前不新建。"
                ),
                confidence=0.9,
            )
        )
    return events


def _route_additional(
    db: Session, order: Order, events: list[EventIn]
) -> tuple[list[EventIn], list[tuple[Order, EventIn]]]:
    local: list[EventIn] = []
    foreign: list[tuple[Order, EventIn]] = []
    for event in events:
        if event.event_type != "additional_order":
            local.append(event)
            continue
        spec = decode_additional(event.proposed_value)
        if spec is None:
            continue
        product = spec["product"]
        if _same_product(product, order.product):
            local.extend(_linked_changes(order, spec, event.evidence_excerpt))
            continue
        existing = customer_open_order(db, order.customer_id, product)
        if existing is not None:
            foreign.extend((existing, item) for item in _linked_changes(existing, spec, event.evidence_excerpt))
            continue
        supply = get_product_supply(db, product)
        if supply is not None:
            extra = "该商品已有供应记录，确认后共用，不会用对话里的数字覆盖在手库存。"
        else:
            extra = (
                "确认后才会创建订单，订单号由系统生成。没有供应记录时，在手数量用对话里写明的库存，"
                "没说则记 0；最早可完工日期没说则用订单交期或今天，并标为待核实。"
            )
        event.explanation = f"{(event.explanation or '').rstrip()} {extra}".strip()
        event.order_no = None
        event.needs_review = True
        local.append(event)
    return local, foreign


def _persist_event(db: Session, order_id: int, source: Source, event: EventIn) -> BusinessEvent:
    row, col = locate_cell(source.raw_text, event.evidence_excerpt)
    record = BusinessEvent(
        order_id=order_id,
        source_id=source.id,
        event_type=event.event_type,
        field_name=event.field_name,
        current_value=event.current_value,
        proposed_value=event.proposed_value,
        proposal_origin=event.proposal_origin,
        evidence_excerpt=event.evidence_excerpt,
        evidence_row=row,
        evidence_col=col,
        status="draft",
        confidence=event.confidence,
        explanation=event.explanation,
        needs_review=event.needs_review,
        suggestion_key=event.suggestion_key,
        created_at=now_iso(),
    )
    db.add(record)
    return record


def _drop_invalid_excerpts(events: list[EventIn], raw_text: str) -> list[EventIn]:
    return [event for event in events if excerpt_in_source(event.evidence_excerpt, raw_text)]


def _drop_same_value(events: list[EventIn]) -> list[EventIn]:
    grouped: dict[str, set[str]] = {}
    for event in events:
        if event.field_name and event.proposed_value:
            grouped.setdefault(event.field_name, set()).add(event.proposed_value.strip())
    kept: list[EventIn] = []
    for event in events:
        if (
            event.field_name in ("quantity", "delivery_date")
            and event.proposed_value
            and event.current_value
            and event.proposed_value.strip() == event.current_value.strip()
            and len(grouped.get(event.field_name, set())) <= 1
        ):
            continue
        kept.append(event)
    return kept


def _event_dict(event: EventIn) -> dict:
    return {
        "event_type": event.event_type,
        "field_name": event.field_name,
        "current_value": event.current_value,
        "proposed_value": event.proposed_value,
        "proposal_origin": event.proposal_origin,
        "evidence_excerpt": event.evidence_excerpt,
        "explanation": event.explanation,
        "suggestion_key": event.suggestion_key,
    }


def _from_model(event: BusinessEvent) -> EventIn:
    return EventIn(
        event_type=event.event_type,
        field_name=event.field_name,
        current_value=event.current_value,
        proposed_value=event.proposed_value,
        proposal_origin=event.proposal_origin,
        evidence_excerpt=event.evidence_excerpt,
        status=event.status,
        suggestion_key=event.suggestion_key,
        explanation=event.explanation,
        confidence=event.confidence,
        needs_review=event.needs_review,
    )


def _apply_conflict_notes(events: list[BusinessEvent], analysis: Analysis) -> None:
    for index, event in enumerate(events):
        note = analysis.conflict_notes.get(index, "")
        event.conflict_flag = bool(note)
        event.conflict_note = note


def _replace_risks(
    db: Session,
    order_id: int,
    source_id: int | None,
    events: list[BusinessEvent],
    analysis: Analysis,
) -> None:
    for risk in list(db.scalars(select(Risk).where(Risk.order_id == order_id))):
        db.delete(risk)
    db.flush()
    for risk in analysis.risks:
        refs = [events[index].id for index in risk.event_refs if index < len(events)]
        primary = events[risk.event_refs[0]] if risk.event_refs else None
        db.add(
            Risk(
                order_id=order_id,
                source_id=primary.source_id if primary else source_id,
                code=risk.code,
                message=risk.message,
                event_ids_json=dumps(refs),
                evidence_excerpt=risk.evidence_excerpt,
                created_at=now_iso(),
            )
        )


def _upsert_suggestions(
    db: Session,
    order_id: int,
    source_id: int,
    events: list[BusinessEvent],
    analysis: Analysis,
) -> None:
    existing = list(
        db.scalars(select(TaskSuggestion).where(TaskSuggestion.order_id == order_id, TaskSuggestion.status == "draft"))
    )
    by_key = {item.suggestion_key: item for item in existing}
    for suggestion in analysis.suggestions:
        refs = [events[index].id for index in suggestion.event_refs if index < len(events)]
        if not refs:
            refs = [event.id for event in events if event.source_id == source_id]
        current = by_key.get(suggestion.suggestion_key)
        if current is None:
            current = TaskSuggestion(
                order_id=order_id,
                suggestion_key=suggestion.suggestion_key,
                created_at=now_iso(),
            )
            db.add(current)
            by_key[suggestion.suggestion_key] = current
        current.source_id = source_id
        current.title = suggestion.title
        current.owner = suggestion.owner
        current.due_date = suggestion.due_date
        current.reason = suggestion.reason
        current.action = suggestion.action
        current.target_task_id = suggestion.target_task_id
        current.order_status = suggestion.order_status
        current.event_ids_json = dumps(refs)
        current.status = "draft"


def refresh_product(db: Session, product: str, source_id: int | None = None) -> None:
    for order in orders_for_product(db, product):
        refresh_order(db, order, source_id)


def refresh_order(db: Session, order: Order, source_id: int | None = None) -> None:
    view = supply_view(db, order)
    if view is None:
        return
    events = list(
        db.scalars(
            select(BusinessEvent)
            .where(BusinessEvent.order_id == order.id, BusinessEvent.status != "rejected")
            .order_by(BusinessEvent.id)
        )
    )
    open_tasks = list(
        db.scalars(select(Task).where(Task.order_id == order.id, Task.status.notin_(["已完成", "已关闭"])))
    )
    analysis = analyze(
        OrderSnap(
            quantity=outstanding_qty(order),
            delivery_date=order.delivery_date,
            status=order.status,
            available_qty=view["available_qty"],
            earliest_completion=view["earliest_completion"],
            track_commitment=view["reserves"],
        ),
        [_from_model(event) for event in events],
        [
            OpenTask(
                id=task.id,
                suggestion_key=task.suggestion_key,
                title=task.title,
                status=task.status,
                owner=task.owner,
            )
            for task in open_tasks
        ],
        today=date.today().isoformat(),
    )
    _apply_conflict_notes(events, analysis)
    fallback = source_id if source_id is not None else (events[-1].source_id if events else None)
    _replace_risks(db, order.id, fallback, events, analysis)
    _sync_refresh_suggestions(db, order.id, fallback, events, analysis, open_tasks)
    db.flush()


def _sync_refresh_suggestions(
    db: Session,
    order_id: int,
    source_id: int | None,
    events: list[BusinessEvent],
    analysis: Analysis,
    open_tasks: list[Task],
) -> None:
    drafts = list(
        db.scalars(select(TaskSuggestion).where(TaskSuggestion.order_id == order_id, TaskSuggestion.status == "draft"))
    )
    by_key = {item.suggestion_key: item for item in drafts}
    open_by_key = {task.suggestion_key: task for task in open_tasks}
    live: set[str] = set()
    for suggestion in analysis.suggestions:
        live.add(suggestion.suggestion_key)
        current = by_key.get(suggestion.suggestion_key)
        if current is None:
            task = open_by_key.get(suggestion.suggestion_key)
            if task and suggestion.title == task.title and suggestion.action in {"create", "update"}:
                continue
            current = TaskSuggestion(
                order_id=order_id,
                suggestion_key=suggestion.suggestion_key,
                created_at=now_iso(),
            )
            db.add(current)
            by_key[suggestion.suggestion_key] = current
        refs = [events[index].id for index in suggestion.event_refs if index < len(events)]
        if source_id is not None:
            current.source_id = source_id
        current.title = suggestion.title
        current.owner = suggestion.owner
        current.due_date = suggestion.due_date
        current.reason = suggestion.reason
        current.action = suggestion.action
        current.target_task_id = suggestion.target_task_id
        current.order_status = suggestion.order_status
        current.event_ids_json = dumps(refs)
        current.status = "draft"
    for item in drafts:
        if item.suggestion_key not in live:
            db.delete(item)


def linked_event_ids(raw: str | None) -> list[int]:
    value = loads(raw, [])
    return [int(item) for item in value]

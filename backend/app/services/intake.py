from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.client import ExtractError, complete_json, parse_model_json
from app.models import Customer, CustomerIntake, Order, Task, now_iso
from app.services.extract import _customer_history, _events_from_llm
from app.services.fixture_extract import extract_fixture_events, merge_cooperation
from app.services.import_summary import render_customer_import
from app.services.rules import EventIn, OrderSnap, analyze
from app.util import dumps, loads


class _BlankOrder:
    quantity = 0
    delivered_qty = 0
    delivery_date = ""
    order_no = ""
    product = ""


def preview_customer_message(db: Session, customer_id: int, text: str, title: str = "") -> dict:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    raw = (text or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="来源内容为空")
    history = _customer_history(db, customer.id)
    events = _read_events(raw, history, customer.name)
    suggestions = _suggestions(events)
    record = CustomerIntake(
        customer_id=customer.id,
        title=(title or "客户沟通").strip(),
        raw_text=raw,
        summary_text=render_customer_import(customer.name, events, suggestions),
        status="draft",
        result_json=dumps({"events": [_event_dict(item) for item in events], "task_suggestions": suggestions}),
        created_at=now_iso(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _body(record, customer.name)


def confirm_intake(db: Session, customer_id: int, intake_id: int) -> dict:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    record = db.get(CustomerIntake, intake_id)
    if record is None or record.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="这条待办草案不存在")
    if record.status != "draft":
        raise HTTPException(status_code=409, detail="这条草案已经处理过")
    payload = loads(record.result_json, {})
    before_orders = _order_count(db, customer.id)
    created = []
    for item in payload.get("task_suggestions") or []:
        task = Task(
            order_id=None,
            order_no="",
            suggestion_key=item.get("suggestion_key") or "",
            title=(item.get("title") or "待确认事项")[:200],
            owner=item.get("owner") or "销售",
            due_date=item.get("due_date"),
            status="待处理",
            reason=item.get("reason") or "",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        db.add(task)
        db.flush()
        created.append(task.id)
    record.status = "confirmed"
    db.commit()
    if _order_count(db, customer.id) != before_orders:
        raise HTTPException(status_code=500, detail="确认待办时不应新建订单")
    body = _body(record, customer.name)
    body["task_ids"] = created
    return body


def _read_events(text: str, history: list[dict], customer_name: str) -> list[EventIn]:
    settings = get_settings()
    if settings.extract_mode == "llm":
        snapshot = {
            "customer_name": customer_name,
            "order_no": "",
            "product": "",
            "quantity": None,
            "delivery_date": "",
            "customer_orders": history,
            "note": "没有当前订单。customer_orders 只含这一位客户。不要编造型号、数量或日期。",
        }
        raw_output = complete_json(snapshot, text)
        payload = parse_model_json(raw_output)
        return merge_cooperation(_events_from_llm(payload, text, _BlankOrder()), text, history)
    if settings.extract_mode != "fixture":
        raise ExtractError("EXTRACT_MODE 只能是 fixture 或 llm。")
    return extract_fixture_events(text, 0, "", 0, "", customer_orders=history)


def _suggestions(events: list[EventIn]) -> list[dict]:
    analysis = analyze(
        OrderSnap(
            quantity=0,
            delivery_date="",
            status="待确认",
            available_qty=10**9,
            earliest_completion="1900-01-01",
            track_commitment=False,
        ),
        events,
        [],
        today=date.today().isoformat(),
    )
    return [
        {
            "suggestion_key": item.suggestion_key,
            "title": item.title,
            "owner": item.owner,
            "due_date": item.due_date,
            "reason": item.reason,
            "status": "draft",
        }
        for item in analysis.suggestions
    ]


def _event_dict(event: EventIn) -> dict:
    return {
        "event_type": event.event_type,
        "field_name": event.field_name,
        "current_value": event.current_value,
        "proposed_value": event.proposed_value,
        "evidence_excerpt": event.evidence_excerpt,
        "explanation": event.explanation,
        "status": "draft",
        "needs_review": True,
    }


def _body(record: CustomerIntake, customer_name: str) -> dict:
    payload = loads(record.result_json, {})
    return {
        "id": record.id,
        "customer_id": record.customer_id,
        "customer_name": customer_name,
        "title": record.title,
        "status": record.status,
        "events": payload.get("events") or [],
        "task_suggestions": payload.get("task_suggestions") or [],
    }


def _order_count(db: Session, customer_id: int) -> int:
    return len(
        list(db.scalars(select(Order.id).where(Order.customer_id == customer_id, Order.deleted_at.is_(None))))
    )

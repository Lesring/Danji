import json

from app.models import AuditLog, BusinessEvent, Customer, Order, Risk, Source, Task, TaskSuggestion
from app.services.extract import linked_event_ids
from app.util import loads


def customer_dict(customer: Customer, order_count: int) -> dict:
    return {
        "id": customer.id,
        "name": customer.name,
        "contact": customer.contact,
        "note": customer.note,
        "order_count": order_count,
    }


def order_dict(order: Order, customer_name: str, pending_count: int) -> dict:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "customer_id": order.customer_id,
        "customer_name": customer_name,
        "product": order.product,
        "quantity": order.quantity,
        "delivered_qty": order.delivered_qty or 0,
        "delivery_date": order.delivery_date,
        "status": order.status,
        "owner": order.owner,
        "pending_count": pending_count,
        "deleted_at": order.deleted_at,
    }


def supply_dict(supply: dict | None) -> dict | None:
    if supply is None:
        return None
    return {
        "product": supply["product"],
        "on_hand": supply["on_hand"],
        "reserved": supply["reserved"],
        "committable": supply["committable"],
        "available_qty": supply["available_qty"],
        "earliest_completion": supply["earliest_completion"],
        "note": supply["note"],
        "updated_at": supply.get("updated_at"),
        "updated_by": supply.get("updated_by"),
    }


def event_dict(event: BusinessEvent, source: Source | None) -> dict:
    return {
        "id": event.id,
        "kind": "event",
        "order_id": event.order_id,
        "source_id": event.source_id,
        "source_title": source.title if source else "",
        "source_type": source.type if source else "",
        "business_time": source.business_time if source else None,
        "event_type": event.event_type,
        "field_name": event.field_name,
        "current_value": event.current_value,
        "proposed_value": event.proposed_value,
        "proposal_origin": event.proposal_origin,
        "evidence_excerpt": event.evidence_excerpt,
        "evidence_row": event.evidence_row,
        "evidence_col": event.evidence_col,
        "status": event.status,
        "confidence": event.confidence,
        "explanation": event.explanation,
        "conflict_flag": event.conflict_flag,
        "conflict_note": event.conflict_note,
        "needs_review": event.needs_review,
        "created_at": event.created_at,
    }


def risk_dict(risk: Risk) -> dict:
    return {
        "id": risk.id,
        "order_id": risk.order_id,
        "source_id": risk.source_id,
        "code": risk.code,
        "message": risk.message,
        "event_ids": linked_event_ids(risk.event_ids_json),
        "evidence_excerpt": risk.evidence_excerpt,
        "created_at": risk.created_at,
    }


def suggestion_dict(suggestion: TaskSuggestion, source_id: int | None, excerpt: str) -> dict:
    return {
        "id": suggestion.id,
        "kind": "task",
        "order_id": suggestion.order_id,
        "source_id": source_id or suggestion.source_id,
        "suggestion_key": suggestion.suggestion_key,
        "title": suggestion.title,
        "owner": suggestion.owner,
        "due_date": suggestion.due_date,
        "reason": suggestion.reason,
        "action": suggestion.action,
        "target_task_id": suggestion.target_task_id,
        "order_status": suggestion.order_status,
        "event_ids": linked_event_ids(suggestion.event_ids_json),
        "status": suggestion.status,
        "evidence_excerpt": excerpt,
        "created_at": suggestion.created_at,
    }


def task_dict(task: Task, order_no: str, source_id: int | None, excerpt: str) -> dict:
    return {
        "id": task.id,
        "order_id": task.order_id,
        "order_no": order_no,
        "suggestion_key": task.suggestion_key,
        "title": task.title,
        "owner": task.owner,
        "due_date": task.due_date,
        "status": task.status,
        "reason": task.reason,
        "event_ids": linked_event_ids(task.event_ids_json),
        "kind": "task",
        "suggestion_id": task.suggestion_id,
        "action": "",
        "source_id": source_id or task.source_id,
        "evidence_excerpt": excerpt,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def board_suggestion_dict(suggestion: TaskSuggestion, order_no: str, source_id: int | None, excerpt: str) -> dict:
    return {
        "id": suggestion.id,
        "kind": "suggestion",
        "suggestion_id": suggestion.id,
        "order_id": suggestion.order_id,
        "order_no": order_no,
        "suggestion_key": suggestion.suggestion_key,
        "title": suggestion.title,
        "owner": suggestion.owner,
        "due_date": suggestion.due_date,
        "status": "待确认",
        "reason": suggestion.reason,
        "action": suggestion.action,
        "event_ids": linked_event_ids(suggestion.event_ids_json),
        "source_id": source_id or suggestion.source_id,
        "evidence_excerpt": excerpt,
        "created_at": suggestion.created_at,
        "updated_at": suggestion.created_at,
    }


def audit_dict(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "object_type": row.object_type,
        "object_id": row.object_id,
        "action": row.action,
        "before": _json_or_text(row.before_json),
        "after": _json_or_text(row.after_json),
        "operator": row.operator,
        "note": row.note,
        "created_at": row.created_at,
    }


def source_dict(source: Source) -> dict:
    return {
        "id": source.id,
        "order_id": source.order_id,
        "type": source.type,
        "title": source.title,
        "raw_text": source.raw_text,
        "table": loads(source.parsed_table_json, None),
        "business_time": source.business_time,
        "file_name": source.file_name,
        "created_at": source.created_at,
    }


def _json_or_text(raw: str):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw

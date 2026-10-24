from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BusinessEvent, Order, Risk, Source, Task, TaskSuggestion
from app.present import board_suggestion_dict, event_dict, risk_dict, suggestion_dict, task_dict
from app.services.apply import first_evidence
from app.services.extract import linked_event_ids


def order_drafts(db: Session, order_id: int) -> dict:
    events = db.scalars(
        select(BusinessEvent)
        .where(BusinessEvent.order_id == order_id, BusinessEvent.status == "draft")
        .order_by(BusinessEvent.id)
    ).all()
    sources = _sources_for(db, [event.source_id for event in events])
    risks = db.scalars(select(Risk).where(Risk.order_id == order_id).order_by(Risk.id)).all()
    suggestions = db.scalars(
        select(TaskSuggestion)
        .where(TaskSuggestion.order_id == order_id, TaskSuggestion.status == "draft")
        .order_by(TaskSuggestion.id)
    ).all()
    return {
        "events": [event_dict(event, sources.get(event.source_id)) for event in events],
        "risks": [risk_dict(risk) for risk in risks],
        "task_suggestions": [_suggestion(db, item) for item in suggestions],
    }


def task_rows(db: Session, order_id: int | None, status: str | None) -> list[dict]:
    rows = []
    if status != "待确认":
        stmt = select(Task).order_by(Task.id.desc())
        if order_id is not None:
            stmt = stmt.where(Task.order_id == order_id)
        if status:
            stmt = stmt.where(Task.status == status)
        for task in db.scalars(stmt):
            if task.order_id is None:
                rows.append(task_dict(task, task.order_no, None, ""))
                continue
            order = db.get(Order, task.order_id)
            if order is None or order.deleted_at:
                continue
            source_id, excerpt = first_evidence(db, linked_event_ids(task.event_ids_json))
            rows.append(task_dict(task, order.order_no, source_id, excerpt))
    if status in (None, "", "待确认"):
        stmt = (
            select(TaskSuggestion)
            .where(TaskSuggestion.status == "draft")
            .order_by(TaskSuggestion.id.desc())
        )
        if order_id is not None:
            stmt = stmt.where(TaskSuggestion.order_id == order_id)
        for suggestion in db.scalars(stmt):
            order = db.get(Order, suggestion.order_id)
            if order is None or order.deleted_at:
                continue
            source_id, excerpt = first_evidence(db, linked_event_ids(suggestion.event_ids_json))
            rows.append(board_suggestion_dict(suggestion, order.order_no, source_id, excerpt))
    return rows


def _suggestion(db: Session, suggestion: TaskSuggestion) -> dict:
    source_id, excerpt = first_evidence(db, linked_event_ids(suggestion.event_ids_json))
    return suggestion_dict(suggestion, source_id, excerpt)


def _sources_for(db: Session, ids: list[int]) -> dict[int, Source]:
    if not ids:
        return {}
    return {item.id: item for item in db.scalars(select(Source).where(Source.id.in_(ids)))}

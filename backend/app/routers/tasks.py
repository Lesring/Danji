from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries import task_rows
from app.schemas import TaskPatch
from app.services.apply import update_task
from app.present import task_dict
from app.services.apply import first_evidence
from app.services.extract import linked_event_ids
from app.models import Order

router = APIRouter(tags=["tasks"])


@router.get("/tasks")
def list_tasks(order_id: int | None = None, status: str | None = None, db: Session = Depends(get_db)):
    return task_rows(db, order_id, status)


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, payload: TaskPatch, db: Session = Depends(get_db)):
    task = update_task(db, task_id, payload.model_dump())
    order = db.get(Order, task.order_id) if task.order_id else None
    source_id, excerpt = first_evidence(db, linked_event_ids(task.event_ids_json))
    return task_dict(task, order.order_no if order else task.order_no, source_id, excerpt)

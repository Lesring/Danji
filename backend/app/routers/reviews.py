from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Order
from app.queries import order_drafts
from app.schemas import ConfirmIn, RejectIn
from app.services.apply import confirm_draft, reject_draft

router = APIRouter(tags=["reviews"])


@router.get("/orders/{order_id}/drafts")
def get_drafts(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order_drafts(db, order_id)


@router.post("/drafts/{draft_id}/confirm")
def confirm(draft_id: int, payload: ConfirmIn, db: Session = Depends(get_db)):
    return confirm_draft(db, draft_id, payload.kind, payload.model_dump())


@router.post("/drafts/{draft_id}/reject")
def reject(draft_id: int, payload: RejectIn, db: Session = Depends(get_db)):
    return reject_draft(db, draft_id, payload.kind, payload.reason, payload.operator)

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.llm.client import ExtractError
from app.models import Customer
from app.present import customer_dict, order_dict
from app.schemas import ChatPreviewIn, CustomerIn, IntakeIn
from app.services.customer_profile import preview_customer_chat, preview_customer_file
from app.services.intake import confirm_intake, preview_customer_message
from app.services.records import add_customer, order_count

router = APIRouter(tags=["customers"])


@router.get("/customers")
def list_customers(db: Session = Depends(get_db)):
    rows = []
    for customer in db.scalars(select(Customer).order_by(Customer.id)):
        rows.append(customer_dict(customer, order_count(db, customer.id)))
    return rows


@router.post("/customers/preview")
def preview_customer(payload: ChatPreviewIn):
    try:
        return preview_customer_chat(payload.text)
    except ExtractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/customers/preview/file")
async def preview_customer_from_file(file: UploadFile = File(...)):
    data = await file.read()
    try:
        return preview_customer_file(file.filename or "", data)
    except ExtractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/customers")
def create_customer(payload: CustomerIn, db: Session = Depends(get_db)):
    customer, order = add_customer(db, payload)
    db.commit()
    body = {"customer": customer_dict(customer, order_count(db, customer.id))}
    if order is not None:
        body["order"] = order_dict(order, customer.name, 0)
    return body


@router.post("/customers/{customer_id}/intake")
def create_intake(customer_id: int, payload: IntakeIn, db: Session = Depends(get_db)):
    try:
        return preview_customer_message(db, customer_id, payload.text, payload.title)
    except ExtractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/customers/{customer_id}/intake/{intake_id}/confirm")
def confirm_customer_intake(customer_id: int, intake_id: int, db: Session = Depends(get_db)):
    return confirm_intake(db, customer_id, intake_id)

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.llm.client import ExtractError
from app.schemas import AssistantTurnIn
from app.services.assistant import assistant_turn

router = APIRouter(tags=["assistant"])


@router.post("/assistant/turn")
def turn(payload: AssistantTurnIn, db: Session = Depends(get_db)):
    try:
        return assistant_turn(db, payload)
    except ExtractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

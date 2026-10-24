from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.llm.client import ExtractError
from app.schemas import AssistantTurnIn
from app.services.assistant import assistant_turn, parse_assistant_file

router = APIRouter(tags=["assistant"])


@router.post("/assistant/parse-file")
async def parse_file(file: UploadFile = File(...)):
    data = await file.read()
    try:
        return parse_assistant_file(file.filename or "upload", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/assistant/turn")
def turn(payload: AssistantTurnIn, db: Session = Depends(get_db)):
    try:
        return assistant_turn(db, payload)
    except ExtractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

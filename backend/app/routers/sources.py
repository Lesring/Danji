from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.llm.client import ExtractError
from app.models import Order, Source, now_iso
from app.present import source_dict
from app.queries import order_drafts
from app.schemas import SourceIn
from app.services.extract import run_extraction
from app.services.ingest import SOURCE_TYPES, parse_upload
from app.util import dumps

router = APIRouter(tags=["sources"])


class Incoming:
    def __init__(
        self,
        source_type: str,
        title: str,
        text: str,
        business_time: str | None,
        file_name: str | None,
        table: dict | None,
    ):
        self.type = source_type
        self.title = title
        self.text = text
        self.business_time = business_time
        self.file_name = file_name
        self.table = table


@router.post("/orders/{order_id}/sources")
async def create_source(order_id: int, request: Request, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.deleted_at:
        raise HTTPException(status_code=409, detail="订单在回收站中，恢复后才能继续接入沟通")
    payload = await _read_payload(request)
    if payload.type not in SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="来源类型不支持")
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="来源内容为空")
    source = Source(
        order_id=order.id,
        type=payload.type,
        title=payload.title.strip() or payload.file_name or "未命名来源",
        raw_text=payload.text,
        parsed_table_json=dumps(payload.table) if payload.table else "",
        business_time=payload.business_time or None,
        file_name=payload.file_name,
        created_at=now_iso(),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    source_id = source.id
    try:
        run_extraction(db, source)
        db.commit()
    except ExtractError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail={"message": exc.message, "source_id": source_id}) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail={"message": "解析失败，来源已保留，可稍后重试。", "source_id": source_id},
        ) from exc
    body = order_drafts(db, order.id)
    body["source_id"] = source_id
    return body


@router.get("/evidence/{source_id}")
def get_evidence(source_id: int, db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="找不到这条来源")
    return source_dict(source)


async def _read_payload(request: Request) -> Incoming:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="请求体需要是 UTF-8 JSON") from exc
        data = SourceIn.model_validate(payload)
        return Incoming(data.type, data.title, data.text, data.business_time, data.file_name, None)
    form = await request.form()
    source_type = str(form.get("type") or "chat")
    title = str(form.get("title") or "")
    text = str(form.get("text") or "")
    business_time = str(form.get("business_time") or "") or None
    upload = form.get("file")
    file_name = None
    table = None
    if upload is not None and hasattr(upload, "read"):
        file_name = getattr(upload, "filename", None) or "upload"
        data = await upload.read()
        try:
            parsed_text, table, forced_type = parse_upload(file_name, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="文件不是 UTF-8 文本") from exc
        text = parsed_text
        if forced_type:
            source_type = forced_type
        elif source_type == "file":
            source_type = "document"
    return Incoming(source_type, title, text, business_time, file_name, table)

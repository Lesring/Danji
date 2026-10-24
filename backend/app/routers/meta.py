from fastapi import APIRouter

from app.config import get_settings
from app.fixtures import load_demo

router = APIRouter(tags=["meta"])


@router.get("/health")
def health():
    settings = get_settings()
    return {
        "extract_mode": settings.extract_mode,
        "llm_configured": bool(settings.llm_api_key),
    }


@router.get("/demo/samples")
def samples(order_no: str | None = None):
    rows = load_demo()["samples"]
    if order_no:
        rows = [item for item in rows if item["order_no"] == order_no]
    return rows

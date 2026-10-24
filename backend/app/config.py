import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")


@dataclass
class Settings:
    extract_mode: str
    llm_api_base: str
    llm_api_key: str
    llm_model: str
    ocr_api_base: str
    ocr_api_key: str
    ocr_model: str
    asr_api_base: str
    asr_api_key: str
    asr_model: str
    fallback_api_base: str
    fallback_api_key: str
    fallback_model: str
    database_url: str


def get_settings() -> Settings:
    raw_db = os.getenv("DANJI_DATABASE", "").strip()
    if not raw_db:
        database_url = "sqlite:///" + (BACKEND_DIR / "data" / "danji.db").as_posix()
    elif raw_db.startswith("sqlite:"):
        database_url = raw_db
    else:
        database_url = "sqlite:///" + Path(raw_db).as_posix()
    mode = os.getenv("EXTRACT_MODE", "fixture").strip().lower() or "fixture"
    return Settings(
        extract_mode=mode,
        llm_api_base=os.getenv("LLM_API_BASE", "https://api.openai.com/v1").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini").strip(),
        ocr_api_base=os.getenv("OCR_API_BASE", "").strip(),
        ocr_api_key=os.getenv("OCR_API_KEY", "").strip(),
        ocr_model=os.getenv("OCR_MODEL", "").strip(),
        asr_api_base=os.getenv("ASR_API_BASE", "").strip(),
        asr_api_key=os.getenv("ASR_API_KEY", "").strip(),
        asr_model=os.getenv("ASR_MODEL", "").strip(),
        fallback_api_base=(
            os.getenv("ARK_FALLBACK_API_BASE", "").strip()
            or os.getenv("LLM_FALLBACK_API_BASE", "").strip()
            or "https://ark.cn-beijing.volces.com/api/v3"
        ),
        fallback_api_key=(
            os.getenv("ARK_FALLBACK_API_KEY", "").strip() or os.getenv("LLM_FALLBACK_API_KEY", "").strip()
        ),
        fallback_model=(
            os.getenv("ARK_FALLBACK_MODEL", "").strip()
            or os.getenv("LLM_FALLBACK_MODEL", "").strip()
            or "doubao-seed-2-1-turbo-260628"
        ),
        database_url=database_url,
    )

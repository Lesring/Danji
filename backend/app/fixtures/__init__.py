import json
import os
from pathlib import Path

_FIXTURES_DIR = Path(__file__).resolve().parent
_DEFAULT_FIXTURE = "demo.json"


def load_demo() -> dict:
    name = os.getenv("DANJI_FIXTURE", _DEFAULT_FIXTURE).strip() or _DEFAULT_FIXTURE
    path = _FIXTURES_DIR / name
    if not path.is_file():
        path = _FIXTURES_DIR / _DEFAULT_FIXTURE
    return json.loads(path.read_text(encoding="utf-8"))

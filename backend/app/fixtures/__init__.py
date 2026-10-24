import json
from pathlib import Path

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "demo.json"


def load_demo() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

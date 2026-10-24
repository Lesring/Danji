import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DANJI_DATABASE", str(tmp_path / "test.db"))
    monkeypatch.setenv("DANJI_FIXTURE", "demo_minimal.json")
    monkeypatch.setenv("EXTRACT_MODE", "fixture")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("OCR_API_KEY", "")
    monkeypatch.setenv("ASR_API_KEY", "")
    monkeypatch.setenv("DANJI_SEED_TASKS", "0")
    from app.db import reset_engine
    from app.main import create_app

    reset_engine()
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client

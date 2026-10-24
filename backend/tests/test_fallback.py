import httpx

from app.llm.client import ExtractError, complete_text
from app.services.media import ocr_image, transcribe_audio


def _response(status: int, body: dict | None = None):
    class Response:
        status_code = status

        def json(self):
            return body or {}

        text = "nope"

    return Response()


def test_primary_success_does_not_call_fallback(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "primary-key")
    monkeypatch.setenv("LLM_API_BASE", "http://primary.example/v1")
    monkeypatch.setenv("LLM_MODEL", "primary-model")
    called = {"fallback": 0}

    def post(url, **kwargs):
        return _response(200, {"choices": [{"message": {"content": "primary-ok"}}]})

    def fallback(*args, **kwargs):
        called["fallback"] += 1
        return "fallback-ok"

    monkeypatch.setattr("app.llm.client.httpx.post", post)
    monkeypatch.setattr("app.llm.client.chat_complete", fallback)
    assert complete_text("sys", "hi") == "primary-ok"
    assert called["fallback"] == 0


def test_client_error_does_not_call_fallback(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "primary-key")
    monkeypatch.setenv("LLM_API_BASE", "http://primary.example/v1")

    def post(url, **kwargs):
        return _response(400, {"error": {"message": "bad request"}})

    def fallback(*args, **kwargs):
        raise AssertionError("400 不应改走备用")

    monkeypatch.setattr("app.llm.client.httpx.post", post)
    monkeypatch.setattr("app.llm.client.chat_complete", fallback)
    try:
        complete_text("sys", "hi")
    except ExtractError as exc:
        assert exc.upstream_status == 400
    else:
        raise AssertionError("应当抛出主接口错误")


def test_fallback_client_does_not_retry(monkeypatch):
    monkeypatch.setenv("ARK_FALLBACK_API_KEY", "test-key")
    monkeypatch.setenv("ARK_FALLBACK_API_BASE", "https://example.invalid/v1")
    monkeypatch.setenv("ARK_FALLBACK_MODEL", "fallback-model")
    seen = {}

    class Completions:
        @staticmethod
        def create(**kwargs):
            seen["create"] = kwargs

            class Message:
                content = "ok"

            class Choice:
                message = Message()

            class Response:
                choices = [Choice()]

            return Response()

    class Chat:
        completions = Completions()

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.chat = Chat()

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    from app.llm.fallback import chat_complete

    assert chat_complete([{"role": "user", "content": "hi"}], timeout=12, temperature=0.1) == "ok"
    assert seen["client"]["max_retries"] == 0
    assert seen["client"]["timeout"] == 12
    assert seen["create"]["model"] == "fallback-model"


def test_timeout_uses_one_fallback(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "primary-key")
    monkeypatch.setenv("LLM_API_BASE", "http://primary.example/v1")
    monkeypatch.setenv("LLM_MODEL", "primary-model")
    seen = {}

    def post(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    def fallback(messages, *, timeout, temperature):
        seen["messages"] = messages
        seen["timeout"] = timeout
        return "fallback-ok"

    monkeypatch.setattr("app.llm.client.httpx.post", post)
    monkeypatch.setattr("app.llm.client.chat_complete", fallback)
    assert complete_text("sys", "用一句话介绍你自己") == "fallback-ok"
    assert seen["messages"][1]["content"] == "用一句话介绍你自己"


def test_image_primary_failure_sends_image_url_to_fallback(monkeypatch):
    monkeypatch.setenv("OCR_API_KEY", "primary-key")
    monkeypatch.setenv("OCR_API_BASE", "http://primary.example/v3")
    seen = {}

    def post(url, **kwargs):
        return _response(503, {"error": {"message": "unavailable"}})

    def fallback(messages, *, timeout, temperature):
        seen["content"] = messages[0]["content"]
        return "图中文字"

    monkeypatch.setattr("app.services.media.httpx.post", post)
    monkeypatch.setattr("app.services.media.chat_complete", fallback)
    assert ocr_image(b"png", "image/png") == "图中文字"
    image = next(part for part in seen["content"] if part["type"] == "image_url")
    assert image["image_url"]["url"].startswith("data:image/png;base64,")


def test_audio_fallback_rejection_is_surfaced(monkeypatch):
    monkeypatch.setenv("ASR_API_KEY", "primary-key")
    monkeypatch.setenv("ASR_API_BASE", "http://primary.example/v3")

    def post(url, **kwargs):
        raise httpx.ConnectError("timed out")

    def fallback(messages, *, timeout, temperature):
        part = next(item for item in messages[0]["content"] if item["type"] == "input_audio")
        assert part["input_audio"]["format"] == "wav"
        raise RuntimeError("model does not support audio")

    monkeypatch.setattr("app.services.media.httpx.post", post)
    monkeypatch.setattr("app.services.media.chat_complete", fallback)
    try:
        transcribe_audio(b"abc", "call.wav")
    except ValueError as exc:
        assert "备用接口失败" in str(exc)
        assert "does not support audio" in str(exc)
    else:
        raise AssertionError("录音备用失败时不能当成转写成功")

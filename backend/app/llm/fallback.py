"""主接口失败后的一次 OpenAI 兼容 chat.completions 备用调用。"""

import logging
import re

import httpx

from app.config import get_settings

logger = logging.getLogger("danji.llm")
_SECRET = re.compile(r"(sk-|aip_|ark-|Bearer\s+)\S+")


def public_error(exc: BaseException) -> str:
    text = _SECRET.sub("[redacted]", str(exc))
    return " ".join(text.split())[:500]


def is_transport(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def status_should_fallback(status: int | None) -> bool:
    if status is None:
        return False
    return status in {402, 429} or status >= 500


def chat_complete(messages: list, *, timeout: float, temperature: float) -> str:
    settings = get_settings()
    if not settings.fallback_api_key:
        raise RuntimeError("未配置 ARK_FALLBACK_API_KEY")
    from openai import OpenAI

    logger.info("path=fallback model=%s", settings.fallback_model)
    client = OpenAI(
        base_url=settings.fallback_api_base,
        api_key=settings.fallback_api_key,
        timeout=timeout,
        max_retries=0,
    )
    try:
        response = client.chat.completions.create(
            model=settings.fallback_model,
            temperature=temperature,
            messages=messages,
        )
    except Exception as exc:
        logger.info("path=fallback-failed model=%s error=%s", settings.fallback_model, public_error(exc))
        raise RuntimeError(public_error(exc)) from exc
    choice = response.choices[0].message.content if response.choices else ""
    text = (choice or "").strip()
    if not text:
        raise RuntimeError("备用接口没有返回文本")
    return text

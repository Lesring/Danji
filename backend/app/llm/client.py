import json
import logging
import re

import httpx

from app.config import get_settings
from app.llm.fallback import chat_complete, is_transport, public_error, status_should_fallback
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger("danji.llm")


class ExtractError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        *,
        upstream_status: int | None = None,
        transport: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.upstream_status = upstream_status
        self.transport = transport


def complete_json(snapshot: dict, source_text: str) -> str:
    return complete_text(
        SYSTEM_PROMPT,
        build_user_prompt(snapshot, source_text),
        missing_key="未配置 LLM_API_KEY。请在 backend/.env 中填写密钥，或把 EXTRACT_MODE 设为 fixture。来源已保留。",
        http_error="调用模型接口失败：{exc}。来源已保留。",
        status_error="模型接口返回 {code}。来源已保留，可检查 LLM_API_BASE 与模型名后重试。",
        empty_error="模型响应里没有可用的文本内容。来源已保留。",
    )


def complete_text(
    system: str,
    user: str,
    *,
    missing_key: str = "未配置 LLM_API_KEY。",
    http_error: str = "调用模型接口失败：{exc}",
    status_error: str = "模型接口返回 {code}。可检查 LLM_API_BASE 与模型名后重试。",
    empty_error: str = "模型响应里没有可用的文本内容。",
) -> str:
    settings = get_settings()
    if not settings.llm_api_key:
        raise ExtractError(missing_key, status_code=400)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        text = _post_chat(
            settings.llm_api_base,
            settings.llm_api_key,
            settings.llm_model,
            messages,
            timeout=60,
            temperature=0.1,
            http_error=http_error,
            status_error=status_error,
            empty_error=empty_error,
        )
    except ExtractError as exc:
        if not (exc.transport or status_should_fallback(exc.upstream_status)):
            raise
        logger.info(
            "path=primary-failed model=%s upstream=%s transport=%s",
            settings.llm_model,
            exc.upstream_status,
            exc.transport,
        )
        try:
            text = chat_complete(messages, timeout=300, temperature=0.1)
        except Exception as fallback_exc:
            raise ExtractError(
                f"{exc.message}；备用接口失败：{public_error(fallback_exc)}",
                status_code=502,
                upstream_status=exc.upstream_status,
                transport=exc.transport,
            ) from fallback_exc
        return text
    logger.info("path=primary model=%s", settings.llm_model)
    return text


def _post_chat(
    base: str,
    api_key: str,
    model: str,
    messages: list,
    *,
    timeout: float,
    temperature: float,
    http_error: str,
    status_error: str,
    empty_error: str,
) -> str:
    url = base.rstrip("/") + "/chat/completions"
    payload = {"model": model, "temperature": temperature, "messages": messages}
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise ExtractError(http_error.format(exc=exc), status_code=502, transport=is_transport(exc)) from exc
    if response.status_code >= 400:
        raise ExtractError(
            status_error.format(code=response.status_code),
            status_code=502,
            upstream_status=response.status_code,
        )
    body = response.json()
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ExtractError(empty_error, status_code=502) from exc


def parse_model_json(raw: str) -> dict:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractError("模型没有返回可解析的 JSON。来源已保留。", status_code=502) from exc
    if not isinstance(data, dict):
        raise ExtractError("模型 JSON 缺少 events 数组。来源已保留。", status_code=502)
    if data.get("events") is None and isinstance(data.get("additional_products"), list):
        data["events"] = []
    if not isinstance(data.get("events"), list):
        raise ExtractError("模型 JSON 缺少 events 数组。来源已保留。", status_code=502)
    if "additional_products" in data and not isinstance(data.get("additional_products"), list):
        raise ExtractError("additional_products 必须是数组。来源已保留。", status_code=502)
    return data

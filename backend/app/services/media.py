"""图片、文档和录音走火山方舟 Responses API。只在对应密钥已填写时发起请求。"""

import base64
import logging
import re
from pathlib import Path

import httpx

from app.config import get_settings
from app.llm.fallback import chat_complete, is_transport, public_error, status_should_fallback

logger = logging.getLogger("danji.media")

# 订单图、PDF 和通话转写经常超过 256 token。方舟把回答和思维链都算进 max_output_tokens。
MAX_OUTPUT_TOKENS = 4096
DEFAULT_MODEL = "doubao-seed-2-0-mini-260428"
_AUDIO_MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/x-m4a",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "flac": "audio/flac",
    "amr": "audio/amr",
}


def complete_instruction(instruction: str, user_text: str) -> str:
    """新建客户或订单的字段提炼走这套多模态接口，不走文本专用模型。"""
    body = f"{instruction.strip()}\n\n原文：\n{user_text}"
    return _call(
        "ocr",
        [{"type": "input_text", "text": body}],
        timeout=90,
        missing_key="未配置 OCR_API_KEY。请在 backend/.env 填写后再根据文本或文件新建。",
        call_error="多模态接口调用失败：{exc}。可检查 OCR_API_BASE。",
        status_error="多模态接口返回 {code}。可检查 OCR_API_BASE、OCR_API_KEY 和 OCR_MODEL。",
        empty_error="多模态接口没有返回内容。",
        bad_body_error="多模态接口没有返回可用文本。",
    )


def complete_text(text: str) -> str:
    """同一套方舟客户端上的纯文本请求，供连通性检查使用。"""
    return _call(
        "ocr",
        [{"type": "input_text", "text": text}],
        timeout=60,
        missing_key=(
            "未配置 OCR_API_KEY。请在 backend/.env 填写后再调用图片或文档识别。"
        ),
        call_error="文字识别接口调用失败：{exc}。可检查 OCR_API_BASE。",
        status_error="文字识别接口返回 {code}。可检查 OCR_API_BASE、OCR_API_KEY 和 OCR_MODEL。",
        empty_error="文字识别没有返回内容。",
        bad_body_error="文字识别接口没有返回可用文本。",
    )


def ocr_image(data: bytes, mime: str) -> str:
    settings_key_error = (
        "未配置 OCR_API_KEY。请在 backend/.env 填写后再上传图片或扫描件。"
        "TXT、CSV、XLSX、DOCX 和带文字层的 PDF 不需要这个密钥。"
    )
    encoded = base64.b64encode(data).decode("ascii")
    return _call(
        "ocr",
        [
            {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"},
            {"type": "input_text", "text": "提取图片中的全部文字，保留换行。不要解释。"},
        ],
        timeout=60,
        missing_key=settings_key_error,
        call_error="文字识别接口调用失败：{exc}。可检查 OCR_API_BASE。",
        status_error="文字识别接口返回 {code}。可检查 OCR_API_BASE、OCR_API_KEY 和 OCR_MODEL。",
        empty_error="文字识别没有返回内容。",
        bad_body_error="文字识别接口没有返回可用文本。",
    )


def read_file(data: bytes, filename: str, instruction: str) -> str:
    """把本地 PDF 等文件以 input_file 送给方舟。带文字层的 PDF 仍由本地解析，不走这里。"""
    encoded = base64.b64encode(data).decode("ascii")
    name = Path(filename or "document.pdf").name or "document.pdf"
    mime = "application/pdf" if name.lower().endswith(".pdf") else "application/octet-stream"
    return _call(
        "ocr",
        [
            {
                "type": "input_file",
                "file_data": f"data:{mime};base64,{encoded}",
                "filename": name,
            },
            {"type": "input_text", "text": instruction},
        ],
        timeout=120,
        missing_key=(
            "未配置 OCR_API_KEY。请在 backend/.env 填写后再把 PDF 送给识别接口。"
        ),
        call_error="文件识别接口调用失败：{exc}。可检查 OCR_API_BASE。",
        status_error="文件识别接口返回 {code}。可检查 OCR_API_BASE、OCR_API_KEY 和 OCR_MODEL。",
        empty_error="文件识别没有返回内容。",
        bad_body_error="文件识别接口没有返回可用文本。",
    )


def transcribe_audio(data: bytes, filename: str) -> str:
    fmt = _audio_format(filename)
    mime = _AUDIO_MIME.get(fmt, "application/octet-stream")
    encoded = base64.b64encode(data).decode("ascii")
    return _call(
        "asr",
        [
            {"type": "input_audio", "audio_url": f"data:{mime};base64,{encoded}"},
            {"type": "input_text", "text": "逐字转写这段录音。能分辨说话人时请标明。不要解释，不要总结。"},
        ],
        timeout=180,
        missing_key=(
            "未配置 ASR_API_KEY。请在 backend/.env 填写后再上传录音。"
            "文本、表格和文档不需要这个密钥。"
        ),
        call_error="录音转写接口调用失败：{exc}。可检查 ASR_API_BASE。",
        status_error="录音转写接口返回 {code}。可检查 ASR_API_BASE、ASR_API_KEY 和 ASR_MODEL。",
        empty_error="录音转写没有返回内容。",
        bad_body_error="录音转写接口没有返回可用文本。",
    )


def _call(
    kind: str,
    content: list,
    *,
    timeout: float,
    missing_key: str,
    call_error: str,
    status_error: str,
    empty_error: str,
    bad_body_error: str,
) -> str:
    settings = get_settings()
    if kind == "asr":
        api_key = settings.asr_api_key
        base = settings.asr_api_base or settings.llm_api_base
        model = settings.asr_model or DEFAULT_MODEL
    else:
        api_key = settings.ocr_api_key
        base = settings.ocr_api_base or settings.llm_api_base
        model = settings.ocr_model or DEFAULT_MODEL
    if not api_key:
        raise ValueError(missing_key)
    payload = {
        "model": model,
        "temperature": 0.7,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": "minimal"},
        "input": [{"role": "user", "content": content}],
    }
    try:
        response = httpx.post(
            _responses_url(base),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        primary = ValueError(call_error.format(exc=exc))
        if is_transport(exc):
            return _fallback_media(kind, content, timeout, primary)
        raise primary from exc
    if response.status_code >= 400:
        detail = _error_detail(response)
        message = status_error.format(code=response.status_code)
        if detail:
            message = f"{message} {detail}"
        primary = ValueError(message)
        if status_should_fallback(response.status_code):
            return _fallback_media(kind, content, timeout, primary)
        raise primary
    try:
        text = _output_text(response.json())
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(bad_body_error) from exc
    if not text:
        raise ValueError(empty_error)
    logger.info("path=primary kind=%s model=%s", kind, model)
    return text


def _fallback_media(kind: str, content: list, timeout: float, primary: ValueError) -> str:
    logger.info("path=primary-failed kind=%s error=%s", kind, public_error(primary))
    try:
        messages = [{"role": "user", "content": _chat_parts(content)}]
        text = chat_complete(messages, timeout=timeout, temperature=0.7)
    except Exception as exc:
        raise ValueError(f"{primary}；备用接口失败：{public_error(exc)}") from exc
    if not text.strip():
        raise ValueError(f"{primary}；备用接口没有返回文本")
    return text


def _chat_parts(content: list) -> list:
    parts = []
    for block in content:
        kind = block.get("type")
        if kind == "input_text":
            parts.append({"type": "text", "text": block.get("text") or ""})
        elif kind == "input_image":
            url = block.get("image_url")
            if isinstance(url, dict):
                url = url.get("url")
            parts.append({"type": "image_url", "image_url": {"url": url}})
        elif kind == "input_audio":
            data, fmt = _audio_payload(str(block.get("audio_url") or ""))
            parts.append({"type": "input_audio", "input_audio": {"data": data, "format": fmt}})
        else:
            raise ValueError(f"备用接口不能承接 {kind}")
    return parts


def _audio_payload(url: str) -> tuple[str, str]:
    match = re.match(r"data:audio/([a-z0-9.+-]+);base64,(.+)", url, re.I)
    if not match:
        raise ValueError("备用接口不能解析录音地址")
    mime = match.group(1).lower()
    fmt = "m4a" if mime in {"x-m4a", "mp4", "m4a"} else mime.split("+")[-1]
    return match.group(2), fmt


def _responses_url(base: str) -> str:
    root = (base or "").rstrip("/")
    if root.endswith("/responses"):
        return root
    return root + "/responses"


def _audio_format(filename: str) -> str:
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext in _AUDIO_MIME:
        return ext
    return "wav"


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    message = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "")
        elif isinstance(error, str):
            message = error
    if not message:
        message = response.text[:300]
    return " ".join(message.split())[:300]


def _output_text(body: dict) -> str:
    if not isinstance(body, dict):
        raise ValueError("missing body")
    parts: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") == "reasoning":
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or ""
                if text and block.get("type") in (None, "output_text", "text"):
                    parts.append(str(text))
    if parts:
        return "\n".join(part.strip() for part in parts if str(part).strip()).strip()
    direct = body.get("output_text")
    if isinstance(direct, str):
        return direct.strip()
    error = body.get("error")
    if isinstance(error, dict) and error.get("message"):
        raise ValueError(str(error["message"]))
    return ""

"""从文本或文件提炼新建客户和订单的字段。原文没有的必填项留空，交给人工补。"""

import json
import re

from fastapi import HTTPException

from app.config import get_settings
from app.llm.client import ExtractError
from app.llm.prompts import CUSTOMER_PROFILE_PROMPT
from app.services.ingest import parse_upload
from app.services.media import complete_instruction

MAX_UPLOAD_BYTES = 15 * 1024 * 1024

REQUIRED = (
    ("name", "客户名称"),
    ("order_no", "订单号"),
    ("quantity", "数量"),
    ("delivery_date", "订单交期"),
    ("on_hand", "在手数量"),
    ("earliest_completion", "最早可完工日期"),
)


def preview_customer_file(filename: str, data: bytes) -> dict:
    if not data:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 15MB")
    try:
        text, _table, _kind = parse_upload(filename or "upload.txt", data)
    except ValueError as exc:
        message = str(exc)
        status = 502 if "接口" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    return preview_customer_chat(text)


def preview_customer_chat(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="先粘贴文本，或上传文件")
    parsed = parse_customer_chat(raw)
    if get_settings().extract_mode == "llm":
        parsed = _prefer_model(raw, parsed)
    elif get_settings().extract_mode != "fixture":
        raise ExtractError("EXTRACT_MODE 只能是 fixture 或 llm。")
    grounded = ground_profile(parsed, raw)
    return {**grounded, "missing": missing_labels(grounded)}


def parse_customer_chat(text: str) -> dict:
    name, contact = _speaker(text)
    labeled_name = _after(text, r"客户(?:名称)?[:：]\s*(\S+)") or _after(text, r"[，,]客户([^\s，,。]+)")
    if labeled_name and not labeled_name.startswith("聊天"):
        name = labeled_name
    labeled_contact = _after(text, r"联系人[:：]?\s*([^\s，,。]+)")
    if labeled_contact:
        contact = labeled_contact
    phone = _after(text, r"电话\s*(1\d{10})")
    product = _after(text, r"(?:规格就按|商品[:：]|产品[:：])\s*([^，。\n\s]+)") or _after(
        text, r"要一批([^，。\n\s]+)"
    )
    quantity = _after(text, r"数量\s*(\d+)\s*件") or _after(text, r"(\d+)\s*件")
    delivery = _after(text, r"订单交期(?:按|为|：|:|\s)*(\d{4}-\d{2}-\d{2})") or _after(
        text, r"(\d{4}-\d{2}-\d{2})\s*前到"
    )
    earliest = _after(text, r"最早可完工日期(?:按|为|：|:|\s)*(\d{4}-\d{2}-\d{2})")
    on_hand = _after(text, r"(?:在手(?:数量)?|库存)\s*(\d+)")
    return {
        "name": name,
        "contact": contact,
        "note": f"电话 {phone}" if phone else "",
        "order_no": _after(text, r"(ORD-\d+)") or "",
        "product": product,
        "quantity": quantity,
        "delivery_date": delivery,
        "on_hand": on_hand,
        "earliest_completion": earliest,
    }


def ground_profile(profile: dict, text: str) -> dict:
    compact = re.sub(r"\s+", "", text)
    grounded = {
        "name": _keep_text(profile.get("name"), compact),
        "contact": _keep_text(profile.get("contact"), compact),
        "note": _keep_text(profile.get("note"), compact),
        "order_no": _keep_text(profile.get("order_no"), compact),
        "product": _keep_text(profile.get("product"), compact),
        "quantity": _keep_int(profile.get("quantity"), compact, positive=True),
        "delivery_date": _keep_date(profile.get("delivery_date"), text),
        "on_hand": _keep_int(profile.get("on_hand"), compact, positive=False),
        "earliest_completion": _keep_date(profile.get("earliest_completion"), text),
    }
    return grounded


def missing_labels(profile: dict) -> list[str]:
    labels = []
    for key, label in REQUIRED:
        value = profile.get(key)
        if value is None or value == "":
            labels.append(label)
    return labels


def complete_text(system: str, user: str, **_kwargs) -> str:
    try:
        return complete_instruction(system, user)
    except ValueError as exc:
        raise ExtractError(str(exc), status_code=502) from exc


def _prefer_model(text: str, parsed: dict) -> dict:
    raw = complete_text(CUSTOMER_PROFILE_PROMPT, text)
    model = ground_profile(_load_profile(raw), text)
    merged = dict(parsed)
    for key, value in model.items():
        if value not in (None, ""):
            merged[key] = value
    return merged


def _load_profile(raw: str) -> dict:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ExtractError("模型没有返回可解析的客户信息。", status_code=502)
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ExtractError("模型没有返回可解析的客户信息。", status_code=502) from exc
    if not isinstance(data, dict):
        raise ExtractError("模型没有返回可解析的客户信息。", status_code=502)
    return data


def _speaker(text: str) -> tuple[str, str]:
    for line in text.splitlines():
        matched = re.match(r"\[[^\]]+\]\s*(.+)", line.strip())
        if matched is None:
            continue
        speaker = matched.group(1).strip()
        if not speaker or speaker.startswith("销售"):
            continue
        bits = speaker.split()
        return bits[0], " ".join(bits[1:])
    return "", ""


def _after(text: str, pattern: str) -> str:
    matched = re.search(pattern, text)
    if matched is None:
        return ""
    return matched.group(1).strip()


def _keep_text(value, compact: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text or re.sub(r"\s+", "", text) not in compact:
        return ""
    return text


def _keep_date(value, text: str) -> str:
    raw = "" if value is None else str(value).strip()[:10]
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-" or raw not in text:
        return ""
    return raw


def _keep_int(value, compact: str, *, positive: bool) -> int | None:
    if value is None or value == "":
        return None
    digits = str(value).strip()
    if not digits.isdigit() or digits not in compact:
        return None
    number = int(digits)
    if positive and number <= 0:
        return None
    if number < 0:
        return None
    return number

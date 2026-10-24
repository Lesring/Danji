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


_COLUMN_ALIASES = (
    ("最早可完工日期", "earliest_completion"),
    ("最早可完工", "earliest_completion"),
    ("在手数量", "on_hand"),
    ("订单数量", "quantity"),
    ("订单交期", "delivery_date"),
    ("交货日期", "delivery_date"),
    ("订单号", "order_no"),
    ("含税单价", "price"),
    ("物料", "product"),
    ("商品", "product"),
    ("产品", "product"),
    ("品名", "product"),
    ("规格", "spec"),
    ("型号", "spec"),
    ("件数", "quantity"),
    ("数量", "quantity"),
    ("交期", "delivery_date"),
    ("在手", "on_hand"),
    ("库存", "on_hand"),
    ("单价", "price"),
)


def preview_customer_file(filename: str, data: bytes) -> dict:
    return preview_customer_files([(filename, data)])


def preview_customer_files(items: list[tuple[str, bytes]], pasted: str = "") -> dict:
    if not items and not (pasted or "").strip():
        raise HTTPException(status_code=400, detail="先粘贴文本，或上传文件")
    prose: list[str] = []
    tables: list[dict] = []
    if (pasted or "").strip():
        prose.append(pasted.strip())
    for filename, data in items:
        if not data:
            raise HTTPException(status_code=400, detail="文件是空的")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="文件超过 15MB")
        try:
            text, table, _kind = parse_upload(filename or "upload.txt", data)
        except ValueError as exc:
            message = str(exc)
            status = 502 if "接口" in message else 400
            raise HTTPException(status_code=status, detail=message) from exc
        sheets = table.get("sheets") if isinstance(table, dict) else None
        rows = table.get("rows") if isinstance(table, dict) else None
        spreadsheet = (filename or "").lower().endswith((".csv", ".xlsx", ".xlsm"))
        if sheets:
            tables.extend(sheets)
        elif rows:
            tables.append(table)
        if text.strip() and not spreadsheet:
            prose.append(text.strip())
    orders = _orders_from_tables(tables)
    identity = preview_customer_chat("\n\n".join(prose)) if prose else _empty_profile()
    if not orders:
        return identity
    first = orders[0]
    merged = {
        "name": identity.get("name") or "",
        "contact": identity.get("contact") or "",
        "note": identity.get("note") or "",
        "order_no": first.get("order_no") or "",
        "product": first.get("product") or "",
        "quantity": first.get("quantity"),
        "delivery_date": first.get("delivery_date") or "",
        "on_hand": first.get("on_hand"),
        "earliest_completion": first.get("earliest_completion") or "",
        "orders": orders,
    }
    merged["missing"] = missing_labels(merged)
    return merged


def _empty_profile() -> dict:
    profile = {
        "name": "",
        "contact": "",
        "note": "",
        "order_no": "",
        "product": "",
        "quantity": None,
        "delivery_date": "",
        "on_hand": None,
        "earliest_completion": "",
    }
    return {**profile, "missing": missing_labels(profile)}


def _orders_from_tables(tables: list[dict]) -> list[dict]:
    orders = []
    for table in tables:
        orders.extend(_orders_from_table(table.get("columns") or [], table.get("rows") or []))
    return orders


def _orders_from_table(columns: list[str], rows: list[list[str]]) -> list[dict]:
    fields = [_column_field(column) for column in columns]
    if "product" not in fields and "quantity" not in fields:
        return []
    orders = []
    for row in rows:
        padded = list(row) + [""] * (len(fields) - len(row))
        record = {"product": "", "spec": "", "quantity": "", "delivery_date": "", "order_no": "", "on_hand": "", "earliest_completion": ""}
        for field, cell in zip(fields, padded):
            if field and not record.get(field):
                record[field] = str(cell or "").strip()
        product = " ".join(part for part in (record["product"], record["spec"]) if part).strip()
        quantity = _parse_qty(record["quantity"])
        delivery = _parse_day(record["delivery_date"])
        on_hand = _parse_qty(record["on_hand"], positive=False)
        earliest = _parse_day(record["earliest_completion"])
        if not product and quantity is None and not delivery:
            continue
        line = {
            "order_no": record["order_no"],
            "product": product,
            "quantity": quantity,
            "delivery_date": delivery,
            "on_hand": on_hand,
            "earliest_completion": earliest,
        }
        line["missing"] = [
            label
            for key, label in REQUIRED
            if key != "name" and (line.get(key) is None or line.get(key) == "")
        ]
        orders.append(line)
    return orders


def _column_field(header: str) -> str:
    compact = re.sub(r"\s+", "", header or "")
    matches = [(len(alias), field) for alias, field in _COLUMN_ALIASES if alias in compact]
    if not matches:
        return ""
    return max(matches)[1]


def _parse_qty(value: str, positive: bool = True) -> int | None:
    matched = re.search(r"\d+", value or "")
    if matched is None:
        return None
    number = int(matched.group(0))
    if positive and number <= 0:
        return None
    if number < 0:
        return None
    return number


def _parse_day(value: str) -> str:
    matched = re.search(r"\d{4}-\d{2}-\d{2}", value or "")
    return matched.group(0) if matched else ""


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
        matched = re.match(r"\[([^\]]+)\]\s*(.+)", line.strip())
        if matched is None:
            continue
        if re.fullmatch(r"R\d+C\d+", matched.group(1)):
            continue
        speaker = matched.group(2).strip()
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

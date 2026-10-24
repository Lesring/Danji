import json
from datetime import date, timedelta


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, default):
    if not value:
        return default
    return json.loads(value)


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = ""
    for char in str(value):
        if char.isdigit():
            digits += char
        elif digits:
            break
    if not digits:
        return None
    return int(digits)


def parse_date(value: str | None, default_year: int = 2026) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            date.fromisoformat(text[:10])
        except ValueError:
            return None
        return text[:10]
    year = default_year
    month = None
    day = None
    buffer = ""
    parts: list[int] = []
    for char in text:
        if char.isdigit():
            buffer += char
            continue
        if buffer and char in "年月日/-":
            parts.append(int(buffer))
            buffer = ""
            if char == "年" and parts:
                year = parts[-1]
        elif buffer:
            parts.append(int(buffer))
            buffer = ""
    if buffer:
        parts.append(int(buffer))
    if "年" in text and len(parts) >= 3:
        year, month, day = parts[0], parts[1], parts[2]
    elif len(parts) >= 2:
        month, day = parts[-2], parts[-1]
    if month is None or day is None:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def plus_days(today: str, days: int) -> str:
    return (date.fromisoformat(today) + timedelta(days=days)).isoformat()


FIELD_LABELS = {
    "quantity": "数量",
    "delivery_date": "交期",
    "available_qty": "可用库存",
    "status": "状态",
}

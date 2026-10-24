from app.util import dumps, loads, parse_date, parse_int


def encode_additional(
    product: str,
    quantity: int,
    delivery_date: str = "",
    on_hand: int | None = None,
    earliest_completion: str = "",
) -> str:
    return dumps(
        {
            "product": product,
            "quantity": quantity,
            "delivery_date": delivery_date,
            "on_hand": on_hand,
            "earliest_completion": earliest_completion,
        }
    )


def decode_additional(raw: str | None) -> dict | None:
    data = loads(raw, None)
    if not isinstance(data, dict):
        return None
    product = str(data.get("product") or "").strip()
    quantity_raw = data.get("quantity")
    quantity = parse_int(str(quantity_raw)) if quantity_raw is not None and str(quantity_raw).strip() else None
    if not product or quantity is None or quantity <= 0:
        return None
    on_hand_raw = data.get("on_hand")
    if on_hand_raw is None or str(on_hand_raw).strip() == "":
        on_hand = None
    else:
        on_hand = parse_int(str(on_hand_raw))
        if on_hand is None or on_hand < 0:
            return None
    delivery = parse_date(str(data.get("delivery_date"))) if data.get("delivery_date") else ""
    earliest = parse_date(str(data.get("earliest_completion"))) if data.get("earliest_completion") else ""
    return {
        "product": product,
        "quantity": quantity,
        "delivery_date": delivery or "",
        "on_hand": on_hand,
        "earliest_completion": earliest or "",
    }

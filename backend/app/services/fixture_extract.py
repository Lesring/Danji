import re

from app.services.additional import encode_additional
from app.services.rules import EventIn


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _line_with(text: str, needle: str) -> str:
    compact_needle = _compact(needle)
    for line in text.splitlines():
        if compact_needle and compact_needle in _compact(line):
            return line.strip()
    stripped = text.strip()
    return stripped


def extract_fixture_events(
    text: str,
    quantity: int,
    delivery_date: str,
    available_qty: int,
    earliest_completion: str = "",
    delivered_qty: int = 0,
    customer_orders: list | None = None,
) -> list[EventIn]:
    events = _extract_core(
        text,
        quantity,
        delivery_date,
        available_qty,
        earliest_completion,
        delivered_qty,
    )
    events.extend(cooperation_events(text, customer_orders or []))
    return events


def _extract_core(
    text: str,
    quantity: int,
    delivery_date: str,
    available_qty: int,
    earliest_completion: str = "",
    delivered_qty: int = 0,
) -> list[EventIn]:
    compact = _compact(text)
    if "分批交付" in compact:
        line = _line_with(text, "分批交付")
        return [
            EventIn(
                event_type="commitment",
                proposal_origin="销售承诺",
                evidence_excerpt=line,
                explanation="销售补充了分批交付的沟通进展，应关联已有跟进任务。",
                suggestion_key="confirm_split_or_date",
                confidence=0.9,
            )
        ]

    if ("包装" in compact or "谁跟进还没定" in compact) and "先取消" not in compact:
        line = _line_with(text, "谁跟进") if "谁跟进" in compact else _line_with(text, "包装")
        return [
            EventIn(
                event_type="owner_unclear",
                proposal_origin="内部沟通",
                evidence_excerpt=line,
                explanation="沟通里提到了待确认事项，但没有指定销售、生产或订单负责人。",
                confidence=0.8,
            )
        ]

    if any(token in compact for token in ("先取消", "临时取消", "取消订单", "订单取消")) and "不取消" not in compact:
        line = _line_with(text, "取消")
        return [
            EventIn(
                event_type="cancellation",
                field_name="status",
                current_value="已确认",
                proposed_value="待确认取消",
                proposal_origin="客户期望",
                evidence_excerpt=line,
                explanation="客户提出取消，在人工确认前不直接关闭订单。",
                confidence=0.92,
            )
        ]

    mentions_early = "11月6" in compact
    mentions_original = "原交期" in compact or "11月10" in compact
    mentions_reversal = "说错" in compact or "还是按" in compact or "10:30" in compact
    if mentions_early and mentions_original and mentions_reversal and "150" not in compact:
        early_line = _line_with(text, "11月6")
        original_line = _line_with(text, "原交期") if "原交期" in compact else _line_with(text, "11月10")
        return [
            EventIn(
                event_type="delivery_change",
                field_name="delivery_date",
                current_value=delivery_date,
                proposed_value="2026-11-06",
                proposal_origin="客户期望",
                evidence_excerpt=early_line,
                explanation="客户先提出提前到 11 月 6 日。",
                confidence=0.9,
            ),
            EventIn(
                event_type="delivery_change",
                field_name="delivery_date",
                current_value=delivery_date,
                proposed_value="2026-11-10",
                proposal_origin="客户期望",
                evidence_excerpt=original_line,
                explanation="客户随后改口，要求维持原交期。两条提议都保留。",
                confidence=0.9,
            ),
        ]

    if "150" in compact and mentions_early:
        customer_line = _line_with(text, "150")
        events = [
            EventIn(
                event_type="quantity_change",
                field_name="quantity",
                current_value=str(quantity),
                proposed_value="150",
                proposal_origin="客户期望",
                evidence_excerpt=customer_line,
                explanation="客户要求把数量增加到 150 件。",
                confidence=0.94,
            ),
            EventIn(
                event_type="delivery_change",
                field_name="delivery_date",
                current_value=delivery_date,
                proposed_value="2026-11-06",
                proposal_origin="客户期望",
                evidence_excerpt=_line_with(text, "11月6"),
                explanation="客户希望 11 月 6 日前发出。",
                confidence=0.94,
            ),
        ]
        if "库存" in compact:
            events.append(
                EventIn(
                    event_type="inventory_fact",
                    field_name="available_qty",
                    current_value=str(available_qty),
                    proposed_value="120" if "120" in compact else str(available_qty),
                    proposal_origin="库存事实",
                    evidence_excerpt=_line_with(text, "库存"),
                    explanation="确认后会按这句话更新该商品的在手数量，并重算未关闭订单。未确认前不改库存。",
                    confidence=0.9,
                )
            )
        if "跟客户说" in compact or "口头" in compact or "答应" in compact:
            events.append(
                EventIn(
                    event_type="commitment",
                    proposal_origin="销售承诺",
                    evidence_excerpt=_line_with(text, "跟客户说") if "跟客户说" in compact else _line_with(text, "答应"),
                    explanation="销售已口头答应 150 件、11 月 6 日发货，该承诺尚未核实，不能直接改写订单。",
                    proposed_value="150",
                    confidence=0.93,
                )
            )
        return events

    events: list[EventIn] = []
    partial = _partial_delivery(text, quantity, delivered_qty)
    if partial is not None:
        events.append(partial)
    events.extend(_additional_products(text))
    stock_match = re.search(r"(?:库存只剩|库存还有|现在库存|在手(?:仅剩|只剩)?|可用库存(?:调整为|改为)?)\s*(\d+)\s*件", text)
    if stock_match and stock_match.group(1) != str(available_qty):
        proposed = stock_match.group(1)
        events.append(
            EventIn(
                event_type="inventory_fact",
                field_name="available_qty",
                current_value=str(available_qty),
                proposed_value=proposed,
                proposal_origin="库存事实",
                evidence_excerpt=_line_with(text, proposed),
                explanation=f"确认后会把该商品的在手数量改为 {proposed} 件，并重算未关闭订单。未确认前不改库存。",
                confidence=0.9,
            )
        )
    prod_match = re.search(r"最早\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if prod_match:
        proposed_date = f"2026-{int(prod_match.group(1)):02d}-{int(prod_match.group(2)):02d}"
        if proposed_date != earliest_completion:
            events.append(
                EventIn(
                    event_type="production_constraint",
                    field_name="earliest_completion",
                    current_value=earliest_completion,
                    proposed_value=proposed_date,
                    proposal_origin="生产约束",
                    evidence_excerpt=_line_with(text, "最早"),
                    explanation=f"确认后会把该商品的最早可完工日期改为 {proposed_date}，并重算未关闭订单。未确认前不改订单交期。",
                    confidence=0.9,
                )
            )
    match = re.search(r"(?:增加到|数量改为|改为)\s*(\d+)\s*件", text)
    if match and "11月6" not in compact:
        proposed = match.group(1)
        events.append(
            EventIn(
                event_type="quantity_change",
                field_name="quantity",
                current_value=str(quantity),
                proposed_value=proposed,
                proposal_origin="客户期望",
                evidence_excerpt=_line_with(text, proposed),
                explanation=f"客户要求把数量调整为 {proposed} 件，未提出新的交付日期。",
                confidence=0.9,
            )
        )
    return events


_BUY_INTENT = re.compile(r"再买|还要买|另外|另买|还需要|也要买|再采购|同时买")
_PRODUCT_QTY = re.compile(r"([\u4e00-\u9fff]{2,12})\s*(\d+)\s*件")
_NAME_PREFIXES = (
    "另外再买",
    "另外还要买",
    "另外要买",
    "还要再买",
    "还要买",
    "另外买",
    "也要买",
    "同时买",
    "再采购",
    "还需要",
    "另买",
    "再买",
    "另外要",
    "还要",
    "另外",
    "以及",
    "和",
    "再",
)
_NAME_STOP = {"库存", "在手", "当前库存", "可用库存", "数量", "这批", "这批货", "订单", "客户", "已经", "交付", "要货"}


def _clean_product_name(raw: str) -> str:
    name = raw.strip()
    changed = True
    while changed and name:
        changed = False
        for prefix in _NAME_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix):
                name = name[len(prefix) :].strip(" ，,。；;、:：")
                changed = True
                break
    return name.strip(" ，,。；;、:：")


def _clause_after(text: str, end: int) -> str:
    window = text[end : end + 80]
    stop = len(window)
    for token in ("另外", "再买", "还要买", "\n"):
        index = window.find(token)
        if index > 0:
            stop = min(stop, index)
    return window[:stop]


def _partial_delivery(text: str, quantity: int, delivered_qty: int) -> EventIn | None:
    half = re.search(r"已(?:经)?(?:交付|发货|发出|发了)(?:了)?一半|交付了一半|发了一半|交了一半", text)
    if half:
        line = _line_with(text, "一半")
        half_qty = quantity // 2
        return EventIn(
            event_type="partial_delivery",
            field_name="delivered_qty",
            current_value=str(delivered_qty),
            proposed_value="一半",
            proposal_origin="客户期望",
            evidence_excerpt=line,
            explanation=(
                f"对话说这批已经交付一半。确认后已交付数量记为 {half_qty}（订单数量 {quantity} 的一半），"
                "原订单数量保留，占用只计未交部分。"
            ),
            confidence=0.92,
        )
    shipped = re.search(r"已(?:经)?(?:交付|发出|发货|发了|发)\s*(\d+)\s*件", text)
    if shipped is None:
        return None
    amount = shipped.group(1)
    line = _line_with(text, amount)
    return EventIn(
        event_type="partial_delivery",
        field_name="delivered_qty",
        current_value=str(delivered_qty),
        proposed_value=amount,
        proposal_origin="客户期望",
        evidence_excerpt=line,
        explanation=f"对话说已交付 {amount} 件。确认后记入已交付数量，不把订单数量改小，占用只计未交部分。",
        confidence=0.9,
    )


def _additional_products(text: str) -> list[EventIn]:
    if _BUY_INTENT.search(text) is None:
        return []
    events: list[EventIn] = []
    seen: set[str] = set()
    for match in _PRODUCT_QTY.finditer(text):
        name = _clean_product_name(match.group(1))
        if len(name) < 2 or name in _NAME_STOP or name in seen:
            continue
        seen.add(name)
        quantity = int(match.group(2))
        window = _clause_after(text, match.end())
        delivery = ""
        date_match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", window)
        if date_match and "最早" not in window[max(0, date_match.start() - 12) : date_match.start()]:
            delivery = f"2026-{int(date_match.group(1)):02d}-{int(date_match.group(2)):02d}"
        on_hand = None
        stock_match = re.search(r"(?:库存|在手)\s*(\d+)\s*件", window)
        if stock_match:
            on_hand = int(stock_match.group(1))
        earliest = ""
        early_iso = re.search(r"最早(?:可完工日期|可完工|完工日期|完工)?\s*(\d{4}-\d{2}-\d{2})", window)
        early_cn = re.search(r"最早(?:可完工日期|可完工|完工日期|完工)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", window)
        if early_iso:
            earliest = early_iso.group(1)
        elif early_cn:
            earliest = f"2026-{int(early_cn.group(1)):02d}-{int(early_cn.group(2)):02d}"
        events.append(
            EventIn(
                event_type="additional_order",
                proposed_value=encode_additional(name, quantity, delivery, on_hand, earliest),
                proposal_origin="客户期望",
                evidence_excerpt=_line_with(text, name),
                explanation=f"建议为该客户新增商品订单：{name} {quantity} 件。确认前不创建订单，订单号由系统生成。",
                confidence=0.9,
            )
        )
    return events


_KNOWN_PRODUCTS = (
    "小型断路器",
    "塑壳断路器",
    "漏电断路器",
    "框架断路器",
    "交流接触器",
    "热继电器",
    "时间继电器",
    "交流电流表",
    "电能表",
)
_ORDER_FIELD_TYPES = {"quantity_change", "delivery_change", "additional_order", "commitment"}


def cooperation_events(text: str, customer_orders: list | None) -> list[EventIn]:
    history = list(customer_orders or [])
    events: list[EventIn] = []
    meeting = _offline_meeting(text)
    if meeting is not None:
        events.append(meeting)
    repeat = _repeat_spec(text, history)
    if repeat is not None:
        events.append(repeat)
        return events
    gap = _clarification(text)
    if gap is not None:
        events.append(gap)
    return events


def merge_cooperation(events: list[EventIn], text: str, customer_orders: list | None) -> list[EventIn]:
    required = cooperation_events(text, customer_orders)
    if not required:
        return events
    locked = {item.event_type for item in required}
    drop_fields = "repeat_spec" in locked or "clarification" in locked
    meeting_dates = {
        (item.proposed_value or "")[:10]
        for item in required
        if item.event_type == "offline_meeting" and item.proposed_value
    }
    kept: list[EventIn] = []
    for event in events:
        if event.event_type in {"offline_meeting", "clarification", "repeat_spec"}:
            continue
        if drop_fields and event.event_type in _ORDER_FIELD_TYPES:
            continue
        if (
            event.event_type == "delivery_change"
            and (event.proposed_value or "")[:10] in meeting_dates
        ):
            continue
        if event.event_type == "additional_order" and meeting_dates:
            excerpt = event.evidence_excerpt or ""
            if "当面谈" in excerpt or "谈的是" in excerpt or "备货" in excerpt:
                continue
        if _invented_value(event, text):
            continue
        kept.append(event)
    kept.extend(required)
    return kept


def _invented_value(event: EventIn, text: str) -> bool:
    compact = _compact(text)
    if event.event_type == "quantity_change" and event.proposed_value:
        return _compact(event.proposed_value) not in compact
    if event.event_type in {"delivery_change", "production_constraint"} and event.proposed_value:
        parsed = event.proposed_value.strip()[:10]
        if parsed and parsed in text:
            return False
        month = re.search(r"(\d{4})-(\d{2})-(\d{2})", parsed)
        if month and f"{int(month.group(2))}月{int(month.group(3))}" in compact:
            return False
        return True
    blob = f"{event.proposed_value or ''}{event.explanation or ''}"
    if event.event_type == "repeat_spec":
        for token in ("NXB", "NM1", "NXBLE", "CJX2"):
            if token in blob and token not in text:
                return True
    return False


def _offline_meeting(text: str) -> EventIn | None:
    compact = _compact(text)
    if "当面谈" not in compact and "派人到厂" not in compact:
        return None
    line = _line_with(text, "当面谈") if "当面谈" in compact else _line_with(text, "派人到厂")
    iso = re.search(r"(20\d{2}-\d{2}-\d{2})", line)
    when = iso.group(1) if iso else ""
    weekday = ""
    week = re.search(r"星期[一二三四五六日天]", line)
    if week:
        weekday = week.group(0)
    clock = ""
    clock_match = re.search(r"(上午|下午)\s*(\d{1,2})\s*点", line)
    if clock_match:
        clock = f"{clock_match.group(1)}{clock_match.group(2)}点"
    visitor = ""
    visitor_match = re.search(r"采购\s*([\u4e00-\u9fff]{2})", text)
    if visitor_match:
        visitor = f"采购{visitor_match.group(1)}"
    host = "销售"
    host_match = re.search(r"销售\s*([\u4e00-\u9fff]{2})", text)
    if host_match:
        host = f"销售{host_match.group(1)}"
    place = ""
    place_match = re.search(r"地点在([^，。\n]{2,20})", text)
    if place_match:
        place = place_match.group(1).strip()
    elif "厂" in line:
        place = "厂里"
    topic = ""
    topic_match = re.search(r"谈的是([^。\n]{2,24})", text)
    if topic_match:
        topic = topic_match.group(1).strip()
    pieces = [item for item in (when, weekday, clock) if item]
    when_text = "".join(pieces) or "时间未写清"
    who = visitor or "客户未说明来人"
    where = place or "地点未写清"
    what = topic or "事项未写清"
    explanation = f"{when_text}，{who}到{where}当面谈，谈的是{what}，{host}接待。确认前不改订单数量和订单交期。"
    return EventIn(
        event_type="offline_meeting",
        current_value=host,
        proposed_value=when,
        proposal_origin="客户期望",
        evidence_excerpt=line,
        explanation=explanation,
        confidence=0.9,
        needs_review=True,
    )


def _repeat_spec(text: str, history: list) -> EventIn | None:
    if "像上次一样" not in _compact(text):
        return None
    line = _line_with(text, "像上次一样")
    quantity = ""
    qty_match = re.search(r"(?:再来|再要|再下)\s*(\d+)\s*[件台]", text)
    if qty_match:
        quantity = qty_match.group(1)
    delivery = ""
    iso = re.search(r"(20\d{2}-\d{2}-\d{2})", line) or re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    if iso:
        delivery = iso.group(1)
    else:
        cn = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", line)
        if cn:
            delivery = f"2026-{int(cn.group(1)):02d}-{int(cn.group(2)):02d}"
    stated = []
    if quantity:
        stated.append(f"客户说了 {quantity} 件")
    if delivery:
        stated.append(f"要求交期 {delivery}")
    stated_text = "，".join(stated)
    if not history:
        extra = f"{stated_text}。" if stated_text else ""
        return EventIn(
            event_type="clarification",
            proposed_value="规格",
            proposal_origin="客户期望",
            evidence_excerpt=line,
            explanation=(
                f"没有该客户的历史订单，不能把「像上次一样」对上规格。{extra}"
                "规格仍须问清。不猜测型号，不新建订单。"
            ),
            confidence=0.9,
            needs_review=True,
        )
    last = history[-1]
    order_no = str(last.get("order_no") or "")
    product = str(last.get("product") or "")
    if not order_no or not product:
        return EventIn(
            event_type="clarification",
            proposed_value="规格",
            proposal_origin="客户期望",
            evidence_excerpt=line,
            explanation="历史订单里没有可沿用的规格。不猜测型号，不新建订单。",
            confidence=0.86,
            needs_review=True,
        )
    demand = []
    if quantity:
        demand.append(f"新数量 {quantity} 件")
    if delivery:
        demand.append(f"要求交期 {delivery}")
    demand_text = "，".join(demand) if demand else "数量和要求交期这次没说"
    return EventIn(
        event_type="repeat_spec",
        field_name="product",
        current_value=order_no,
        proposed_value=product,
        proposal_origin="客户期望",
        evidence_excerpt=line,
        explanation=(
            f"规格沿用该客户上一笔合作 {order_no} 的{product}。{demand_text}。"
            "确认前不新建、不改写订单。"
        ),
        confidence=0.9,
        needs_review=True,
    )


def _stated_delivery(text: str) -> bool:
    stripped = re.sub(r"\[[^\]]*\]", "", text)
    if re.search(r"\d{1,2}\s*月\s*\d{1,2}\s*日", stripped):
        return True
    if re.search(r"20\d{2}-\d{2}-\d{2}", stripped) and re.search(r"交期|要货|前到", stripped):
        return True
    return False


def _clarification(text: str) -> EventIn | None:
    if any(name in text for name in _KNOWN_PRODUCTS):
        specific = True
    elif "断路器" in text or "要一批" in text or "尽快" in text:
        specific = False
    else:
        return None
    missing: list[str] = []
    if not specific:
        missing.append("规格")
    if not re.search(r"\d+\s*[件台]", text):
        missing.append("数量")
    if "尽快" in text or (not specific and not _stated_delivery(text)):
        missing.append("要求交期")
    if not missing:
        return None
    line = text.strip().splitlines()[0]
    for needle in ("尽快", "要一批", "断路器", "交期"):
        if needle in _compact(text):
            line = _line_with(text, needle)
            break
    joined = "、".join(missing)
    return EventIn(
        event_type="clarification",
        proposed_value=joined,
        proposal_origin="客户期望",
        evidence_excerpt=line,
        explanation=f"还缺{joined}。先待办问清，不新建订单，不补型号、数量或日期。",
        confidence=0.9,
        needs_review=True,
    )

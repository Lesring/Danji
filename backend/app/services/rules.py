from dataclasses import dataclass, field

from app.util import FIELD_LABELS, parse_date, parse_int, plus_days


@dataclass
class EventIn:
    event_type: str
    field_name: str | None = None
    current_value: str | None = None
    proposed_value: str | None = None
    proposal_origin: str = ""
    evidence_excerpt: str = ""
    status: str = "draft"
    suggestion_key: str | None = None
    explanation: str = ""
    confidence: float = 0.5
    needs_review: bool = True
    order_no: str | None = None


@dataclass
class OpenTask:
    id: int
    suggestion_key: str
    title: str
    status: str
    owner: str = ""


@dataclass
class OrderSnap:
    quantity: int
    delivery_date: str
    status: str
    available_qty: int
    earliest_completion: str
    track_commitment: bool = True


@dataclass
class RiskOut:
    code: str
    message: str
    event_refs: list[int] = field(default_factory=list)
    evidence_excerpt: str = ""


@dataclass
class SuggestionOut:
    suggestion_key: str
    title: str
    owner: str
    due_date: str | None
    reason: str
    action: str = "create"
    target_task_id: int | None = None
    order_status: str | None = None
    event_refs: list[int] = field(default_factory=list)


@dataclass
class Analysis:
    risks: list[RiskOut]
    suggestions: list[SuggestionOut]
    conflict_notes: dict[int, str]


def analyze(order: OrderSnap, events: list[EventIn], open_tasks: list[OpenTask], today: str) -> Analysis:
    active_events = [event for event in events if event.status != "rejected"]
    active_tasks = [task for task in open_tasks if task.status not in ("已完成", "已关闭")]
    risks: list[RiskOut] = []
    notes: dict[int, list[str]] = {}

    def add_note(index: int, text: str) -> None:
        notes.setdefault(index, []).append(text)

    qty_hits: list[tuple[int, int]] = []
    date_hits: list[tuple[int, str]] = []
    for index, event in enumerate(active_events):
        if event.field_name == "quantity" and event.event_type == "quantity_change":
            proposed = parse_int(event.proposed_value)
            if proposed is not None and proposed > order.available_qty:
                qty_hits.append((index, proposed))
        if event.field_name == "delivery_date" and event.event_type == "delivery_change":
            proposed_date = parse_date(event.proposed_value)
            if proposed_date and proposed_date < order.earliest_completion:
                date_hits.append((index, proposed_date))

    if qty_hits:
        proposed = max(item[1] for item in qty_hits)
        shortfall = proposed - order.available_qty
        refs = [index for index, _ in qty_hits]
        risks.append(
            RiskOut(
                code="qty_over_stock",
                message=f"提议数量 {proposed} 件，超过本单可承诺上限 {order.available_qty} 件，缺口 {shortfall} 件。",
                event_refs=refs,
                evidence_excerpt=active_events[refs[0]].evidence_excerpt,
            )
        )
        for index, _ in qty_hits:
            add_note(index, "数量超出可用库存")

    if date_hits:
        proposed_date = min(item[1] for item in date_hits)
        refs = [index for index, _ in date_hits]
        risks.append(
            RiskOut(
                code="delivery_before_capacity",
                message=f"要求交期 {proposed_date} 早于最早可完工日期 {order.earliest_completion}。",
                event_refs=refs,
                evidence_excerpt=active_events[refs[0]].evidence_excerpt,
            )
        )
        for index, _ in date_hits:
            add_note(index, "交期早于生产能力")

    commitments = [
        (index, event)
        for index, event in enumerate(active_events)
        if event.event_type == "commitment" or event.proposal_origin == "销售承诺"
    ]
    if commitments and not _commitment_verified(active_events):
        index, event = commitments[0]
        risks.append(
            RiskOut(
                code="unverified_commitment",
                message="存在尚未被生产或订单负责人确认的销售承诺，不能把该承诺写入订单。",
                event_refs=[item[0] for item in commitments],
                evidence_excerpt=event.evidence_excerpt,
            )
        )
        for ref, _event in commitments:
            add_note(ref, "承诺尚未核实")

    for field_name in ("quantity", "delivery_date"):
        values: dict[str, list[int]] = {}
        for index, event in enumerate(active_events):
            if event.field_name != field_name or not event.proposed_value:
                continue
            normalized = event.proposed_value.strip()
            if field_name == "delivery_date":
                normalized = parse_date(normalized) or normalized
            values.setdefault(normalized, []).append(index)
        if len(values) > 1:
            shown = "、".join(values.keys())
            refs = [index for indexes in values.values() for index in indexes]
            risks.append(
                RiskOut(
                    code="contradiction",
                    message=f"字段「{FIELD_LABELS[field_name]}」存在多个未拒绝的提议值：{shown}。系统不会自动选择其中一条。",
                    event_refs=refs,
                    evidence_excerpt=active_events[refs[0]].evidence_excerpt,
                )
            )
            for index in refs:
                add_note(index, "与其他提议矛盾")

    owner_events = [
        (index, event)
        for index, event in enumerate(active_events)
        if event.event_type == "owner_unclear"
    ]
    if owner_events:
        risks.append(
            RiskOut(
                code="owner_missing",
                message="有待办事项但没有可分配的责任人，任务将标为待指派。",
                event_refs=[index for index, _ in owner_events],
                evidence_excerpt=owner_events[0][1].evidence_excerpt,
            )
        )

    if order.track_commitment and order.quantity > order.available_qty:
        risks.append(
            RiskOut(
                code="qty_over_stock",
                message=(
                    f"已确认数量 {order.quantity} 件，超过本单可承诺上限 {order.available_qty} 件，"
                    f"缺口 {order.quantity - order.available_qty} 件。"
                ),
            )
        )
    committed_date = parse_date(order.delivery_date)
    if order.track_commitment and committed_date and committed_date < order.earliest_completion and not date_hits:
        risks.append(
            RiskOut(
                code="delivery_before_capacity",
                message=f"订单交期 {committed_date} 早于最早可完工日期 {order.earliest_completion}。",
            )
        )

    cancel_refs = _cancellation_refs(active_events)
    if cancel_refs:
        risks.append(
            RiskOut(
                code="cancellation",
                message="客户提出取消，且没有更新的相反要求。建议把订单标为待确认取消，并关闭未完成任务。",
                event_refs=cancel_refs,
                evidence_excerpt=active_events[cancel_refs[0]].evidence_excerpt,
            )
        )

    suggestions = _suggest(
        order,
        active_events,
        active_tasks,
        risks,
        today,
    )
    return Analysis(
        risks=risks,
        suggestions=suggestions,
        conflict_notes={index: "；".join(items) for index, items in notes.items()},
    )


def _commitment_verified(events: list[EventIn]) -> bool:
    return any(
        event.status == "confirmed" and event.proposal_origin in ("生产约束", "订单负责人")
        for event in events
    )


def _cancellation_refs(events: list[EventIn]) -> list[int]:
    refs: list[int] = []
    for index, event in enumerate(events):
        if event.event_type != "cancellation":
            continue
        later = events[index + 1 :]
        if any(item.event_type in ("quantity_change", "delivery_change") for item in later):
            continue
        refs.append(index)
    return refs


def _suggest(
    order: OrderSnap,
    events: list[EventIn],
    open_tasks: list[OpenTask],
    risks: list[RiskOut],
    today: str,
) -> list[SuggestionOut]:
    codes = {risk.code for risk in risks}
    suggestions: list[SuggestionOut] = []
    seen: set[str] = set()

    def emit(
        key: str,
        title: str,
        owner: str,
        reason: str,
        due_days: int = 1,
        when: str | None = None,
        order_status: str | None = None,
        action: str = "create",
        target_task_id: int | None = None,
        event_refs: list[int] | None = None,
    ) -> None:
        if key in seen:
            return
        if action == "create":
            existing = next((task for task in open_tasks if task.suggestion_key == key), None)
            if existing:
                action = "update"
                target_task_id = existing.id
                reason = f"新消息与未关闭任务「{existing.title}」相关，建议更新这条任务，而不是再新建一条。"
        seen.add(key)
        suggestions.append(
            SuggestionOut(
                suggestion_key=key,
                title=title,
                owner=owner,
                due_date=when or plus_days(today, due_days),
                reason=reason,
                action=action,
                target_task_id=target_task_id,
                order_status=order_status,
                event_refs=event_refs or [],
            )
        )

    conflict = "qty_over_stock" in codes or "delivery_before_capacity" in codes
    if conflict:
        proposed_qty = 0
        for event in events:
            if event.event_type == "quantity_change":
                proposed_qty = max(proposed_qty, parse_int(event.proposed_value) or 0)
        demands = []
        if proposed_qty > order.available_qty:
            demands.append(proposed_qty)
        if order.quantity > order.available_qty:
            demands.append(order.quantity)
        if demands:
            basis = max(demands)
            capacity_title = f"核实额外 {basis - order.available_qty} 件的产能与最早可完工日期"
        else:
            capacity_title = "核实提前交付的产能与最早可完工日期"
        emit(
            "confirm_split_or_date",
            "确认客户是否接受分批交付或调整交期",
            "销售",
            "数量或交期与库存、生产能力不一致，需要销售先和客户确认可接受的方案。",
            due_days=1,
        )
        emit(
            "verify_capacity",
            capacity_title,
            "生产",
            "在回复客户之前，需要生产确认缺口数量的最早可完工日期。",
            due_days=1,
        )
        emit(
            "mark_pending",
            "交期或数量确认前将订单标记为待确认",
            "订单负责人",
            "未核实的承诺不能视为订单已落实。",
            due_days=0,
            order_status="待确认",
        )
    elif "unverified_commitment" in codes:
        emit(
            "verify_commitment",
            "核实销售承诺是否可执行",
            "生产",
            "销售承诺尚未得到生产或订单负责人确认。",
            due_days=1,
        )

    if "contradiction" in codes:
        emit(
            "confirm_final_value",
            "核对前后不一致的最终口径",
            "销售",
            "同一字段有多条不同提议，系统不会自动合并。",
            due_days=1,
        )

    if "qty_over_stock" not in codes:
        for event in events:
            if event.event_type != "quantity_change":
                continue
            proposed = parse_int(event.proposed_value)
            current = parse_int(event.current_value)
            if proposed is None or proposed == current:
                continue
            emit(
                "confirm_quantity",
                f"确认数量由 {current} 改为 {proposed}",
                "订单负责人",
                "数量变更未超出可用库存，确认后才会写入订单。",
                due_days=1,
            )
            break

    if "delivery_before_capacity" not in codes and "contradiction" not in codes:
        for event in events:
            if event.event_type != "delivery_change":
                continue
            proposed = parse_date(event.proposed_value)
            current = parse_date(event.current_value)
            if not proposed or proposed == current:
                continue
            emit(
                "confirm_delivery",
                f"确认交期由 {current} 改为 {proposed}",
                "订单负责人",
                "交期变更未早于生产能力，确认后才会写入订单。",
                due_days=1,
            )
            break

    if "cancellation" in codes:
        emit(
            "mark_cancel",
            "确认客户取消，并将订单标为待确认取消",
            "订单负责人",
            "客户提出取消，且没有更新的相反要求。",
            due_days=0,
            order_status="待确认取消",
        )
        for task in open_tasks:
            emit(
                f"close_task_{task.id}",
                f"关闭任务：{task.title}",
                task.owner,
                "客户取消后，建议关闭尚未完成的任务。",
                due_days=0,
                action="close",
                target_task_id=task.id,
            )

    for index, event in enumerate(events):
        if event.suggestion_key:
            emit(
                event.suggestion_key,
                _title_for_key(event.suggestion_key, order),
                _owner_for_key(event.suggestion_key),
                event.explanation or "新消息与已有跟进事项相关。",
                event_refs=[index],
            )
        if event.event_type == "owner_unclear":
            emit(
                "unassigned_followup",
                "待指派：确认包装等未明确责任人的事项",
                "",
                "抽出了待办，但没有可分配的责任人。",
                due_days=1,
                event_refs=[index],
            )
        if event.event_type == "offline_meeting":
            emit(
                "offline_meeting",
                (event.explanation or "线下当面洽谈")[:200],
                (event.current_value or "销售")[:80],
                _with_excerpt(event.explanation or "客户约定派人到厂当面谈。确认前不改订单。", event.evidence_excerpt),
                due_days=1,
                when=parse_date(event.proposed_value),
                event_refs=[index],
            )
        if event.event_type == "clarification":
            missing = event.proposed_value or "规格、数量、要求交期"
            emit(
                "clarify_before_order",
                f"下单前先确认{missing}"[:200],
                "销售",
                _with_excerpt(event.explanation or "规格、数量或要求交期没说清。确认前不新建订单。", event.evidence_excerpt),
                due_days=1,
                event_refs=[index],
            )
        if event.event_type == "repeat_spec":
            emit(
                "confirm_repeat_spec",
                f"确认是否按 {event.current_value} 的{event.proposed_value}再下一笔"[:200],
                "销售",
                _with_excerpt(event.explanation or "规格沿用该客户上一笔合作。确认前不新建、不改写订单。", event.evidence_excerpt),
                due_days=1,
                event_refs=[index],
            )
    return suggestions


def _with_excerpt(reason: str, excerpt: str) -> str:
    quote = (excerpt or "").strip()
    if not quote or quote in reason:
        return reason
    return f"{reason} 依据：{quote}"


def _title_for_key(key: str, order: OrderSnap) -> str:
    titles = {
        "confirm_split_or_date": "确认客户是否接受分批交付或调整交期",
        "verify_capacity": "核实额外数量的产能与最早可完工日期",
        "mark_pending": "交期或数量确认前将订单标记为待确认",
        "verify_commitment": "核实销售承诺是否可执行",
        "confirm_final_value": "核对前后不一致的最终口径",
        "confirm_quantity": "确认数量变更",
        "confirm_delivery": "确认交期变更",
        "mark_cancel": "确认客户取消，并将订单标为待确认取消",
    }
    if key in titles:
        return titles[key]
    return f"跟进订单 {order.status} 相关事项"


def _owner_for_key(key: str) -> str:
    owners = {
        "confirm_split_or_date": "销售",
        "verify_capacity": "生产",
        "mark_pending": "订单负责人",
        "verify_commitment": "生产",
        "confirm_final_value": "销售",
        "confirm_quantity": "订单负责人",
        "confirm_delivery": "订单负责人",
        "mark_cancel": "订单负责人",
        "unassigned_followup": "",
    }
    return owners.get(key, "")

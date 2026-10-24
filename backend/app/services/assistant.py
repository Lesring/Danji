import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.client import ExtractError, complete_text
from app.llm.prompts import ASSISTANT_PROMPT, build_assistant_user
from app.models import BusinessEvent, Customer, Order, ProductSupply, Source, Task, TaskSuggestion
from app.services.supply import referencing_orders
from app.schemas import AssistantTurnIn
from app.services.apply import TASK_STATUSES
from app.services.ingest import SOURCE_TYPES
from app.util import parse_date

NO_KEY_REPLY = "还没有填写模型密钥。请在 backend/.env 中填写 LLM_API_KEY 后再使用对话。审核页仍可以粘贴沟通并解析为草案。"
ROUTES = {"workbench", "review", "tasks", "warehouse"}
EVENT_LABELS = {
    "quantity_change": "数量变更",
    "delivery_change": "交期变更",
    "commitment": "承诺",
    "cancellation": "取消",
    "owner_unclear": "责任人不明",
    "inventory_fact": "库存事实",
    "production_constraint": "生产约束",
    "partial_delivery": "部分交付",
    "additional_order": "另一商品订单",
    "offline_meeting": "线下洽谈",
    "clarification": "待澄清",
    "repeat_spec": "沿用上次规格",
}


@dataclass
class Outcome:
    action: dict | None = None
    note: str | None = None


def assistant_turn(db: Session, payload: AssistantTurnIn) -> dict:
    messages = _recent_messages(payload)
    if not messages or not any(item["role"] == "user" for item in messages):
        return {"reply": "先说要查看或处理哪张订单、哪段沟通。", "actions": []}
    if not get_settings().llm_api_key:
        return {"reply": NO_KEY_REPLY, "actions": []}
    snapshot = build_snapshot(db, payload)
    raw = complete_text(ASSISTANT_PROMPT, build_assistant_user(snapshot, messages))
    data = _load_object(raw)
    _merge_warehouse_intent(db, messages, data)
    return validate_actions(db, data, payload.order_id)


def build_snapshot(db: Session, payload: AssistantTurnIn) -> dict:
    rows = _order_rows(db)
    current = None
    if payload.order_id:
        match = next(((order, name) for order, name in rows if order.id == payload.order_id), None)
        if match is not None:
            current = _current_order(db, match[0], match[1])
    return {
        "route": payload.route,
        "product": (payload.product or "").strip(),
        "orders": [
            {
                "id": order.id,
                "order_no": order.order_no,
                "customer_name": name,
                "product": order.product,
                "quantity": order.quantity,
                "delivery_date": order.delivery_date,
                "status": order.status,
                "trashed": bool(order.deleted_at),
            }
            for order, name in rows
        ],
        "warehouse": _warehouse_rows(db),
        "current_order": current,
    }


def validate_actions(db: Session, data: dict, current_order_id: int | None) -> dict:
    reply = data.get("reply")
    reply = reply.strip() if isinstance(reply, str) and reply.strip() else "我没有整理出可以执行的动作。"
    incoming = data.get("actions")
    if not isinstance(incoming, list):
        incoming = []
    rows = _order_rows(db)
    kept: list[dict] = []
    notes: list[str] = []
    for item in incoming[:24]:
        if not isinstance(item, dict):
            continue
        outcome = _one(db, rows, item, current_order_id)
        if outcome.action is not None:
            kept.append(outcome.action)
        if outcome.note and outcome.note not in notes:
            notes.append(outcome.note)
    if notes:
        reply = reply + "\n" + "\n".join(notes)
    return {"reply": reply, "actions": kept}


def _load_object(raw: str) -> dict:
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
            raise ExtractError("模型没有返回可解析的 JSON。", status_code=502)
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ExtractError("模型没有返回可解析的 JSON。", status_code=502) from exc
    if not isinstance(data, dict):
        raise ExtractError("模型没有返回可解析的 JSON。", status_code=502)
    if "reply" not in data and isinstance(data.get("events"), list):
        raise ExtractError("模型返回的是抽取结果，不是对话动作。", status_code=502)
    return data


def _recent_messages(payload: AssistantTurnIn) -> list[dict]:
    rows = []
    for message in payload.messages[-8:]:
        content = message.content.strip()
        if not content:
            continue
        rows.append({"role": message.role, "content": content[:8000]})
    return rows


def _order_rows(db: Session) -> list[tuple[Order, str]]:
    return list(
        db.execute(
            select(Order, Customer.name).join(Customer, Customer.id == Order.customer_id).order_by(Order.id)
        ).all()
    )


def _current_order(db: Session, order: Order, customer_name: str) -> dict:
    events = db.scalars(
        select(BusinessEvent)
        .where(BusinessEvent.order_id == order.id, BusinessEvent.status == "draft")
        .order_by(BusinessEvent.id)
        .limit(20)
    ).all()
    suggestions = db.scalars(
        select(TaskSuggestion)
        .where(TaskSuggestion.order_id == order.id, TaskSuggestion.status == "draft")
        .order_by(TaskSuggestion.id)
        .limit(20)
    ).all()
    tasks = db.scalars(
        select(Task)
        .where(Task.order_id == order.id, Task.status.notin_(["已完成", "已关闭"]))
        .order_by(Task.id)
        .limit(20)
    ).all()
    return {
        "id": order.id,
        "order_no": order.order_no,
        "customer_name": customer_name,
        "product": order.product,
        "quantity": order.quantity,
        "delivery_date": order.delivery_date,
        "trashed": bool(order.deleted_at),
        "draft_events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "field_name": event.field_name,
                "current_value": event.current_value,
                "proposed_value": event.proposed_value,
                "proposal_origin": event.proposal_origin,
            }
            for event in events
        ],
        "draft_tasks": [
            {"id": item.id, "title": item.title, "owner": item.owner, "due_date": item.due_date, "action": item.action}
            for item in suggestions
        ],
        "open_tasks": [
            {"id": task.id, "title": task.title, "owner": task.owner, "status": task.status, "due_date": task.due_date}
            for task in tasks
        ],
    }


def _one(db: Session, rows: list[tuple[Order, str]], item: dict, current_order_id: int | None) -> Outcome:
    kind = str(item.get("type") or "").strip()
    if kind == "navigate":
        return _navigate(rows, item, current_order_id)
    if kind == "open_evidence":
        return _open_evidence(db, item)
    if kind == "create_source":
        return _create_source(rows, item, current_order_id)
    if kind == "confirm_draft":
        return _confirm(db, rows, item)
    if kind == "reject_draft":
        return _reject(db, rows, item)
    if kind == "patch_task":
        return _patch_task(db, rows, item)
    if kind == "update_supply":
        return _update_supply(db, item)
    if kind == "update_supplies":
        return _update_supplies(db, item)
    if kind == "delete_supply":
        return _delete_supply(db, item)
    return Outcome()


def _navigate(rows: list[tuple[Order, str]], item: dict, current_order_id: int | None) -> Outcome:
    route = str(item.get("route") or "").strip()
    if route not in ROUTES:
        return Outcome()
    action = {"type": "navigate", "route": route, "order_id": None, "order_no": "", "product": "", "summary": ""}
    if route == "warehouse":
        product = str(item.get("product") or "").strip()
        action["product"] = product
        action["summary"] = f"打开仓库{(' · ' + product) if product else ''}"
        return Outcome(action)
    if route == "workbench":
        action["summary"] = "打开工作台"
        return Outcome(action)
    if route == "tasks":
        action["summary"] = "打开任务看板"
        return Outcome(action)
    token = _token(item.get("order_no"))
    matched, note = _match_token(rows, token, current_order_id)
    if note:
        return Outcome(note=note)
    if len(matched) != 1:
        return Outcome(note="打开审核页需要指定一张订单。")
    order, name = matched[0]
    action["order_id"] = order.id
    action["order_no"] = order.order_no
    action["summary"] = f"打开 {order.order_no} {name} 的审核页"
    return Outcome(action)


def _open_evidence(db: Session, item: dict) -> Outcome:
    source_id = _int(item.get("source_id"))
    if source_id is None:
        return Outcome()
    source = db.get(Source, source_id)
    if source is None:
        return Outcome(note="找不到这条来源。")
    excerpt = str(item.get("excerpt") or "").strip()[:200]
    return Outcome(
        {
            "type": "open_evidence",
            "source_id": source.id,
            "excerpt": excerpt,
            "row": _int(item.get("row")),
            "col": _int(item.get("col")),
            "summary": f"打开来源「{source.title or '未命名来源'}」",
        }
    )


def _create_source(rows: list[tuple[Order, str]], item: dict, current_order_id: int | None) -> Outcome:
    text = str(item.get("text") or "").strip()
    if not text:
        return Outcome(note="录入来源需要有沟通原文。")
    token = _token(item.get("order_no"))
    matched, note = _match_token(rows, token, current_order_id)
    if note:
        return Outcome(note=note)
    if len(matched) != 1:
        return Outcome(note="录入来源需要指定一张订单。")
    order, name = matched[0]
    if order.deleted_at:
        return Outcome(note=f"{order.order_no} 在回收站中，恢复后才能录入。")
    source_type = str(item.get("source_type") or "chat").strip() or "chat"
    if source_type not in SOURCE_TYPES:
        return Outcome(note="来源类型不支持。")
    title = str(item.get("title") or "").strip() or "对话录入"
    return Outcome(
        {
            "type": "create_source",
            "order_id": order.id,
            "order_no": order.order_no,
            "customer_name": name,
            "source_type": source_type,
            "title": title[:200],
            "text": text[:8000],
            "summary": f"把这段{ _source_label(source_type) }录入 {order.order_no} {name}",
        }
    )


def _confirm(db: Session, rows: list[tuple[Order, str]], item: dict) -> Outcome:
    kind = item.get("kind")
    draft_id = _int(item.get("draft_id"))
    if kind not in {"event", "task"} or draft_id is None:
        return Outcome()
    record, order = _draft(db, kind, draft_id)
    if record is None or order is None:
        return Outcome(note="找不到这条草案。")
    mismatch = _order_token_matches(rows, item.get("order_no"), order)
    if mismatch is not None:
        return mismatch
    if record.status != "draft":
        return Outcome(note="这条草案已经处理过。")
    if order.deleted_at:
        return Outcome(note=f"{order.order_no} 在回收站中，恢复后才能确认。")
    if kind == "task":
        suggestion: TaskSuggestion = record
        return Outcome(
            {
                "type": "confirm_draft",
                "kind": "task",
                "draft_id": suggestion.id,
                "order_id": order.id,
                "order_no": order.order_no,
                "apply_to_order": False,
                "can_apply": False,
                "title": suggestion.title,
                "owner": suggestion.owner,
                "due_date": suggestion.due_date or "",
                "summary": f"确认 {order.order_no} 的任务「{suggestion.title}」",
            }
        )
    event: BusinessEvent = record
    can_apply = _can_apply(event)
    return Outcome(
        {
            "type": "confirm_draft",
            "kind": "event",
            "draft_id": event.id,
            "order_id": order.id,
            "order_no": order.order_no,
            "apply_to_order": False,
            "can_apply": can_apply,
            "field_name": event.field_name or "",
            "current_value": event.current_value or "",
            "proposed_value": event.proposed_value or "",
            "summary": _event_summary(event, order),
        }
    )


def _reject(db: Session, rows: list[tuple[Order, str]], item: dict) -> Outcome:
    kind = item.get("kind")
    draft_id = _int(item.get("draft_id"))
    if kind not in {"event", "task"} or draft_id is None:
        return Outcome()
    record, order = _draft(db, kind, draft_id)
    if record is None or order is None:
        return Outcome(note="找不到这条草案。")
    mismatch = _order_token_matches(rows, item.get("order_no"), order)
    if mismatch is not None:
        return mismatch
    if record.status != "draft":
        return Outcome(note="这条草案已经处理过。")
    reason = str(item.get("reason") or "").strip()[:200]
    label = "任务建议" if kind == "task" else "事件草案"
    summary = f"拒绝 {order.order_no} 的{label}"
    if reason:
        summary += f"：{reason}"
    return Outcome(
        {
            "type": "reject_draft",
            "kind": kind,
            "draft_id": record.id,
            "order_id": order.id,
            "order_no": order.order_no,
            "reason": reason,
            "summary": summary,
        }
    )


def _patch_task(db: Session, rows: list[tuple[Order, str]], item: dict) -> Outcome:
    task_id = _int(item.get("task_id"))
    if task_id is None:
        return Outcome()
    task = db.get(Task, task_id)
    if task is None:
        return Outcome(note="找不到这条任务。")
    if task.order_id is None:
        return Outcome(note="这张订单已经彻底删除，已完成的任务只保留记录，不能再改。")
    order = db.get(Order, task.order_id)
    if order is None:
        return Outcome(note="找不到这条任务。")
    mismatch = _order_token_matches(rows, item.get("order_no"), order)
    if mismatch is not None:
        return mismatch
    if order.deleted_at:
        return Outcome(note=f"{order.order_no} 在回收站中，恢复后才能改任务。")
    patch: dict[str, str] = {}
    status = str(item.get("status") or "").strip()
    if status:
        if status not in TASK_STATUSES:
            return Outcome(note="任务状态不合法。")
        if status != task.status:
            patch["status"] = status
    owner = item.get("owner")
    if isinstance(owner, str) and owner.strip() and owner.strip() != task.owner:
        patch["owner"] = owner.strip()
    due = item.get("due_date")
    if isinstance(due, str) and due.strip():
        parsed = parse_date(due.strip())
        if parsed is None:
            return Outcome(note="截止日期格式应为 YYYY-MM-DD。")
        if parsed != task.due_date:
            patch["due_date"] = parsed
    title = item.get("title")
    if isinstance(title, str) and title.strip() and title.strip() != task.title:
        patch["title"] = title.strip()
    if not patch:
        return Outcome()
    bits = []
    if "status" in patch:
        bits.append(f"状态 {task.status} → {patch['status']}")
    if "owner" in patch:
        bits.append(f"负责人 {task.owner or '未指定'} → {patch['owner']}")
    if "due_date" in patch:
        bits.append(f"截止 {task.due_date or '无'} → {patch['due_date']}")
    if "title" in patch:
        bits.append(f"标题改为「{patch['title']}」")
    return Outcome(
        {
            "type": "patch_task",
            "task_id": task.id,
            "order_id": order.id,
            "order_no": order.order_no,
            "summary": f"{order.order_no} 任务「{task.title}」：" + "，".join(bits),
            "before": {
                "title": task.title,
                "owner": task.owner,
                "status": task.status,
                "due_date": task.due_date,
            },
            "patch": patch,
        }
    )


WAREHOUSE_NOTE = "A公司成品仓"


def _warehouse_rows(db: Session) -> list[dict]:
    supplies = db.scalars(select(ProductSupply).order_by(ProductSupply.id)).all()
    return [
        {
            "product": supply.product,
            "on_hand": supply.on_hand,
            "earliest_completion": supply.earliest_completion,
            "note": (supply.note or "").strip()[:80],
            "referenced": bool(referencing_orders(db, supply.product)),
        }
        for supply in supplies
    ]


def _merge_warehouse_intent(db: Session, messages: list[dict], data: dict) -> None:
    text = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            text = str(message.get("content") or "")
            break
    if not _wants_warehouse_label_only(re.sub(r"\s+", "", text)):
        return
    items = []
    for supply in db.scalars(select(ProductSupply).order_by(ProductSupply.id)):
        if (supply.note or "").strip() == WAREHOUSE_NOTE:
            continue
        items.append(
            {
                "product": supply.product,
                "on_hand": supply.on_hand,
                "earliest_completion": supply.earliest_completion,
                "note": WAREHOUSE_NOTE,
            }
        )
    actions = data.get("actions")
    if not isinstance(actions, list):
        actions = []
    actions = [
        item
        for item in actions
        if not (isinstance(item, dict) and item.get("type") in {"update_supply", "update_supplies", "delete_supply"})
    ]
    if items:
        actions.append({"type": "update_supplies", "items": items})
        data["reply"] = (
            "可以，不限当前页面。这些商品都在 A公司成品仓。"
            "确认后只把商品详情改成「A公司成品仓」，在手数量、最早可完工日期和订单都不改。"
        )
    else:
        data["reply"] = "仓库里的商品详情已经只剩「A公司成品仓」，没有要改的。"
    data["actions"] = actions


def _wants_warehouse_label_only(compact: str) -> bool:
    if "仓库" not in compact or "成品仓" not in compact:
        return False
    keep = "只保留" in compact or "仅保留" in compact
    wipe = any(word in compact for word in ("删", "清空", "去掉", "清除"))
    return keep and wipe


def _update_supply(db: Session, item: dict) -> Outcome:
    payload, note = _checked_supply_item(db, item)
    if payload is None:
        return Outcome(note=note)
    wrapped = _update_supplies(db, {"items": [payload]})
    if note and wrapped.note:
        wrapped.note = note + "\n" + wrapped.note
    elif note:
        wrapped.note = note
    return wrapped


def _update_supplies(db: Session, item: dict) -> Outcome:
    raw_items = item.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return Outcome()
    kept: list[dict] = []
    notes: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        payload, note = _checked_supply_item(db, raw)
        if note and note not in notes:
            notes.append(note)
        if payload is not None:
            kept.append(payload)
    if not kept:
        return Outcome(note=notes[0] if notes else None)
    if len(kept) > 1 and all(row.get("note") == WAREHOUSE_NOTE for row in kept):
        summary = f"把 {len(kept)} 个商品的详情改为「A公司成品仓」，不改在手数量、最早可完工日期和订单。"
    elif len(kept) == 1:
        summary = f"更新仓库里的「{kept[0]['product']}」，不改订单。"
    else:
        summary = f"更新仓库里 {len(kept)} 个商品的资料，不改订单。"
    note = "\n".join(notes) if notes else None
    return Outcome({"type": "update_supplies", "items": kept, "summary": summary}, note=note)


def _checked_supply_item(db: Session, item: dict) -> tuple[dict | None, str | None]:
    product = str(item.get("product") or "").strip()
    supply = db.scalar(select(ProductSupply).where(ProductSupply.product == product))
    if supply is None:
        return None, f"仓库里没有「{product or '未命名商品'}」。"
    try:
        on_hand = int(item.get("on_hand", supply.on_hand))
    except (TypeError, ValueError):
        return None, "在手数量要是整数。"
    if on_hand < 0:
        return None, "在手数量不能为负数。"
    earliest = parse_date(str(item.get("earliest_completion", supply.earliest_completion) or "").strip())
    if earliest is None:
        return None, "最早可完工日期格式应为 YYYY-MM-DD。"
    note = str(item.get("note")).strip() if item.get("note") is not None else (supply.note or "")
    renamed = ""
    name = item.get("name")
    if isinstance(name, str) and name.strip() and name.strip() != supply.product:
        renamed = name.strip()
        if len(renamed) > 120:
            return None, "商品名称过长。"
        if referencing_orders(db, supply.product):
            return None, f"还有订单在用「{supply.product}」，不能改名称。"
        if db.scalar(select(ProductSupply).where(ProductSupply.product == renamed)) is not None:
            return None, "这个商品已经在仓库里。"
    if (
        on_hand == supply.on_hand
        and earliest == supply.earliest_completion
        and note == (supply.note or "")
        and not renamed
    ):
        return None, None
    payload = {
        "product": supply.product,
        "on_hand": on_hand,
        "earliest_completion": earliest,
        "note": note,
    }
    if renamed:
        payload["name"] = renamed
    return payload, None


def _delete_supply(db: Session, item: dict) -> Outcome:
    product = str(item.get("product") or "").strip()
    supply = db.scalar(select(ProductSupply).where(ProductSupply.product == product))
    if supply is None:
        return Outcome(note=f"仓库里没有「{product or '未命名商品'}」。")
    users = referencing_orders(db, supply.product)
    if users:
        shown = []
        for order in users[:6]:
            customer = db.get(Customer, order.customer_id)
            who = customer.name if customer else "未知客户"
            state = "回收站" if order.deleted_at else order.status
            shown.append(f"{order.order_no} {who}（{state}）")
        return Outcome(note=f"还有订单在用「{supply.product}」：{'、'.join(shown)}，不能删除。")
    return Outcome(
        {
            "type": "delete_supply",
            "product": supply.product,
            "summary": f"从仓库删除「{supply.product}」。订单里没有这个商品。",
        }
    )


def _draft(db: Session, kind: str, draft_id: int):
    if kind == "event":
        event = db.get(BusinessEvent, draft_id)
        if event is None:
            return None, None
        return event, db.get(Order, event.order_id)
    suggestion = db.get(TaskSuggestion, draft_id)
    if suggestion is None:
        return None, None
    return suggestion, db.get(Order, suggestion.order_id)


def _order_token_matches(rows: list[tuple[Order, str]], token, order: Order) -> Outcome | None:
    text = _token(token)
    if not text:
        return None
    matched, note = _match_token(rows, text, None)
    if note:
        return Outcome(note=note)
    if len(matched) != 1 or matched[0][0].id != order.id:
        return Outcome(note="草案和订单对不上。")
    return None


def _match_token(rows: list[tuple[Order, str]], token: str, current_order_id: int | None):
    if token:
        matched = _resolve(rows, token)
        if not matched:
            return [], f"找不到订单 {token}。"
        if len(matched) > 1:
            names = "、".join(order.order_no for order, _name in matched)
            return [], f"「{token}」对应多张订单：{names}。请说明是哪一张。"
        return matched, None
    if current_order_id:
        found = [pair for pair in rows if pair[0].id == current_order_id]
        if len(found) == 1:
            return found, None
    return [], None


def _resolve(rows: list[tuple[Order, str]], token: str) -> list[tuple[Order, str]]:
    folded = token.casefold()
    exact = [pair for pair in rows if pair[0].order_no.casefold() == folded]
    if exact:
        return exact
    found = []
    for pair in rows:
        order, name = pair
        fields = [order.order_no, name, order.product or ""]
        if any(_hit(field, folded) for field in fields):
            found.append(pair)
    return found


def _hit(field: str, folded: str) -> bool:
    value = field.strip().casefold()
    if not value:
        return False
    return folded == value or folded in value or value in folded


def _can_apply(event: BusinessEvent) -> bool:
    if event.event_type in {
        "commitment",
        "partial_delivery",
        "additional_order",
        "inventory_fact",
        "production_constraint",
        "offline_meeting",
        "clarification",
        "repeat_spec",
    }:
        return False
    if event.proposal_origin == "销售承诺":
        return False
    return event.field_name in {"quantity", "delivery_date"}


def _event_summary(event: BusinessEvent, order: Order) -> str:
    if event.event_type == "commitment" or event.proposal_origin == "销售承诺":
        return f"记录 {order.order_no} 的销售承诺，不改写订单"
    if event.event_type == "partial_delivery":
        return f"记录 {order.order_no} 已交付 {event.proposed_value or ''}，不改订单数量"
    if event.event_type == "inventory_fact":
        return f"确认后更新 {order.product} 的在手数量为 {event.proposed_value or ''}"
    if event.event_type == "production_constraint":
        return f"确认后更新 {order.product} 的最早可完工日期为 {event.proposed_value or ''}"
    if event.event_type == "additional_order":
        return "确认后按草案为该客户处理另一商品订单"
    if _can_apply(event):
        label = "数量" if event.field_name == "quantity" else "交期"
        return (
            f"{order.order_no} {label} {event.current_value or ''} → {event.proposed_value or ''}。"
            "勾选采纳后才会写入订单"
        )
    label = EVENT_LABELS.get(event.event_type, "事件")
    return f"记录 {order.order_no} 的{label}，不改写订单"


def _source_label(source_type: str) -> str:
    return {
        "chat": "聊天记录",
        "table": "表格",
        "call_transcript": "通话转写",
        "note": "备注",
        "document": "文件",
    }.get(source_type, "沟通")


def _token(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int(value) -> int | None:
    if isinstance(value, bool) or value is None or isinstance(value, str) and not value.strip():
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


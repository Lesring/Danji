from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, BusinessEvent, Order, Task, TaskSuggestion, now_iso
from app.services.additional import decode_additional
from app.services.supply import customer_open_order, get_product_supply, recompute_product, supply_key, write_supply
from app.util import dumps, parse_date, parse_int

OPERATOR = "演示用户"
TASK_STATUSES = {"待处理", "进行中", "已完成", "已关闭"}
ORDER_FIELDS = {"quantity", "delivery_date"}


def require_active_order(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.deleted_at:
        raise HTTPException(status_code=409, detail="订单在回收站中，恢复后才能处理")
    return order


def confirm_draft(db: Session, draft_id: int, kind: str, body: dict) -> dict:
    if kind == "event":
        return _confirm_event(db, draft_id, body)
    if kind == "task":
        return _confirm_task(db, draft_id, body)
    raise HTTPException(status_code=400, detail="kind 只能是 event 或 task")


def reject_draft(db: Session, draft_id: int, kind: str, reason: str, operator: str) -> dict:
    record = _load_draft(db, draft_id, kind)
    require_active_order(db, record.order_id)
    if record.status != "draft":
        raise HTTPException(status_code=409, detail="这条草案已经处理过")
    record.status = "rejected"
    db.add(
        AuditLog(
            object_type="business_event" if kind == "event" else "task_suggestion",
            object_id=record.id,
            action="reject",
            before_json=dumps({"status": "draft"}),
            after_json=dumps({"status": "rejected"}),
            operator=operator or OPERATOR,
            note=reason,
            created_at=now_iso(),
        )
    )
    db.commit()
    return {"id": record.id, "kind": kind, "status": "rejected"}


def update_task(db: Session, task_id: int, body: dict) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.order_id is None:
        raise HTTPException(status_code=409, detail="这张订单已经彻底删除，已完成的任务只保留记录，不能再修改。")
    require_active_order(db, task.order_id)
    before = {"status": task.status, "owner": task.owner, "due_date": task.due_date, "title": task.title}
    if "title" in body and body["title"] is not None:
        task.title = body["title"].strip() or task.title
    if "owner" in body and body["owner"] is not None:
        task.owner = body["owner"].strip()
    if "due_date" in body and body["due_date"] is not None:
        if body["due_date"] == "":
            task.due_date = None
        else:
            parsed = parse_date(body["due_date"])
            if parsed is None:
                raise HTTPException(status_code=400, detail="截止日期格式应为 YYYY-MM-DD")
            task.due_date = parsed
    if "status" in body and body["status"] is not None:
        if body["status"] not in TASK_STATUSES:
            raise HTTPException(status_code=400, detail="任务状态不合法")
        task.status = body["status"]
    if task.status == "进行中" and not task.owner.strip():
        raise HTTPException(status_code=400, detail="任务必须有负责人后才能变为进行中")
    task.updated_at = now_iso()
    db.add(
        AuditLog(
            object_type="task",
            object_id=task.id,
            action="update",
            before_json=dumps(before),
            after_json=dumps(
                {"status": task.status, "owner": task.owner, "due_date": task.due_date, "title": task.title}
            ),
            operator=body.get("operator") or OPERATOR,
            note="",
            created_at=now_iso(),
        )
    )
    db.commit()
    db.refresh(task)
    return task


def _confirm_event(db: Session, event_id: int, body: dict) -> dict:
    event = db.get(BusinessEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="事件草案不存在")
    if event.status != "draft":
        raise HTTPException(status_code=409, detail="这条草案已经处理过")
    order = require_active_order(db, event.order_id)
    apply_to_order = bool(body.get("apply_to_order"))
    operator = body.get("operator") or OPERATOR
    before_order = _order_snapshot(order)
    supply_written = False
    delivered_written = False
    audit_order = False
    affected: Order | None = None
    created_order: Order | None = None
    action = "confirm_apply" if apply_to_order else "confirm_record"
    note = "已写入订单" if apply_to_order else "只记录事件，未改写订单"
    if event.event_type == "inventory_fact":
        quantity = parse_int(body.get("proposed_value") or event.proposed_value)
        if quantity is None or quantity < 0:
            raise HTTPException(status_code=400, detail="在手数量必须是非负整数")
        event.proposed_value = str(quantity)
        write_supply(db, order, on_hand=quantity, operator=operator, source_id=event.source_id)
        supply_written = True
        action = "confirm_supply"
        note = "已更新该商品的在手数量，未改写订单"
    elif event.event_type == "production_constraint":
        earliest = parse_date(body.get("proposed_value") or event.proposed_value)
        if earliest is None:
            raise HTTPException(status_code=400, detail="最早可完工日期格式应为 YYYY-MM-DD")
        event.proposed_value = earliest
        write_supply(db, order, earliest=earliest, operator=operator, source_id=event.source_id)
        supply_written = True
        action = "confirm_supply"
        note = "已更新该商品的最早可完工日期，未改写订单"
    elif event.event_type == "partial_delivery":
        delivered = _delivered_target(order, body.get("proposed_value") or event.proposed_value)
        event.proposed_value = str(delivered)
        order.delivered_qty = delivered
        delivered_written = True
        audit_order = True
        action = "confirm_delivered"
        note = "已记录已交付数量，订单数量未改，占用按未交数量计算"
    elif event.event_type == "additional_order":
        affected, created_order, note = _confirm_additional(db, order, event, operator)
        action = "confirm_additional_order"
    elif event.event_type in {"offline_meeting", "clarification", "repeat_spec"}:
        if apply_to_order:
            raise HTTPException(status_code=400, detail="线下洽谈、待澄清规格和沿用上次规格只记待办，不能直接写入订单。")
        note = "已记录待办，未改写订单"
    elif apply_to_order:
        if event.event_type == "commitment" or event.proposal_origin == "销售承诺":
            raise HTTPException(status_code=400, detail="销售承诺只记录，不能直接改写订单。请确认对应的数量或交期变更。")
        if event.field_name not in ORDER_FIELDS:
            raise HTTPException(status_code=400, detail="该事件没有可写入订单的字段")
        proposed = body.get("proposed_value") or event.proposed_value
        if event.field_name == "quantity":
            quantity = parse_int(proposed)
            if quantity is None or quantity <= 0:
                raise HTTPException(status_code=400, detail="数量必须是正整数")
            event.proposed_value = str(quantity)
            order.quantity = quantity
        else:
            delivery = parse_date(proposed)
            if delivery is None:
                raise HTTPException(status_code=400, detail="交期格式应为 YYYY-MM-DD")
            event.proposed_value = delivery
            order.delivery_date = delivery
        audit_order = True
        action = "confirm_apply"
        note = "已写入订单"
    elif event.event_type == "commitment":
        note = "已记录销售承诺，未改写订单"
    event.status = "confirmed"
    after_order = _order_snapshot(order)
    db.add(
        AuditLog(
            object_type="order" if audit_order else "business_event",
            object_id=order.id if audit_order else event.id,
            action=action,
            before_json=dumps(before_order if audit_order else {"status": "draft"}),
            after_json=dumps(after_order if audit_order else {"status": "confirmed", "event_id": event.id}),
            operator=operator,
            note=note,
            created_at=now_iso(),
        )
    )
    recomputed: set[int] = set()

    def touch(target: Order | None) -> None:
        if target is None or target.id in recomputed:
            return
        recompute_product(db, target, event.source_id)
        recomputed.add(target.id)

    if supply_written or delivered_written or (audit_order and event.field_name == "quantity"):
        touch(order)
    if event.event_type == "additional_order":
        touch(affected)
    db.commit()
    result = {"id": event.id, "kind": "event", "status": "confirmed", "order": after_order}
    if created_order is not None:
        result["created_order_id"] = created_order.id
        result["created_order_no"] = created_order.order_no
    return result


def _order_snapshot(order: Order) -> dict:
    return {
        "quantity": order.quantity,
        "delivery_date": order.delivery_date,
        "status": order.status,
        "delivered_qty": order.delivered_qty or 0,
    }


def _delivered_target(order: Order, raw: str | None) -> int:
    text = str(raw or "").strip()
    if text in {"一半", "半", "half", "50%"} or "一半" in text:
        return order.quantity // 2
    value = parse_int(text)
    if value is None or value < 0:
        raise HTTPException(status_code=400, detail="已交付数量必须是非负整数，或「一半」")
    if value > order.quantity:
        raise HTTPException(status_code=400, detail="已交付数量不能超过订单数量")
    return value


def _apply_spec_to_order(order: Order, spec: dict) -> None:
    order.quantity = int(spec["quantity"])
    if spec.get("delivery_date"):
        order.delivery_date = spec["delivery_date"]


def _confirm_additional(db: Session, order: Order, event: BusinessEvent, operator: str) -> tuple[Order | None, Order | None, str]:
    spec = decode_additional(event.proposed_value)
    if spec is None:
        raise HTTPException(status_code=400, detail="新商品订单草案不完整")
    event.proposed_value = dumps(spec)
    if (spec["product"] or "").strip() == (order.product or "").strip():
        _apply_spec_to_order(order, spec)
        return order, None, "当前订单的商品不重复新建，已把变更写到这张订单"
    existing = customer_open_order(db, order.customer_id, spec["product"])
    if existing is not None:
        _apply_spec_to_order(existing, spec)
        return existing, None, f"该客户已有商品订单 {existing.order_no}，未重复新建"
    delivery = spec["delivery_date"] or order.delivery_date or date.today().isoformat()
    from app.services.records import create_suggested_order

    created = create_suggested_order(
        db,
        customer_id=order.customer_id,
        product=spec["product"],
        quantity=int(spec["quantity"]),
        delivery_date=delivery,
        owner=order.owner,
        on_hand=spec["on_hand"],
        earliest=spec["earliest_completion"] or None,
        operator=operator,
        source_id=event.source_id,
    )
    return created, created, f"已为该客户新增商品订单 {created.order_no}"


def _confirm_task(db: Session, suggestion_id: int, body: dict) -> dict:
    suggestion = db.get(TaskSuggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="任务建议不存在")
    if suggestion.status != "draft":
        raise HTTPException(status_code=409, detail="这条草案已经处理过")
    order = require_active_order(db, suggestion.order_id)
    owner = suggestion.owner if body.get("owner") is None else str(body.get("owner")).strip()
    title = (body.get("title") or suggestion.title).strip()
    due_raw = suggestion.due_date if body.get("due_date") is None else body.get("due_date")
    due_date = None
    if due_raw:
        due_date = parse_date(str(due_raw))
        if due_date is None:
            raise HTTPException(status_code=400, detail="截止日期格式应为 YYYY-MM-DD")
    before_order = {"status": order.status}
    task: Task | None = None
    if suggestion.action == "close":
        task = db.get(Task, suggestion.target_task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="要关闭的任务不存在")
        task.status = "已关闭"
        task.updated_at = now_iso()
    elif suggestion.action == "update":
        task = db.get(Task, suggestion.target_task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="要更新的任务不存在")
        task.title = title
        task.owner = owner
        task.due_date = due_date
        task.reason = suggestion.reason
        task.event_ids_json = suggestion.event_ids_json
        task.source_id = suggestion.source_id
        task.updated_at = now_iso()
    else:
        task = Task(
            order_id=order.id,
            order_no=order.order_no,
            suggestion_key=suggestion.suggestion_key,
            title=title,
            owner=owner,
            due_date=due_date,
            status="待处理",
            reason=suggestion.reason,
            event_ids_json=suggestion.event_ids_json,
            source_id=suggestion.source_id,
            suggestion_id=suggestion.id,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        db.add(task)
        db.flush()
    if task.status == "进行中" and not task.owner.strip():
        raise HTTPException(status_code=400, detail="任务必须有负责人后才能变为进行中")
    if suggestion.order_status and suggestion.order_status != before_order["status"]:
        order.status = suggestion.order_status
        recompute_product(db, order, suggestion.source_id)
    elif suggestion.order_status:
        order.status = suggestion.order_status
    suggestion.status = "confirmed"
    suggestion.title = title
    suggestion.owner = owner
    suggestion.due_date = due_date
    db.add(
        AuditLog(
            object_type="task",
            object_id=task.id,
            action=suggestion.action,
            before_json=dumps({"order": before_order, "suggestion_id": suggestion.id}),
            after_json=dumps(
                {
                    "task_id": task.id,
                    "title": task.title,
                    "owner": task.owner,
                    "status": task.status,
                    "order_status": order.status,
                }
            ),
            operator=body.get("operator") or OPERATOR,
            note=suggestion.reason,
            created_at=now_iso(),
        )
    )
    db.commit()
    return {"id": suggestion.id, "kind": "task", "status": "confirmed", "task_id": task.id, "order_status": order.status}


def _load_draft(db: Session, draft_id: int, kind: str):
    if kind == "event":
        record = db.get(BusinessEvent, draft_id)
        if record is None:
            raise HTTPException(status_code=404, detail="事件草案不存在")
        return record
    if kind == "task":
        record = db.get(TaskSuggestion, draft_id)
        if record is None:
            raise HTTPException(status_code=404, detail="任务建议不存在")
        return record
    raise HTTPException(status_code=400, detail="kind 只能是 event 或 task")


def order_audit_entries(db: Session, order_id: int) -> list[AuditLog]:
    event_ids = list(db.scalars(select(BusinessEvent.id).where(BusinessEvent.order_id == order_id)))
    task_ids = list(db.scalars(select(Task.id).where(Task.order_id == order_id)))
    suggestion_ids = list(db.scalars(select(TaskSuggestion.id).where(TaskSuggestion.order_id == order_id)))
    order = db.get(Order, order_id)
    supply = get_product_supply(db, supply_key(order)) if order is not None else None
    rows = list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc())))
    matched = []
    for row in rows:
        if supply is not None and row.object_type == "product_supply" and row.object_id == supply.id:
            matched.append(row)
        elif row.object_type == "order" and row.object_id == order_id:
            matched.append(row)
        elif row.object_type == "business_event" and row.object_id in event_ids:
            matched.append(row)
        elif row.object_type == "task" and row.object_id in task_ids:
            matched.append(row)
        elif row.object_type == "task_suggestion" and row.object_id in suggestion_ids:
            matched.append(row)
    return matched


def first_evidence(db: Session, event_ids: list[int]) -> tuple[int | None, str]:
    if not event_ids:
        return None, ""
    event = db.get(BusinessEvent, event_ids[0])
    if event is None:
        return None, ""
    return event.source_id, event.evidence_excerpt

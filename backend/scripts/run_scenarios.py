"""Run four order scenarios against a fresh database.

From the backend directory:

    python scripts/run_scenarios.py fixture
    python scripts/run_scenarios.py llm
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CASES = BACKEND / "cases"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

MODE = sys.argv[1] if len(sys.argv) > 1 else "fixture"
if MODE not in {"fixture", "llm"}:
    raise SystemExit("mode 只能是 fixture 或 llm")

os.environ["DANJI_DATABASE"] = str(Path(tempfile.mkdtemp(prefix="danji-cases-")) / "cases.db")
os.environ["EXTRACT_MODE"] = MODE

from fastapi.testclient import TestClient  # noqa: E402

from app.db import open_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Order, SupplyConstraint  # noqa: E402

DONE = {"已完成", "已交付", "已关闭"}
OPEN_TASK = {"待处理", "进行中"}


def load_orders() -> list[dict]:
    return json.loads((CASES / "orders.json").read_text(encoding="utf-8"))


def seed_cases() -> None:
    db = open_session()
    try:
        for item in load_orders():
            customer = Customer(
                name=item["customer_name"],
                contact=item.get("contact", ""),
                note=item.get("note", ""),
            )
            db.add(customer)
            db.flush()
            order = Order(
                order_no=item["order_no"],
                customer_id=customer.id,
                product=item["product"],
                quantity=item["quantity"],
                delivery_date=item["delivery_date"],
                status=item["status"],
                owner=item["owner"],
            )
            db.add(order)
            db.flush()
            db.add(
                SupplyConstraint(
                    order_id=order.id,
                    available_qty=item["available_qty"],
                    earliest_completion=item["earliest_completion"],
                    note=item.get("supply_note", ""),
                )
            )
        db.commit()
    finally:
        db.close()


def order_by_no(client: TestClient, order_no: str) -> dict:
    return next(item for item in client.get("/orders").json() if item["order_no"] == order_no)


def detail(client: TestClient, order_id: int) -> dict:
    return client.get(f"/orders/{order_id}").json()


def post_text(client: TestClient, order_id: int, source_type: str, title: str, text: str, business_time: str) -> dict:
    response = client.post(
        f"/orders/{order_id}/sources",
        json={"type": source_type, "title": title, "text": text, "business_time": business_time},
    )
    return {"status": response.status_code, "body": _body(response)}


def post_file(client: TestClient, order_id: int, source_type: str, title: str, filename: str, business_time: str) -> dict:
    payload = (CASES / filename).read_bytes()
    response = client.post(
        f"/orders/{order_id}/sources",
        data={"type": source_type, "title": title, "business_time": business_time},
        files={"file": (filename, payload, "text/plain")},
    )
    return {"status": response.status_code, "body": _body(response)}


def _body(response) -> dict | str:
    try:
        return response.json()
    except Exception:
        return response.text


def brief_events(body: dict) -> list[dict]:
    if not isinstance(body, dict):
        return []
    rows = []
    for event in body.get("events") or []:
        rows.append(
            {
                "id": event["id"],
                "type": event["event_type"],
                "field": event.get("field_name"),
                "current": event.get("current_value"),
                "proposed": event.get("proposed_value"),
                "origin": event.get("proposal_origin"),
                "evidence": event.get("evidence_excerpt"),
                "source_type": event.get("source_type"),
            }
        )
    return rows


def brief_tasks(body: dict) -> list[dict]:
    if not isinstance(body, dict):
        return []
    rows = []
    for item in body.get("task_suggestions") or []:
        rows.append(
            {
                "id": item["id"],
                "key": item["suggestion_key"],
                "title": item["title"],
                "owner": item["owner"],
                "due": item["due_date"],
                "action": item["action"],
                "reason": item["reason"],
            }
        )
    return rows


def confirm_tasks(client: TestClient, suggestions: list[dict]) -> list[dict]:
    created = []
    for item in suggestions:
        response = client.post(f"/drafts/{item['id']}/confirm", json={"kind": "task"})
        created.append({"title": item["title"], "status": response.status_code, "body": _body(response)})
    return created


def reminder_gaps(client: TestClient) -> list[dict]:
    orders = client.get("/orders").json()
    tasks = client.get("/tasks").json()
    gaps = []
    for order in orders:
        if order["status"] in DONE:
            continue
        open_tasks = [
            task["title"]
            for task in tasks
            if task["order_id"] == order["id"] and task["status"] in OPEN_TASK
        ]
        if order["pending_count"] == 0 and not open_tasks:
            gaps.append(
                {
                    "order_no": order["order_no"],
                    "status": order["status"],
                    "pending_count": order["pending_count"],
                    "open_tasks": open_tasks,
                }
            )
    return gaps


def main() -> None:
    seed_cases()
    report: dict = {"mode": MODE, "cases": {}}
    with TestClient(app) as client:
        report["health"] = client.get("/health").json()
        report["before_reminders"] = {
            "open_orders": [
                {"order_no": item["order_no"], "status": item["status"], "pending_count": item["pending_count"]}
                for item in client.get("/orders").json()
                if item["status"] not in DONE
            ],
            "gaps": reminder_gaps(client),
        }

        order = order_by_no(client, "ORD-301")
        uploaded = post_file(client, order["id"], "chat", "签收结案", "01-ord301-chat.txt", "2026-11-12T09:16:00")
        after = detail(client, order["id"])
        evidence = None
        if isinstance(uploaded["body"], dict) and uploaded["body"].get("source_id"):
            evidence = client.get(f"/evidence/{uploaded['body']['source_id']}").json()
        report["cases"]["1_normal_delivery"] = {
            "upload_status": uploaded["status"],
            "file_name": None if not isinstance(evidence, dict) else evidence.get("file_name"),
            "source_type": None if not isinstance(evidence, dict) else evidence.get("type"),
            "events": brief_events(uploaded["body"] if isinstance(uploaded["body"], dict) else {}),
            "tasks": brief_tasks(uploaded["body"] if isinstance(uploaded["body"], dict) else {}),
            "error": uploaded["body"] if uploaded["status"] != 200 else None,
            "order_after": {
                "quantity": after["order"]["quantity"],
                "delivery_date": after["order"]["delivery_date"],
                "status": after["order"]["status"],
                "pending_count": after["order"]["pending_count"],
            },
            "passed": after["order"]["status"] == "已完成" and uploaded["status"] == 200,
        }

        order = order_by_no(client, "ORD-302")
        turns = []
        specs = [
            ("02-ord302-turn1.txt", "第一次改交期", "2026-10-22T09:02:00"),
            ("02-ord302-turn2.txt", "第二次改数量", "2026-10-22T14:36:00"),
            ("02-ord302-turn3.txt", "第三次改数量和交期", "2026-10-23T10:15:00"),
        ]
        latest = {}
        for filename, title, business_time in specs:
            text = (CASES / filename).read_text(encoding="utf-8")
            result = post_text(client, order["id"], "chat", title, text, business_time)
            latest = result["body"] if isinstance(result["body"], dict) else {}
            turns.append(
                {
                    "title": title,
                    "status": result["status"],
                    "events": brief_events(latest),
                    "tasks": brief_tasks(latest),
                    "risks": [
                        {"code": item["code"], "message": item["message"]}
                        for item in (latest.get("risks") or [])
                    ]
                    if isinstance(latest, dict)
                    else [],
                    "error": result["body"] if result["status"] != 200 else None,
                }
            )
        quantities = {item["proposed"] for turn in turns for item in turn["events"] if item["field"] == "quantity"}
        dates = {item["proposed"] for turn in turns for item in turn["events"] if item["field"] == "delivery_date"}
        task_titles = [item["title"] for item in brief_tasks(latest)]
        report["cases"]["2_repeated_changes"] = {
            "turns": turns,
            "quantities_seen": sorted(value for value in quantities if value),
            "dates_seen": sorted(value for value in dates if value),
            "latest_tasks": task_titles,
            "passed": bool(task_titles) and {"180", "240"} <= quantities and "2026-12-02" in dates and "2026-11-20" in dates,
        }
        report["cases"]["2_confirmed_tasks"] = confirm_tasks(client, brief_tasks(latest))
        report["cases"]["2_board"] = [
            {"order_no": item["order_no"], "title": item["title"], "owner": item["owner"], "status": item["status"], "due": item["due_date"]}
            for item in client.get("/tasks").json()
            if item["order_no"] == "ORD-302"
        ]

        order = order_by_no(client, "ORD-303")
        chat = post_text(
            client,
            order["id"],
            "chat",
            "按原订单",
            (CASES / "03-ord303-chat.txt").read_text(encoding="utf-8"),
            "2026-10-20T11:02:00",
        )
        call = post_text(
            client,
            order["id"],
            "call_transcript",
            "电话改单",
            (CASES / "03-ord303-call.txt").read_text(encoding="utf-8"),
            "2026-10-24T16:40:00",
        )
        before_apply = detail(client, order["id"])["order"]
        call_body = call["body"] if isinstance(call["body"], dict) else {}
        applied = []
        for event in brief_events(call_body):
            if event["type"] in {"quantity_change", "delivery_change"}:
                response = client.post(
                    f"/drafts/{event['id']}/confirm",
                    json={"kind": "event", "apply_to_order": True},
                )
                applied.append({"field": event["field"], "proposed": event["proposed"], "status": response.status_code, "body": _body(response)})
        after_apply = detail(client, order["id"])["order"]
        call_events = brief_events(call_body)
        report["cases"]["3_phone_change"] = {
            "chat_status": chat["status"],
            "chat_events": brief_events(chat["body"] if isinstance(chat["body"], dict) else {}),
            "call_status": call["status"],
            "call_error": call["body"] if call["status"] != 200 else None,
            "call_events": call_events,
            "call_tasks": brief_tasks(call_body),
            "call_risks": [
                {"code": item["code"], "message": item["message"]} for item in (call_body.get("risks") or [])
            ],
            "order_before_confirm": {
                "quantity": before_apply["quantity"],
                "delivery_date": before_apply["delivery_date"],
                "status": before_apply["status"],
            },
            "applied": applied,
            "order_after_confirm": {
                "quantity": after_apply["quantity"],
                "delivery_date": after_apply["delivery_date"],
                "status": after_apply["status"],
            },
            "passed": any(item["proposed"] == "95" and item["field"] == "quantity" for item in call_events)
            and any(item["proposed"] == "2026-11-12" and item["field"] == "delivery_date" for item in call_events)
            and before_apply["quantity"] == 60
            and after_apply["quantity"] == 95
            and after_apply["delivery_date"] == "2026-11-12",
        }

        report["after_reminders"] = {
            "orders": [
                {"order_no": item["order_no"], "status": item["status"], "pending_count": item["pending_count"]}
                for item in client.get("/orders").json()
            ],
            "open_tasks": [
                {"order_no": item["order_no"], "title": item["title"], "status": item["status"]}
                for item in client.get("/tasks").json()
                if item["status"] in OPEN_TASK
            ],
            "gaps": reminder_gaps(client),
        }
        report["cases"]["4_reminders"] = {
            "passed": report["after_reminders"]["gaps"] == [] and any(
                item["status"] not in DONE for item in report["after_reminders"]["orders"]
            ),
            "gaps": report["after_reminders"]["gaps"],
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

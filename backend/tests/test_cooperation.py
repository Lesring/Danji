import json
from pathlib import Path

from app.fixtures import load_demo
from app.llm.prompts import SYSTEM_PROMPT
from app.services.fixture_extract import extract_fixture_events, merge_cooperation
from app.services.rules import EventIn, analyze, OrderSnap

ROOT = Path(__file__).resolve().parents[2] / "测试素材" / "D-三类洽谈"


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8").strip()


def _sample(sample_id: str) -> str:
    return next(item["text"] for item in load_demo()["samples"] if item["id"] == sample_id).strip()


def _order(client, order_no: str) -> dict:
    return next(item for item in client.get("/orders").json() if item["order_no"] == order_no)


def test_chat_files_match_demo_buttons():
    assert _sample("ord018-meeting") == _text("01-线下洽谈-聊天记录.txt")
    assert _sample("ord018-repeat") == _text("03-沿用上次规格-有历史.txt")


def test_prompt_requests_meeting_clarification_and_repeat():
    assert "offline_meeting" in SYSTEM_PROMPT
    assert "clarification" in SYSTEM_PROMPT
    assert "repeat_spec" in SYSTEM_PROMPT
    assert "customer_orders" in SYSTEM_PROMPT
    assert "不要编造型号" in SYSTEM_PROMPT
    assert "不要猜规格" in SYSTEM_PROMPT


def test_meeting_becomes_its_own_task_and_does_not_change_the_order(client):
    order = _order(client, "ORD-018")
    response = client.post(
        f"/orders/{order['id']}/sources",
        json={"type": "chat", "title": "线下当面洽谈", "text": _text("01-线下洽谈-聊天记录.txt")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    meeting = next(item for item in body["events"] if item["event_type"] == "offline_meeting")
    assert meeting["status"] == "draft"
    assert "当面谈" in meeting["evidence_excerpt"]
    task = next(item for item in body["task_suggestions"] if item["suggestion_key"] == "offline_meeting")
    assert "采购周宁" in task["title"]
    assert "2026-11-03" in task["title"]
    assert "星期二" in task["title"]
    assert "厂区会议室" in task["title"]
    assert "框架断路器备货" in task["title"]
    assert "销售林晓" in task["owner"]
    assert task["due_date"] == "2026-11-03"
    assert task["status"] == "draft"
    detail = client.get(f"/orders/{order['id']}").json()
    assert detail["order"]["quantity"] == 100
    assert detail["order"]["delivery_date"] == "2026-11-10"
    blocked = client.post(
        f"/drafts/{meeting['id']}/confirm",
        json={"kind": "event", "apply_to_order": True},
    )
    assert blocked.status_code == 400
    kept = client.get(f"/orders/{order['id']}").json()
    assert kept["order"]["quantity"] == 100
    assert kept["order"]["delivery_date"] == "2026-11-10"


def test_vague_request_without_history_asks_and_does_not_invent(client):
    created = client.post("/customers", json={"name": "澄江新客", "contact": "C老板", "note": "电话 13800001007"})
    assert created.status_code == 200, created.text
    customer_id = created.json()["customer"]["id"]
    assert created.json()["customer"]["order_count"] == 0
    response = client.post(
        f"/customers/{customer_id}/intake",
        json={"title": "规格未说清", "text": _text("02-规格未说清-聊天记录.txt")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    event = next(item for item in body["events"] if item["event_type"] == "clarification")
    assert event["proposed_value"] == "规格、数量、要求交期"
    assert not any(char.isdigit() for char in (event["proposed_value"] or ""))
    spoken = f"{event['proposed_value']} {event['explanation']}"
    for banned in ("NXB", "NM1", "小型断路器", "塑壳断路器", "2026-"):
        assert banned not in spoken
    task = next(item for item in body["task_suggestions"] if item["suggestion_key"] == "clarify_before_order")
    assert "规格" in task["title"] and "数量" in task["title"] and "要求交期" in task["title"]
    confirmed = client.post(f"/customers/{customer_id}/intake/{body['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert client.get("/orders").json()
    assert all(item["customer_id"] != customer_id for item in client.get("/orders").json())
    tasks = client.get("/tasks").json()
    assert any(item["order_id"] is None and "规格" in item["title"] for item in tasks)


def test_repeat_uses_only_this_customers_last_order(client):
    order = _order(client, "ORD-018")
    response = client.post(
        f"/orders/{order['id']}/sources",
        json={"type": "chat", "title": "沿用上次规格", "text": _text("03-沿用上次规格-有历史.txt")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    event = next(item for item in body["events"] if item["event_type"] == "repeat_spec")
    assert event["current_value"] == "ORD-018"
    assert event["proposed_value"] == "小型断路器"
    assert "ORD-018" in event["explanation"]
    assert "40" in event["explanation"]
    assert "2026-12-02" in event["explanation"]
    assert "确认前不新建" in event["explanation"]
    task = next(item for item in body["task_suggestions"] if item["suggestion_key"] == "confirm_repeat_spec")
    assert "ORD-018" in task["title"]
    assert "小型断路器" in task["title"]
    detail = client.get(f"/orders/{order['id']}").json()
    assert detail["order"]["quantity"] == 100
    assert detail["order"]["delivery_date"] == "2026-11-10"
    assert len([item for item in client.get("/orders").json() if item["customer_id"] == order["customer_id"]]) == 1
    blocked = client.post(f"/drafts/{event['id']}/confirm", json={"kind": "event", "apply_to_order": True})
    assert blocked.status_code == 400
    again = client.get(f"/orders/{order['id']}").json()
    assert again["order"]["quantity"] == 100


def test_repeat_without_history_does_not_guess_a_spec(client):
    created = client.post("/customers", json={"name": "澄江新客", "contact": "C老板"})
    customer_id = created.json()["customer"]["id"]
    response = client.post(
        f"/customers/{customer_id}/intake",
        json={"text": _text("03-沿用上次规格-无历史.txt")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    event = next(item for item in body["events"] if item["event_type"] == "clarification")
    assert event["proposed_value"] == "规格"
    assert "40" in event["explanation"]
    assert "2026-12-02" in event["explanation"]
    blob = json.dumps(body, ensure_ascii=False)
    for banned in ("小型断路器", "塑壳断路器", "NXB", "NM1", "ORD-018"):
        assert banned not in blob
    assert all(item["event_type"] != "repeat_spec" for item in body["events"])
    assert "order" not in created.json()


def test_preview_order_sheet_keeps_every_row_and_can_join_a_chat(client):
    folder = ROOT.parent / "B-订货表缺客户名"
    sheet = folder / "订货表-仅采购明细.xlsx"
    chat = folder / "补全客户身份-聊天记录.txt"
    alone = client.post(
        "/customers/preview/file",
        files={"file": (sheet.name, sheet.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert alone.status_code == 200, alone.text
    body = alone.json()
    assert body["name"] == ""
    assert body["name"] != "物料"
    assert len(body["orders"]) == 5
    assert body["orders"][0]["product"] == "塑壳断路器 250A 3P"
    assert body["orders"][0]["quantity"] == 40
    assert body["orders"][0]["delivery_date"] == "2026-11-20"
    assert body["orders"][4]["product"] == "电能表 单相"
    assert body["orders"][4]["delivery_date"] == "2026-12-01"
    joined = client.post(
        "/customers/preview/file",
        files=[
            ("files", (sheet.name, sheet.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("files", (chat.name, chat.read_bytes(), "text/plain")),
        ],
    )
    assert joined.status_code == 200, joined.text
    merged = joined.json()
    assert merged["name"] == "南浦配电"
    assert merged["contact"] == "计划陈工"
    assert "13800001002" in merged["note"]
    assert len(merged["orders"]) == 5
    assert "客户名称" not in merged["missing"]


def test_create_customer_from_several_sheet_rows(client):
    response = client.post(
        "/customers",
        json={
            "name": "南浦配电",
            "contact": "计划陈工",
            "orders": [
                {
                    "order_no": "ORD-501",
                    "product": "塑壳断路器 250A 3P",
                    "quantity": 40,
                    "delivery_date": "2026-11-20",
                    "available_qty": 200,
                    "earliest_completion": "2026-11-15",
                },
                {
                    "order_no": "ORD-502",
                    "product": "漏电断路器 63A 2P",
                    "quantity": 24,
                    "delivery_date": "2026-11-25",
                    "available_qty": 200,
                    "earliest_completion": "2026-11-04",
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["customer"]["order_count"] == 2
    assert [item["order_no"] for item in body["orders"]] == ["ORD-501", "ORD-502"]
    listed = client.get("/orders").json()
    assert {item["order_no"] for item in listed} >= {"ORD-501", "ORD-502"}


def test_preview_file_uses_the_same_fields_as_pasted_text(client):
    path = ROOT.parent / "A-完整协作三阶段" / "01-询价接洽-聊天记录.txt"
    response = client.post(
        "/customers/preview/file",
        files={"file": (path.name, path.read_bytes(), "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "青禾成套"
    assert body["order_no"] == "ORD-418"
    assert body["quantity"] == 80
    assert body["missing"] == []


def test_preview_file_rejects_unsupported_type(client):
    response = client.post(
        "/customers/preview/file",
        files={"file": ("notes.bin", b"not a supported document", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "只支持" in response.json()["detail"]


def test_preview_vague_chat_asks_for_the_missing_required_fields(client):
    response = client.post("/customers/preview", json={"text": _text("02-规格未说清-聊天记录.txt")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "澄江新客"
    assert body["contact"] == "C老板"
    assert "13800001007" in body["note"]
    assert body["product"] == "断路器"
    assert body["quantity"] is None
    assert body["order_no"] == ""
    assert body["delivery_date"] == ""
    assert body["on_hand"] is None
    assert body["earliest_completion"] == ""
    assert body["missing"] == ["订单号", "数量", "订单交期", "在手数量", "最早可完工日期"]
    blob = json.dumps(body, ensure_ascii=False)
    for banned in ("NXB", "NM1", "小型断路器", "2026-11"):
        assert banned not in blob


def test_preview_cooperation_chat_keeps_written_fields(client):
    text = (ROOT.parent / "A-完整协作三阶段" / "01-询价接洽-聊天记录.txt").read_text(encoding="utf-8")
    response = client.post("/customers/preview", json={"text": text})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "青禾成套"
    assert body["contact"] == "采购周宁"
    assert "13800001001" in body["note"]
    assert body["order_no"] == "ORD-418"
    assert body["product"] == "小型断路器"
    assert body["quantity"] == 80
    assert body["delivery_date"] == "2026-12-08"
    assert body["on_hand"] == 120
    assert body["earliest_completion"] == "2026-11-08"
    assert body["missing"] == []


def test_preview_each_stage_of_a_is_complete(client):
    folder = ROOT.parent / "A-完整协作三阶段"
    for name in ("01-询价接洽-聊天记录.txt", "02-确认下单-聊天记录.txt", "03-交付结案-聊天记录.txt"):
        response = client.post("/customers/preview", json={"text": (folder / name).read_text(encoding="utf-8")})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["missing"] == [], name
        assert body["name"] == "青禾成套"
        assert body["order_no"] == "ORD-418"
        assert body["product"] == "小型断路器"
        assert body["quantity"] == 80
        assert body["delivery_date"] == "2026-12-08"
        assert body["on_hand"] == 120
        assert body["earliest_completion"] == "2026-11-08"


def test_preview_drops_fields_the_model_invented(client, monkeypatch):
    monkeypatch.setenv("EXTRACT_MODE", "llm")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    def fake(system, user, **kwargs):
        assert "不要编造" in system
        return json.dumps(
            {
                "name": "并不存在的公司",
                "contact": "",
                "note": "",
                "order_no": "ORD-999",
                "product": "NXB-63",
                "quantity": "150",
                "delivery_date": "2026-11-06",
                "on_hand": "120",
                "earliest_completion": "2026-11-08",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.customer_profile.complete_text", fake)
    response = client.post("/customers/preview", json={"text": _text("02-规格未说清-聊天记录.txt")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "澄江新客"
    assert body["product"] == "断路器"
    assert body["quantity"] is None
    assert body["order_no"] == ""
    assert body["delivery_date"] == ""
    assert body["on_hand"] is None
    assert "2026-11-06" not in json.dumps(body, ensure_ascii=False)


def test_merge_drops_invented_model_on_a_vague_chat():
    text = _text("02-规格未说清-聊天记录.txt")
    merged = merge_cooperation(
        [
            EventIn(
                "quantity_change",
                field_name="quantity",
                current_value="0",
                proposed_value="10",
                evidence_excerpt="要一批断路器",
                explanation="猜了 10 件",
            ),
            EventIn(
                "repeat_spec",
                proposed_value="NXB-63",
                evidence_excerpt="要一批断路器",
                explanation="猜成 NXB-63",
            ),
        ],
        text,
        [],
    )
    blob = " ".join(f"{item.event_type} {item.proposed_value or ''} {item.explanation}" for item in merged)
    assert "NXB" not in blob
    assert all(item.event_type != "quantity_change" for item in merged)
    assert any(item.event_type == "clarification" and item.proposed_value == "规格、数量、要求交期" for item in merged)


def test_merge_repeat_keeps_history_product_and_drops_a_guess():
    text = _text("03-沿用上次规格-有历史.txt")
    history = [
        {"order_no": "ORD-019", "product": "塑壳断路器", "quantity": 80, "delivery_date": "2026-11-20"},
        {"order_no": "ORD-018", "product": "小型断路器", "quantity": 100, "delivery_date": "2026-11-10"},
    ]
    merged = merge_cooperation(
        [
            EventIn("additional_order", proposed_value="NM1", evidence_excerpt="像上次一样", explanation="猜成 NM1"),
            EventIn(
                "quantity_change",
                field_name="quantity",
                current_value="100",
                proposed_value="40",
                evidence_excerpt="再来 40 件",
            ),
        ],
        text,
        history,
    )
    event = next(item for item in merged if item.event_type == "repeat_spec")
    assert event.current_value == "ORD-018"
    assert event.proposed_value == "小型断路器"
    assert all(item.event_type not in {"additional_order", "quantity_change"} for item in merged)
    assert all("NM1" not in (item.proposed_value or "") for item in merged)


def test_fixture_meeting_task_carries_who_when_and_what():
    events = extract_fixture_events(
        _text("01-线下洽谈-聊天记录.txt"),
        100,
        "2026-11-10",
        120,
        "2026-11-08",
        customer_orders=[{"order_no": "ORD-018", "product": "小型断路器"}],
    )
    meeting = next(item for item in events if item.event_type == "offline_meeting")
    result = analyze(
        OrderSnap(100, "2026-11-10", "已确认", 120, "2026-11-08"),
        events,
        [],
        "2026-10-24",
    )
    task = next(item for item in result.suggestions if item.suggestion_key == "offline_meeting")
    assert meeting.proposed_value == "2026-11-03"
    assert task.due_date == "2026-11-03"
    assert "采购周宁" in task.title and "销售林晓" in task.owner

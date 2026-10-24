import json

from app.fixtures import load_demo


def _order(client, order_no: str) -> dict:
    orders = client.get("/orders").json()
    return next(item for item in orders if item["order_no"] == order_no)


def _sample(sample_id: str) -> dict:
    return next(item for item in load_demo()["samples"] if item["id"] == sample_id)


def _post_sample(client, sample_id: str):
    sample = _sample(sample_id)
    order = _order(client, sample["order_no"])
    response = client.post(
        f"/orders/{order['id']}/sources",
        json={
            "type": sample["type"],
            "title": sample["title"],
            "text": sample["text"],
            "business_time": sample["business_time"],
        },
    )
    return order, response


def test_main_case_keeps_order_until_confirmation(client):
    order, response = _post_sample(client, "ord018-conflict")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["risks"]) >= 2
    assert len(body["task_suggestions"]) == 3
    detail = client.get(f"/orders/{order['id']}").json()
    assert detail["order"]["quantity"] == 100
    assert detail["order"]["delivery_date"] == "2026-11-10"
    assert any(item["event_type"] == "commitment" for item in body["events"])
    assert any("120" in item["message"] and "2026-11-08" in item["message"] or "120" in item["message"] for item in body["risks"])
    assert any("2026-11-08" in item["message"] for item in body["risks"])


def test_commitment_record_does_not_change_delivery_until_field_is_adopted(client):
    order, response = _post_sample(client, "ord018-conflict")
    events = response.json()["events"]
    commitment = next(item for item in events if item["event_type"] == "commitment")
    delivery = next(item for item in events if item["event_type"] == "delivery_change")

    rejected = client.post(
        f"/drafts/{commitment['id']}/confirm",
        json={"kind": "event", "apply_to_order": True},
    )
    assert rejected.status_code == 400
    unchanged = client.get(f"/orders/{order['id']}").json()["order"]
    assert unchanged["delivery_date"] == "2026-11-10"
    assert unchanged["quantity"] == 100

    recorded = client.post(f"/drafts/{commitment['id']}/confirm", json={"kind": "event", "apply_to_order": False})
    assert recorded.status_code == 200
    still = client.get(f"/orders/{order['id']}").json()["order"]
    assert still["delivery_date"] == "2026-11-10"

    adopted = client.post(
        f"/drafts/{delivery['id']}/confirm",
        json={"kind": "event", "apply_to_order": True},
    )
    assert adopted.status_code == 200
    changed = client.get(f"/orders/{order['id']}").json()
    assert changed["order"]["delivery_date"] == "2026-11-06"
    assert changed["order"]["quantity"] == 100
    assert any(item["before"]["delivery_date"] == "2026-11-10" for item in changed["audits"] if isinstance(item["before"], dict) and "delivery_date" in item["before"])


def test_rejected_task_suggestion_is_not_on_the_board(client):
    order, response = _post_sample(client, "ord018-conflict")
    suggestion = response.json()["task_suggestions"][0]
    before = client.get("/tasks", params={"order_id": order["id"]}).json()
    assert any(item["kind"] == "suggestion" and item["suggestion_id"] == suggestion["id"] for item in before)
    rejected = client.post(
        f"/drafts/{suggestion['id']}/reject",
        json={"kind": "task", "reason": "这条先不做"},
    )
    assert rejected.status_code == 200
    tasks = client.get("/tasks", params={"order_id": order["id"]}).json()
    assert all(
        not (item["kind"] == "suggestion" and item["suggestion_id"] == suggestion["id"]) for item in tasks
    )
    drafts = client.get(f"/orders/{order['id']}/drafts").json()
    assert suggestion["id"] not in [item["id"] for item in drafts["task_suggestions"]]


def test_draft_suggestion_moves_to_open_column_after_confirm(client):
    order, response = _post_sample(client, "ord018-cancel")
    suggestion = next(item for item in response.json()["task_suggestions"] if item["suggestion_key"] == "mark_cancel")
    waiting = client.get("/tasks", params={"order_id": order["id"], "status": "待确认"}).json()
    shown = next(item for item in waiting if item["suggestion_id"] == suggestion["id"])
    assert shown["kind"] == "suggestion"
    assert shown["status"] == "待确认"
    assert shown["order_no"] == "ORD-018"
    confirmed = client.post(f"/drafts/{suggestion['id']}/confirm", json={"kind": "task"})
    assert confirmed.status_code == 200
    board = client.get("/tasks", params={"order_id": order["id"]}).json()
    assert any(item["kind"] == "task" and item["id"] == confirmed.json()["task_id"] and item["status"] == "待处理" for item in board)
    assert all(item["kind"] != "suggestion" or item["suggestion_id"] != suggestion["id"] for item in board)


def test_followup_references_open_task(client):
    _order_row, response = _post_sample(client, "ord018-conflict")
    sales = next(item for item in response.json()["task_suggestions"] if item["suggestion_key"] == "confirm_split_or_date")
    confirmed = client.post(f"/drafts/{sales['id']}/confirm", json={"kind": "task"})
    assert confirmed.status_code == 200
    _order_row, follow = _post_sample(client, "ord018-followup")
    assert follow.status_code == 200, follow.text
    update = next(
        item for item in follow.json()["task_suggestions"] if item["suggestion_key"] == "confirm_split_or_date"
    )
    assert update["action"] == "update"
    assert update["target_task_id"] == confirmed.json()["task_id"]
    assert "未关闭任务" in update["reason"]


def test_contradiction_sample_keeps_both_delivery_events(client):
    _order_row, response = _post_sample(client, "ord018-contradiction")
    assert response.status_code == 200, response.text
    deliveries = [item for item in response.json()["events"] if item["event_type"] == "delivery_change"]
    assert {item["proposed_value"] for item in deliveries} == {"2026-11-06", "2026-11-10"}
    assert any(item["code"] == "contradiction" for item in response.json()["risks"])


def test_quantity_sample_and_owner_sample(client):
    _order_row, quantity = _post_sample(client, "ord019-qty")
    assert quantity.status_code == 200, quantity.text
    assert all(item["code"] != "qty_over_stock" for item in quantity.json()["risks"])
    assert any(item["suggestion_key"] == "confirm_quantity" for item in quantity.json()["task_suggestions"])

    _order_row, owner = _post_sample(client, "ord018-owner")
    assert owner.status_code == 200, owner.text
    suggestion = next(item for item in owner.json()["task_suggestions"] if item["suggestion_key"] == "unassigned_followup")
    assert suggestion["owner"] == ""


def test_cancel_sample_suggests_closing_existing_task(client):
    order, first = _post_sample(client, "ord018-owner")
    suggestion = next(item for item in first.json()["task_suggestions"] if item["suggestion_key"] == "unassigned_followup")
    created = client.post(
        f"/drafts/{suggestion['id']}/confirm",
        json={"kind": "task", "owner": "仓库"},
    )
    assert created.status_code == 200
    cancel = _sample("ord018-cancel")
    response = client.post(
        f"/orders/{order['id']}/sources",
        json={"type": "chat", "title": "取消", "text": cancel["text"]},
    )
    assert response.status_code == 200, response.text
    assert any(
        item["action"] == "close" and item["target_task_id"] == created.json()["task_id"]
        for item in response.json()["task_suggestions"]
    )
    assert any(item["order_status"] == "待确认取消" for item in response.json()["task_suggestions"])


def test_llm_mode_without_key_keeps_source(client, monkeypatch):
    monkeypatch.setenv("EXTRACT_MODE", "llm")
    monkeypatch.setenv("LLM_API_KEY", "")
    order = _order(client, "ORD-018")
    response = client.post(
        f"/orders/{order['id']}/sources",
        json={"type": "chat", "title": "失败", "text": "客户想改数量"},
    )
    assert response.status_code == 400
    source_id = response.json()["detail"]["source_id"]
    evidence = client.get(f"/evidence/{source_id}")
    assert evidence.status_code == 200
    assert "改数量" in evidence.json()["raw_text"]
    drafts = client.get(f"/orders/{order['id']}/drafts").json()
    assert drafts["events"] == []


def test_csv_upload_keeps_cell_coordinates(client):
    order = _order(client, "ORD-018")
    response = client.post(
        f"/orders/{order['id']}/sources",
        data={"title": "库存表", "type": "chat"},
        files={"file": ("stock.csv", "物料,可用数量\n小型断路器,120\n".encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 200, response.text
    evidence = client.get(f"/evidence/{response.json()['source_id']}").json()
    assert evidence["type"] == "table"
    assert "[R2C2] 120" in evidence["raw_text"]
    assert evidence["table"]["rows"][0][1] == "120"


def test_invalid_json_encoding_is_rejected(client):
    order = _order(client, "ORD-018")
    response = client.post(
        f"/orders/{order['id']}/sources",
        content=b"\xe5\xe5",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400


def test_create_customer_with_first_order_then_another_order(client):
    created = client.post(
        "/customers",
        json={
            "name": " 青岚电子 ",
            "contact": "采购 小周",
            "note": "新客户",
            "order": {
                "order_no": " ORD-301 ",
                "product": "贴片电阻",
                "quantity": 50,
                "delivery_date": "2026-11-12",
                "owner": "林晓",
                "available_qty": 200,
                "earliest_completion": "2026-11-05",
                "supply_note": "",
            },
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["customer"]["name"] == "青岚电子"
    assert body["customer"]["order_count"] == 1
    assert body["order"]["order_no"] == "ORD-301"
    assert body["order"]["status"] == "已确认"
    detail = client.get(f"/orders/{body['order']['id']}").json()
    assert detail["customer"]["contact"] == "采购 小周"
    assert detail["supply"]["available_qty"] == 200
    assert "200" in detail["supply"]["note"]
    assert detail["order"]["quantity"] == 50

    second = client.post(
        "/orders",
        json={
            "customer_id": body["customer"]["id"],
            "order_no": "ORD-302",
            "product": "传感器支架",
            "quantity": 80,
            "delivery_date": "11月25日",
            "available_qty": 0,
            "earliest_completion": "2026-11-18",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["customer_name"] == "青岚电子"
    assert second.json()["delivery_date"] == "2026-11-25"
    customers = client.get("/customers").json()
    assert next(item for item in customers if item["name"] == "青岚电子")["order_count"] == 2


def test_customer_without_order_can_receive_orders_later(client):
    created = client.post("/customers", json={"name": "临港电气", "contact": "采购 阿宁", "note": "先建档"})
    assert created.status_code == 200, created.text
    body = created.json()
    assert "order" not in body
    assert body["customer"]["order_count"] == 0
    customer_id = body["customer"]["id"]
    listed = client.get("/customers").json()
    assert next(item for item in listed if item["id"] == customer_id)["order_count"] == 0
    assert client.get("/orders").json()
    assert all(item["customer_id"] != customer_id for item in client.get("/orders").json())
    first = client.post(
        "/orders",
        json={
            "customer_id": customer_id,
            "order_no": "ORD-401",
            "product": "小型断路器",
            "quantity": 20,
            "delivery_date": "2026-12-01",
            "available_qty": 120,
            "earliest_completion": "2026-11-08",
        },
    )
    second = client.post(
        "/orders",
        json={
            "customer_id": customer_id,
            "order_no": "ORD-402",
            "product": "交流接触器",
            "quantity": 6,
            "delivery_date": "2026-12-15",
            "available_qty": 80,
            "earliest_completion": "2026-11-12",
        },
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["customer_id"] == customer_id
    assert second.json()["customer_id"] == customer_id
    again = client.get("/customers").json()
    assert next(item for item in again if item["id"] == customer_id)["order_count"] == 2


def test_create_customer_rejects_blank_name_and_duplicate_order(client):
    blank = client.post("/customers", json={"name": "  "})
    assert blank.status_code == 400
    missing = client.post(
        "/orders",
        json={
            "customer_id": 9999,
            "order_no": "ORD-X",
            "quantity": 1,
            "delivery_date": "2026-11-01",
            "available_qty": 1,
            "earliest_completion": "2026-11-01",
        },
    )
    assert missing.status_code == 404
    first = client.post(
        "/customers",
        json={
            "name": "津门阀门",
            "order": {
                "order_no": "ORD-303",
                "quantity": 60,
                "delivery_date": "2026-11-18",
                "available_qty": 150,
                "earliest_completion": "2026-11-10",
            },
        },
    )
    assert first.status_code == 200, first.text
    duplicate = client.post(
        "/orders",
        json={
            "customer_id": first.json()["customer"]["id"],
            "order_no": "ORD-303",
            "quantity": 10,
            "delivery_date": "2026-12-01",
            "available_qty": 10,
            "earliest_completion": "2026-11-20",
        },
    )
    assert duplicate.status_code == 409
    bad_qty = client.post(
        "/orders",
        json={
            "customer_id": first.json()["customer"]["id"],
            "order_no": "ORD-304",
            "quantity": 0,
            "delivery_date": "2026-12-01",
            "available_qty": 10,
            "earliest_completion": "2026-11-20",
        },
    )
    assert bad_qty.status_code == 400


def test_orders_can_be_filtered_by_customer_or_product(client):
    orders = client.get("/orders").json()
    target = next(item for item in orders if item["order_no"] == "ORD-018")
    by_product = client.get("/orders", params={"product": target["product"]}).json()
    assert [item["order_no"] for item in by_product] == ["ORD-018"]
    by_customer = client.get("/orders", params={"customer_id": target["customer_id"]}).json()
    assert [item["order_no"] for item in by_customer] == ["ORD-018"]
    both = client.get(
        "/orders",
        params={"customer_id": target["customer_id"], "product": "塑壳断路器"},
    ).json()
    assert both == []


def test_trash_hides_order_until_restore_and_purge_removes_it(client):
    order = _order(client, "ORD-019")
    early = client.delete(f"/orders/{order['id']}/purge")
    assert early.status_code == 409
    removed = client.delete(f"/orders/{order['id']}")
    assert removed.status_code == 200
    assert removed.json()["deleted_at"]
    assert all(item["order_no"] != "ORD-019" for item in client.get("/orders").json())
    trash = client.get("/orders", params={"trashed": True}).json()
    assert [item["order_no"] for item in trash] == ["ORD-019"]
    blocked = client.post(
        f"/orders/{order['id']}/sources",
        json={"type": "chat", "title": "回收站", "text": "还要改数量"},
    )
    assert blocked.status_code == 409
    duplicate = client.post(
        "/orders",
        json={
            "customer_id": order["customer_id"],
            "order_no": "ORD-019",
            "quantity": 1,
            "delivery_date": "2026-12-01",
            "available_qty": 1,
            "earliest_completion": "2026-11-01",
        },
    )
    assert duplicate.status_code == 409
    assert "回收站" in duplicate.json()["detail"]
    customers = client.get("/customers").json()
    assert next(item for item in customers if item["id"] == order["customer_id"])["order_count"] == 0
    restored = client.post(f"/orders/{order['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None
    assert any(item["order_no"] == "ORD-019" for item in client.get("/orders").json())
    client.delete(f"/orders/{order['id']}")
    purged = client.delete(f"/orders/{order['id']}/purge")
    assert purged.status_code == 200
    assert client.get(f"/orders/{order['id']}").status_code == 404
    assert client.get("/orders", params={"trashed": True}).json() == []
    assert any(item["order_no"] == "ORD-018" for item in client.get("/orders").json())


def test_trashed_order_leaves_the_task_board(client):
    order, response = _post_sample(client, "ord018-conflict")
    suggestion_id = response.json()["task_suggestions"][0]["id"]
    assert client.get("/tasks", params={"order_id": order["id"]}).json()
    assert client.delete(f"/orders/{order['id']}").status_code == 200
    assert client.get("/tasks", params={"order_id": order["id"]}).json() == []
    assert client.post(f"/orders/{order['id']}/restore").status_code == 200
    again = client.get("/tasks", params={"order_id": order["id"]}).json()
    assert any(item["kind"] == "suggestion" and item["suggestion_id"] == suggestion_id for item in again)


def test_same_product_shares_stock_and_unconfirmed_demand_does_not_reserve(client):
    base = _order(client, "ORD-018")
    supply = client.get(f"/orders/{base['id']}").json()["supply"]
    assert supply["on_hand"] == 120
    assert supply["reserved"] == 100
    assert supply["committable"] == 20
    assert supply["available_qty"] == 120

    created = client.post(
        "/orders",
        json={
            "customer_id": base["customer_id"],
            "order_no": "ORD-028",
            "product": base["product"],
            "quantity": 30,
            "delivery_date": "2026-11-18",
            "available_qty": 999,
            "earliest_completion": "2026-12-01",
        },
    )
    assert created.status_code == 200, created.text
    shared = client.get(f"/orders/{created.json()['id']}").json()["supply"]
    assert shared["on_hand"] == 120
    assert shared["earliest_completion"] == "2026-11-08"
    assert shared["reserved"] == 130
    assert shared["available_qty"] == 20

    original = client.get(f"/orders/{base['id']}").json()["supply"]
    assert original["reserved"] == 130
    assert original["available_qty"] == 90
    assert original["committable"] == -10
    drafts = client.get(f"/orders/{base['id']}/drafts").json()
    assert any("已确认数量" in item["message"] and "90" in item["message"] for item in drafts["risks"])

    _posted, response = _post_sample(client, "ord018-conflict")
    assert response.status_code == 200, response.text
    after = client.get(f"/orders/{base['id']}").json()["supply"]
    assert after["reserved"] == 130
    assert after["on_hand"] == 120
    assert any("90" in item["message"] and "150" in item["message"] for item in response.json()["risks"])


def test_confirming_inventory_and_production_updates_shared_supply(client):
    order = _order(client, "ORD-018")
    stock = client.post(
        f"/orders/{order['id']}/sources",
        json={"type": "note", "title": "盘点", "text": "仓库盘点：库存只剩 90 件。"},
    )
    assert stock.status_code == 200, stock.text
    event = next(item for item in stock.json()["events"] if item["event_type"] == "inventory_fact")
    assert event["proposed_value"] == "90"
    assert client.get(f"/orders/{order['id']}").json()["supply"]["on_hand"] == 120
    confirmed = client.post(f"/drafts/{event['id']}/confirm", json={"kind": "event"})
    assert confirmed.status_code == 200, confirmed.text
    assert client.get(f"/orders/{order['id']}").json()["order"]["quantity"] == 100
    supply = client.get(f"/orders/{order['id']}").json()["supply"]
    assert supply["on_hand"] == 90
    risks = client.get(f"/orders/{order['id']}/drafts").json()
    assert any("已确认数量" in item["message"] and "90" in item["message"] for item in risks["risks"])
    assert any(item["suggestion_key"] == "verify_capacity" for item in risks["task_suggestions"])

    production = client.post(
        f"/orders/{order['id']}/sources",
        json={"type": "note", "title": "生产", "text": "生产反馈：这批最早 11 月 12 日才能完成。"},
    )
    assert production.status_code == 200, production.text
    constraint = next(item for item in production.json()["events"] if item["event_type"] == "production_constraint")
    assert constraint["proposed_value"] == "2026-11-12"
    assert client.get(f"/orders/{order['id']}").json()["supply"]["earliest_completion"] == "2026-11-08"
    applied = client.post(f"/drafts/{constraint['id']}/confirm", json={"kind": "event"})
    assert applied.status_code == 200, applied.text
    updated = client.get(f"/orders/{order['id']}").json()
    assert updated["supply"]["earliest_completion"] == "2026-11-12"
    assert updated["order"]["delivery_date"] == "2026-11-10"
    assert any(item["code"] == "delivery_before_capacity" for item in client.get(f"/orders/{order['id']}/drafts").json()["risks"])


def test_supply_patch_recomputes_open_orders(client):
    order = _order(client, "ORD-018")
    patched = client.patch(
        f"/orders/{order['id']}/supply",
        json={"on_hand": 80, "earliest_completion": "2026-11-20"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["on_hand"] == 80
    assert body["earliest_completion"] == "2026-11-20"
    assert body["reserved"] == 100
    drafts = client.get(f"/orders/{order['id']}/drafts").json()
    assert any(item["code"] == "qty_over_stock" for item in drafts["risks"])
    assert any(item["code"] == "delivery_before_capacity" for item in drafts["risks"])
    audits = client.get(f"/orders/{order['id']}").json()["audits"]
    assert any(item["action"] == "update_supply" and item["before"]["on_hand"] == 120 for item in audits)


def test_cancel_confirmation_releases_reservation(client):
    base = _order(client, "ORD-018")
    other = client.post(
        "/orders",
        json={
            "customer_id": base["customer_id"],
            "order_no": "ORD-029",
            "product": base["product"],
            "quantity": 10,
            "delivery_date": "2026-11-20",
            "available_qty": 1,
            "earliest_completion": "2026-11-01",
        },
    )
    assert other.status_code == 200, other.text
    assert client.get(f"/orders/{other.json()['id']}").json()["supply"]["available_qty"] == 20
    _order_row, response = _post_sample(client, "ord018-cancel")
    suggestion = next(item for item in response.json()["task_suggestions"] if item["suggestion_key"] == "mark_cancel")
    confirmed = client.post(f"/drafts/{suggestion['id']}/confirm", json={"kind": "task"})
    assert confirmed.status_code == 200, confirmed.text
    released = client.get(f"/orders/{other.json()['id']}").json()["supply"]
    assert released["on_hand"] == 120
    assert released["reserved"] == 10
    assert released["available_qty"] == 120


def _products(client) -> list[str]:
    return [item["product"] for item in client.get("/orders").json()]


def _warehouse(client, product: str) -> dict:
    return next(item for item in client.get("/warehouse").json() if item["product"] == product)


def test_fixture_extracts_partial_delivery_and_additional_products(client):
    order, response = _post_sample(client, "ord018-more-products")
    assert response.status_code == 200, response.text
    events = response.json()["events"]
    partial = next(item for item in events if item["event_type"] == "partial_delivery")
    assert partial["field_name"] == "delivered_qty"
    assert partial["proposed_value"] == "一半"
    added = [item for item in events if item["event_type"] == "additional_order"]
    assert len(added) == 2
    specs = {json.loads(item["proposed_value"])["product"]: json.loads(item["proposed_value"]) for item in added}
    assert specs["交流接触器"]["quantity"] == 40
    assert specs["交流接触器"]["delivery_date"] == "2026-11-20"
    assert specs["交流接触器"]["on_hand"] is None
    assert specs["热继电器"]["quantity"] == 25
    assert specs["热继电器"]["delivery_date"] == "2026-11-25"
    assert "建议为该客户新增商品订单" in added[0]["explanation"]
    detail = client.get(f"/orders/{order['id']}").json()
    assert detail["order"]["quantity"] == 100
    assert detail["order"]["delivered_qty"] == 0
    assert "交流接触器" not in _products(client)
    assert "热继电器" not in _products(client)


def test_confirm_additional_orders_and_partial_delivery_reserves_outstanding(client):
    order, response = _post_sample(client, "ord018-more-products")
    partial = next(item for item in response.json()["events"] if item["event_type"] == "partial_delivery")
    confirmed = client.post(f"/drafts/{partial['id']}/confirm", json={"kind": "event"})
    assert confirmed.status_code == 200, confirmed.text
    detail = client.get(f"/orders/{order['id']}").json()
    assert detail["order"]["quantity"] == 100
    assert detail["order"]["delivered_qty"] == 50
    assert detail["supply"]["on_hand"] == 120
    assert detail["supply"]["reserved"] == 50
    assert detail["supply"]["committable"] == 70
    assert detail["supply"]["earliest_completion"] == "2026-11-08"

    for event in response.json()["events"]:
        if event["event_type"] != "additional_order":
            continue
        created = client.post(f"/drafts/{event['id']}/confirm", json={"kind": "event"})
        assert created.status_code == 200, created.text
        assert created.json()["created_order_no"]
    customer_orders = client.get("/orders", params={"customer_id": order["customer_id"]}).json()
    seal = next(item for item in customer_orders if item["product"] == "交流接触器")
    pin = next(item for item in customer_orders if item["product"] == "热继电器")
    assert seal["quantity"] == 40
    assert seal["delivery_date"] == "2026-11-20"
    assert pin["quantity"] == 25
    assert pin["delivery_date"] == "2026-11-25"
    seal_supply = _warehouse(client, "交流接触器")
    pin_supply = _warehouse(client, "热继电器")
    assert seal_supply["on_hand"] == 80
    assert seal_supply["reserved"] == 40
    assert "待核实" not in seal_supply["note"]
    assert seal_supply["earliest_completion"] == "2026-11-12"
    assert pin_supply["on_hand"] == 36
    assert pin_supply["reserved"] == 25
    assert pin_supply["earliest_completion"] == "2026-11-12"
    original = _warehouse(client, "小型断路器")
    assert original["on_hand"] == 120
    assert original["reserved"] == 50
    assert original["committable"] == 70
    assert original["earliest_completion"] == "2026-11-08"


def test_reject_additional_order_creates_nothing(client):
    order, response = _post_sample(client, "ord018-more-products")
    for event in response.json()["events"]:
        rejected = client.post(
            f"/drafts/{event['id']}/reject",
            json={"kind": "event", "reason": "先不建"},
        )
        assert rejected.status_code == 200, rejected.text
    assert "交流接触器" not in _products(client)
    assert "热继电器" not in _products(client)
    detail = client.get(f"/orders/{order['id']}").json()
    assert detail["order"]["quantity"] == 100
    assert detail["order"]["delivered_qty"] == 0
    assert detail["supply"]["reserved"] == 100


def test_existing_product_order_gets_draft_instead_of_duplicate(client):
    base = _order(client, "ORD-018")
    created = client.post(
        "/orders",
        json={
            "customer_id": base["customer_id"],
            "order_no": "ORD-040",
            "product": "交流接触器",
            "quantity": 10,
            "delivery_date": "2026-11-18",
            "available_qty": 50,
            "earliest_completion": "2026-11-15",
        },
    )
    assert created.status_code == 200, created.text
    response = client.post(
        f"/orders/{base['id']}/sources",
        json={
            "type": "chat",
            "title": "已有商品",
            "text": "另外再买交流接触器 40 件，11 月 22 日要货。",
        },
    )
    assert response.status_code == 200, response.text
    assert all(item["event_type"] != "additional_order" for item in response.json()["events"])
    assert _products(client).count("交流接触器") == 1
    drafts = client.get(f"/orders/{created.json()['id']}/drafts").json()
    quantity = next(item for item in drafts["events"] if item["event_type"] == "quantity_change")
    delivery = next(item for item in drafts["events"] if item["event_type"] == "delivery_change")
    assert quantity["current_value"] == "10"
    assert quantity["proposed_value"] == "40"
    assert "不新建" in quantity["explanation"]
    assert delivery["proposed_value"] == "2026-11-22"
    applied = client.post(f"/drafts/{quantity['id']}/confirm", json={"kind": "event", "apply_to_order": True})
    assert applied.status_code == 200, applied.text
    dated = client.post(f"/drafts/{delivery['id']}/confirm", json={"kind": "event", "apply_to_order": True})
    assert dated.status_code == 200, dated.text
    matches = [item for item in client.get("/orders").json() if item["product"] == "交流接触器"]
    assert len(matches) == 1
    assert matches[0]["quantity"] == 40
    assert matches[0]["delivery_date"] == "2026-11-22"
    assert matches[0]["order_no"] == "ORD-040"


def test_current_product_is_not_opened_as_a_new_order(client):
    order = _order(client, "ORD-018")
    response = client.post(
        f"/orders/{order['id']}/sources",
        json={"type": "chat", "title": "当前商品", "text": "另外再买小型断路器 140 件，11 月 21 日要货。"},
    )
    assert response.status_code == 200, response.text
    assert all(item["event_type"] != "additional_order" for item in response.json()["events"])
    quantity = next(item for item in response.json()["events"] if item["event_type"] == "quantity_change")
    assert quantity["current_value"] == "100"
    assert quantity["proposed_value"] == "140"
    assert _products(client).count("小型断路器") == 1
    assert client.get(f"/orders/{order['id']}").json()["order"]["quantity"] == 100


def test_additional_order_reuses_supply_and_records_stated_stock(client):
    order = _order(client, "ORD-018")
    before = _warehouse(client, "塑壳断路器")
    assert before["on_hand"] == 200
    response = client.post(
        f"/orders/{order['id']}/sources",
        json={
            "type": "chat",
            "title": "共用库存",
            "text": "另外再买塑壳断路器 10 件，11 月 28 日要货，库存 5 件。\n另外再买时间继电器 12 件，11 月 21 日要货，库存 7 件，最早可完工日期 2026-12-01。",
        },
    )
    assert response.status_code == 200, response.text
    specs = {
        json.loads(item["proposed_value"])["product"]: json.loads(item["proposed_value"])
        for item in response.json()["events"]
        if item["event_type"] == "additional_order"
    }
    assert specs["塑壳断路器"]["on_hand"] == 5
    assert specs["时间继电器"]["on_hand"] == 7
    assert specs["时间继电器"]["earliest_completion"] == "2026-12-01"
    assert "不会用对话里的数字覆盖在手库存" in next(
        item["explanation"] for item in response.json()["events"] if "塑壳断路器" in item["proposed_value"]
    )
    for event in response.json()["events"]:
        if event["event_type"] != "additional_order":
            continue
        created = client.post(f"/drafts/{event['id']}/confirm", json={"kind": "event"})
        assert created.status_code == 200, created.text
    shared = _warehouse(client, "塑壳断路器")
    assert shared["on_hand"] == 200
    assert shared["earliest_completion"] == "2026-11-15"
    assert shared["reserved"] == 90
    assert shared["note"] == before["note"]
    fresh = _warehouse(client, "时间继电器")
    assert fresh["on_hand"] == 7
    assert fresh["earliest_completion"] == "2026-12-01"
    assert fresh["reserved"] == 12
    assert "待核实" not in fresh["note"]
    assert _warehouse(client, "小型断路器")["on_hand"] == 120


def test_half_delivery_uses_integer_division(client):
    base = _order(client, "ORD-018")
    created = client.post(
        "/orders",
        json={
            "customer_id": base["customer_id"],
            "order_no": "ORD-041",
            "product": "奇数样品",
            "quantity": 7,
            "delivery_date": "2026-11-18",
            "available_qty": 10,
            "earliest_completion": "2026-11-01",
        },
    )
    assert created.status_code == 200, created.text
    response = client.post(
        f"/orders/{created.json()['id']}/sources",
        json={"type": "chat", "title": "一半", "text": "这批已经交付了一半。"},
    )
    assert response.status_code == 200, response.text
    event = next(item for item in response.json()["events"] if item["event_type"] == "partial_delivery")
    assert event["proposed_value"] == "一半"
    confirmed = client.post(f"/drafts/{event['id']}/confirm", json={"kind": "event"})
    assert confirmed.status_code == 200, confirmed.text
    detail = client.get(f"/orders/{created.json()['id']}").json()
    assert detail["order"]["quantity"] == 7
    assert detail["order"]["delivered_qty"] == 3
    assert detail["supply"]["reserved"] == 4


def test_warehouse_patch_recomputes_open_orders(client):
    order = _order(client, "ORD-018")
    listed = _warehouse(client, "小型断路器")
    assert listed["on_hand"] == 120
    assert listed["reserved"] == 100
    assert listed["committable"] == 20
    assert listed["earliest_completion"] == "2026-11-08"
    assert listed["order_id"] == order["id"]
    patched = client.patch(
        "/warehouse",
        json={"product": "小型断路器", "on_hand": 80, "earliest_completion": "2026-11-20"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["on_hand"] == 80
    assert body["reserved"] == 100
    assert body["committable"] == -20
    assert body["earliest_completion"] == "2026-11-20"
    drafts = client.get(f"/orders/{order['id']}/drafts").json()
    assert any(item["code"] == "qty_over_stock" for item in drafts["risks"])
    assert any(item["code"] == "delivery_before_capacity" for item in drafts["risks"])


def test_llm_schema_keeps_other_products_and_delivery_as_drafts():
    from app.services.extract import _events_from_llm

    class SnapshotOrder:
        quantity = 100
        delivered_qty = 0
        delivery_date = "2026-11-10"
        order_no = "ORD-018"
        product = "小型断路器"

    text = "小型断路器已经交付了一半。另外再买交流接触器 40 件。"
    events = _events_from_llm(
        {
            "events": [
                {
                    "event_type": "partial_delivery",
                    "field_name": "delivered_qty",
                    "proposed_value": "一半",
                    "proposal_origin": "客户期望",
                    "evidence_excerpt": "已经交付了一半",
                    "order_no": "ORD-999",
                }
            ],
            "additional_products": [
                {
                    "product": "交流接触器",
                    "quantity": "40",
                    "delivery_date": "2026-11-20",
                    "on_hand": "",
                    "evidence_excerpt": "交流接触器 40 件",
                    "explanation": "建议为该客户新增商品订单",
                }
            ],
        },
        text,
        SnapshotOrder(),
    )
    partial = next(item for item in events if item.event_type == "partial_delivery")
    added = next(item for item in events if item.event_type == "additional_order")
    assert partial.proposed_value == "一半"
    assert partial.order_no is None
    assert added.order_no is None
    spec = json.loads(added.proposed_value)
    assert spec["product"] == "交流接触器"
    assert spec["quantity"] == 40
    assert spec["on_hand"] is None
    assert "ORD-999" not in added.proposed_value


def test_seeded_warehouse_lists_regional_stock(client):
    rows = {item["product"]: item for item in client.get("/warehouse").json()}
    expected = {
        "小型断路器": (120, "2026-11-08"),
        "塑壳断路器": (200, "2026-11-15"),
        "交流接触器": (80, "2026-11-12"),
        "热继电器": (36, "2026-11-12"),
    }
    for product, (on_hand, earliest) in expected.items():
        assert rows[product]["on_hand"] == on_hand
        assert rows[product]["earliest_completion"] == earliest


def test_warehouse_can_add_a_product_without_an_order(client):
    created = client.post(
        "/warehouse",
        json={"product": "样品弹簧", "on_hand": 15, "earliest_completion": "2026-12-15", "note": "新到货"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["product"] == "样品弹簧"
    assert body["on_hand"] == 15
    assert body["reserved"] == 0
    assert body["committable"] == 15
    assert body["earliest_completion"] == "2026-12-15"
    assert body["note"] == "新到货"
    assert body["order_id"] is None
    assert _warehouse(client, "样品弹簧")["on_hand"] == 15

    duplicate = client.post(
        "/warehouse",
        json={"product": " 样品弹簧 ", "on_hand": 1, "earliest_completion": "2026-12-16"},
    )
    assert duplicate.status_code == 409
    blank = client.post("/warehouse", json={"product": "  ", "on_hand": 1, "earliest_completion": "2026-12-16"})
    assert blank.status_code == 400
    bad_date = client.post(
        "/warehouse",
        json={"product": "另一弹簧", "on_hand": 1, "earliest_completion": "下周二"},
    )
    assert bad_date.status_code == 400
    negative = client.post(
        "/warehouse",
        json={"product": "另一弹簧", "on_hand": -1, "earliest_completion": "2026-12-16"},
    )
    assert negative.status_code == 400

    patched = client.patch(
        "/warehouse",
        json={"product": "样品弹簧", "on_hand": 9, "earliest_completion": "2026-12-20"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["on_hand"] == 9
    assert patched.json()["order_id"] is None

    base = _order(client, "ORD-018")
    order = client.post(
        "/orders",
        json={
            "customer_id": base["customer_id"],
            "order_no": "ORD-088",
            "product": "样品弹簧",
            "quantity": 4,
            "delivery_date": "2026-12-28",
            "available_qty": 100,
            "earliest_completion": "2026-12-01",
        },
    )
    assert order.status_code == 200, order.text
    reused = _warehouse(client, "样品弹簧")
    assert reused["on_hand"] == 9
    assert reused["earliest_completion"] == "2026-12-20"
    assert reused["reserved"] == 4
    assert reused["order_id"] == order.json()["id"]


def _assistant(client, monkeypatch, actions, reply="好"):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    def fake(system, user):
        assert system
        assert "最近对话" in user
        return json.dumps({"reply": reply, "actions": actions}, ensure_ascii=False)

    monkeypatch.setattr("app.services.assistant.complete_text", fake)
    return client.post(
        "/assistant/turn",
        json={"route": "workbench", "messages": [{"role": "user", "content": "处理一下"}]},
    )


def test_assistant_without_key_does_not_call_model(client, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("should not call model")

    monkeypatch.setattr("httpx.post", boom)
    response = client.post(
        "/assistant/turn",
        json={"route": "workbench", "messages": [{"role": "user", "content": "打开工作台"}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["actions"] == []
    assert "密钥" in body["reply"]


def test_assistant_strips_apply_to_order_on_commitment(client, monkeypatch):
    _order_row, response = _post_sample(client, "ord018-conflict")
    commitment = next(item for item in response.json()["events"] if item["event_type"] == "commitment")
    turned = _assistant(
        client,
        monkeypatch,
        [
            {
                "type": "confirm_draft",
                "kind": "event",
                "draft_id": commitment["id"],
                "order_no": "ORD-018",
                "apply_to_order": True,
            }
        ],
    )
    assert turned.status_code == 200, turned.text
    action = turned.json()["actions"][0]
    assert action["type"] == "confirm_draft"
    assert action["draft_id"] == commitment["id"]
    assert action["apply_to_order"] is False
    assert action["can_apply"] is False


def test_assistant_drops_unknown_order(client, monkeypatch):
    turned = _assistant(
        client,
        monkeypatch,
        [{"type": "create_source", "order_no": "ORD-999", "source_type": "chat", "text": "这批货先记下"}],
    )
    assert turned.status_code == 200, turned.text
    assert turned.json()["actions"] == []
    assert "ORD-999" in turned.json()["reply"]


def test_assistant_keeps_finished_warehouse_label_from_workbench(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    def fake(system, user):
        assert "warehouse" in user
        assert "全局快照" in user
        return json.dumps(
            {
                "reply": "当前页面是订单列表，不是仓库商品详情；系统不支持删除或清空仓库数据。",
                "actions": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.assistant.complete_text", fake)
    turned = client.post(
        "/assistant/turn",
        json={
            "route": "workbench",
            "messages": [{"role": "user", "content": "把仓库内商品详情都删去，只保留A公司存货"}],
        },
    )
    assert turned.status_code == 200, turned.text
    body = turned.json()
    assert "不支持" not in body["reply"]
    assert "A公司存货" in body["reply"]
    assert body["actions"] == []
    assert "已经只剩" in body["reply"]


def test_assistant_will_not_delete_supply_still_on_an_order(client, monkeypatch):
    turned = _assistant(client, monkeypatch, [{"type": "delete_supply", "product": "小型断路器"}])
    assert turned.status_code == 200, turned.text
    assert turned.json()["actions"] == []
    assert "不能删除" in turned.json()["reply"]
    assert "ORD-018" in turned.json()["reply"]


def test_assistant_can_delete_unused_supply(client, monkeypatch):
    turned = _assistant(client, monkeypatch, [{"type": "delete_supply", "product": "热继电器"}])
    assert turned.status_code == 200, turned.text
    action = turned.json()["actions"][0]
    assert action["type"] == "delete_supply"
    assert action["product"] == "热继电器"


def test_assistant_drops_writes_when_order_matches_many(client, monkeypatch):
    turned = _assistant(
        client,
        monkeypatch,
        [{"type": "create_source", "order_no": "ORD-01", "source_type": "chat", "text": "这批货先记下"}],
    )
    assert turned.status_code == 200, turned.text
    writes = {"create_source", "confirm_draft", "reject_draft", "patch_task"}
    assert all(item["type"] not in writes for item in turned.json()["actions"])
    assert "ORD-018" in turned.json()["reply"]
    assert "ORD-019" in turned.json()["reply"]


def test_assistant_parse_file_txt(client):
    response = client.post(
        "/assistant/parse-file",
        files={"file": ("wechat-chat.txt", "客户聊天\n加 100 件".encode(), "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "加 100 件" in body["text"]
    assert body["source_type"] == "chat"


def test_assistant_turn_attachment_text_on_create_source(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    captured: dict[str, str] = {}

    def fake(_system, user):
        captured["user"] = user
        return json.dumps(
            {
                "reply": "好",
                "actions": [
                    {
                        "type": "create_source",
                        "order_no": "ORD-018",
                        "source_type": "chat",
                        "title": "摘要",
                        "text": "模型写的摘要",
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.assistant.complete_text", fake)
    file_text = "客户聊天 · 10:05\n增加到 150 件"
    turned = client.post(
        "/assistant/turn",
        json={
            "route": "workbench",
            "messages": [{"role": "user", "content": "录入附件"}],
            "attachment": {
                "filename": "chat.txt",
                "source_type": "chat",
                "title": "chat",
                "text": file_text,
            },
        },
    )
    assert turned.status_code == 200, turned.text
    assert "attached_source" in captured["user"]
    action = next(item for item in turned.json()["actions"] if item["type"] == "create_source")
    assert action["text"] == file_text


def test_task_cannot_start_without_owner(client):
    _order_row, response = _post_sample(client, "ord018-owner")
    suggestion = next(item for item in response.json()["task_suggestions"] if item["owner"] == "")
    created = client.post(f"/drafts/{suggestion['id']}/confirm", json={"kind": "task", "owner": ""})
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    blocked = client.patch(f"/tasks/{task_id}", json={"status": "进行中"})
    assert blocked.status_code == 400
    started = client.patch(f"/tasks/{task_id}", json={"owner": "仓库", "status": "进行中"})
    assert started.status_code == 200
    assert started.json()["status"] == "进行中"


def _warehouse_snapshot(client) -> list[tuple]:
    return [
        (item["product"], item["on_hand"], item["earliest_completion"], item["note"])
        for item in client.get("/warehouse").json()
    ]


def test_purge_keeps_finished_tasks_and_does_not_touch_other_records(client):
    from sqlalchemy import select

    from app.db import open_session
    from app.models import SupplyConstraint, Task, TaskSuggestion, now_iso

    order = _order(client, "ORD-018")
    other = _order(client, "ORD-019")
    warehouse_before = _warehouse_snapshot(client)
    notes_before = {item["id"]: item["note"] for item in client.get("/customers").json()}
    _order_row, response = _post_sample(client, "ord018-conflict")
    suggestions = response.json()["task_suggestions"]
    assert len(suggestions) >= 2
    finished = client.post(f"/drafts/{suggestions[0]['id']}/confirm", json={"kind": "task", "owner": "销售"})
    assert finished.status_code == 200, finished.text
    finished_id = finished.json()["task_id"]
    assert client.patch(f"/tasks/{finished_id}", json={"status": "已完成"}).status_code == 200
    opened = client.post(f"/drafts/{suggestions[1]['id']}/confirm", json={"kind": "task", "owner": "生产"})
    assert opened.status_code == 200, opened.text
    open_id = opened.json()["task_id"]
    source_id = response.json()["source_id"]

    db = open_session()
    try:
        other_task = Task(
            order_id=other["id"],
            order_no=other["order_no"],
            title="其他订单已完成",
            status="已完成",
            owner="仓库",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        db.add(other_task)
        db.flush()
        other_task_id = other_task.id
        constraint = db.scalar(select(SupplyConstraint).where(SupplyConstraint.order_id == other["id"]))
        assert constraint is not None
        constraint.source_id = source_id
        db.add(
            TaskSuggestion(
                order_id=other["id"],
                suggestion_key="keep-me",
                title="别删这条建议",
                action="update",
                target_task_id=open_id,
                status="draft",
                created_at=now_iso(),
            )
        )
        db.commit()
    finally:
        db.close()

    assert client.delete(f"/orders/{order['id']}").status_code == 200
    active = {item["order_no"] for item in client.get("/orders").json()}
    assert order["order_no"] not in active
    trash = {item["order_no"] for item in client.get("/orders", params={"trashed": True}).json()}
    assert order["order_no"] in trash
    hidden = {item["id"] for item in client.get("/tasks").json()}
    assert finished_id not in hidden
    assert open_id not in hidden
    assert other_task_id in hidden

    assert client.post(f"/orders/{order['id']}/restore").status_code == 200
    restored = {item["id"] for item in client.get("/tasks").json()}
    assert finished_id in restored
    assert open_id in restored

    assert client.delete(f"/orders/{order['id']}").status_code == 200
    purged = client.delete(f"/orders/{order['id']}/purge")
    assert purged.status_code == 200, purged.text
    assert client.get(f"/orders/{order['id']}").status_code == 404
    assert all(item["order_no"] != "ORD-018" for item in client.get("/orders", params={"trashed": True}).json())
    board = client.get("/tasks").json()
    kept = next(item for item in board if item["id"] == finished_id)
    assert kept["status"] == "已完成"
    assert kept["order_no"] == "ORD-018"
    assert kept["order_id"] is None
    assert all(item["id"] != open_id for item in board)
    assert any(item["id"] == other_task_id and item["status"] == "已完成" for item in board)
    assert _warehouse_snapshot(client) == warehouse_before
    notes_after = {item["id"]: item["note"] for item in client.get("/customers").json()}
    assert notes_after == notes_before
    assert any(item["order_no"] == "ORD-019" for item in client.get("/orders").json())
    blocked = client.patch(f"/tasks/{finished_id}", json={"status": "待处理"})
    assert blocked.status_code == 409

    db = open_session()
    try:
        constraint = db.scalar(select(SupplyConstraint).where(SupplyConstraint.order_id == other["id"]))
        assert constraint is not None
        assert constraint.source_id is None
        suggestion = db.scalar(select(TaskSuggestion).where(TaskSuggestion.order_id == other["id"]))
        assert suggestion is not None
        assert suggestion.title == "别删这条建议"
        assert suggestion.target_task_id is None
    finally:
        db.close()


def test_purge_blocks_when_another_order_still_uses_the_evidence(client):
    from app.db import open_session
    from app.models import BusinessEvent, now_iso

    order = _order(client, "ORD-018")
    other = _order(client, "ORD-019")
    warehouse_before = _warehouse_snapshot(client)
    posted, response = _post_sample(client, "ord018-owner")
    source_id = response.json()["source_id"]
    db = open_session()
    try:
        db.add(
            BusinessEvent(
                order_id=other["id"],
                source_id=source_id,
                event_type="quantity_change",
                evidence_excerpt="还引用着这段沟通",
                created_at=now_iso(),
            )
        )
        db.commit()
    finally:
        db.close()
    assert client.delete(f"/orders/{order['id']}").status_code == 200
    blocked = client.delete(f"/orders/{order['id']}/purge")
    assert blocked.status_code == 409
    assert "沟通证据" in blocked.json()["detail"]
    assert any(item["order_no"] == "ORD-018" for item in client.get("/orders", params={"trashed": True}).json())
    assert _warehouse_snapshot(client) == warehouse_before
    assert posted["order_no"] == "ORD-018"


def _set_order_status(order_no: str, status: str) -> None:
    from sqlalchemy import select

    from app.db import open_session
    from app.models import Order

    db = open_session()
    try:
        row = db.scalar(select(Order).where(Order.order_no == order_no))
        assert row is not None
        row.status = status
        db.commit()
    finally:
        db.close()


def test_two_customers_share_one_product_pool(client):
    meter = _warehouse(client, "塑壳断路器")
    assert meter["on_hand"] == 200
    assert meter["reserved"] == 80
    assert meter["committable"] == 120

    base = _order(client, "ORD-018")
    other_customer = _order(client, "ORD-019")
    assert other_customer["customer_name"] == "南浦配电"
    assert other_customer["customer_id"] != base["customer_id"]
    created = client.post(
        "/orders",
        json={
            "customer_id": other_customer["customer_id"],
            "order_no": "ORD-201",
            "product": "小型断路器",
            "quantity": 30,
            "delivery_date": "2026-11-20",
            "available_qty": 999,
            "earliest_completion": "2026-12-01",
        },
    )
    assert created.status_code == 200, created.text
    second_id = created.json()["id"]
    pool = _warehouse(client, "小型断路器")
    assert pool["on_hand"] == 120
    assert pool["reserved"] == 130
    assert pool["committable"] == -10
    qinghe = client.get(f"/orders/{base['id']}").json()["supply"]
    nanpu = client.get(f"/orders/{second_id}").json()["supply"]
    assert qinghe["available_qty"] == 90
    assert qinghe["reserved"] == 130
    assert qinghe["committable"] == -10
    assert nanpu["available_qty"] == 20
    assert nanpu["on_hand"] == 120
    assert nanpu["earliest_completion"] == "2026-11-08"

    chat = client.post(
        f"/orders/{second_id}/sources",
        json={"type": "chat", "title": "加量", "text": "这批小型断路器能不能增加到 80 件？交期按原计划就行。"},
    )
    assert chat.status_code == 200, chat.text
    assert any(item["event_type"] == "quantity_change" and item["proposed_value"] == "80" for item in chat.json()["events"])
    assert client.get(f"/orders/{second_id}").json()["order"]["quantity"] == 30
    assert _warehouse(client, "小型断路器")["reserved"] == 130

    _order_row, response = _post_sample(client, "ord018-conflict")
    assert response.status_code == 200, response.text
    assert any("150" in item["message"] and "90" in item["message"] for item in response.json()["risks"])
    assert _warehouse(client, "小型断路器")["on_hand"] == 120
    assert _warehouse(client, "小型断路器")["reserved"] == 130

    for status in ("已取消", "已关闭", "待确认取消"):
        _set_order_status("ORD-201", status)
        released = _warehouse(client, "小型断路器")
        assert released["reserved"] == 100, status
        assert released["committable"] == 20, status
        assert client.get(f"/orders/{base['id']}").json()["supply"]["available_qty"] == 120

    _set_order_status("ORD-201", "待确认")
    held = _warehouse(client, "小型断路器")
    assert held["reserved"] == 130
    assert client.get(f"/orders/{base['id']}").json()["supply"]["available_qty"] == 90
    assert client.get(f"/orders/{second_id}").json()["order"]["quantity"] == 30


def test_import_history_keeps_processed_summary_only(client):
    order, response = _post_sample(client, "ord018-conflict")
    assert response.status_code == 200, response.text
    intake = client.post(
        f"/customers/{order['customer_id']}/intake",
        json={"title": "包装跟进", "text": "内部备注\n包装方式还要再确认一下，具体谁跟进还没定。"},
    )
    assert intake.status_code == 200, intake.text
    detail = client.get(f"/orders/{order['id']}").json()
    imports = detail["imports"]
    assert len(imports) >= 2
    assert [item["created_at"] for item in imports] == sorted((item["created_at"] for item in imports), reverse=True)
    recorded = next(item for item in imports if item["scope"] == "order")
    assert set(recorded) == {"id", "scope", "title", "created_at", "summary"}
    summary = recorded["summary"]
    assert "青禾成套" in summary
    assert "小型断路器" in summary
    assert "订单数量" in summary
    assert "订单交期" in summary
    assert "最早可完工日期" in summary
    assert "要求交期" in summary
    assert "150" in summary
    assert "尚未完成的生产计划需要重新核对" not in summary
    assert "我先跟客户说了" not in summary
    assert "{" not in summary
    customer_import = next(item for item in imports if item["scope"] == "customer")
    assert "青禾成套" in customer_import["summary"]
    assert "没有挂在具体订单" in customer_import["summary"]
    assert "包装方式还要再确认" not in customer_import["summary"]
    assert client.get(f"/orders/{_order(client, 'ORD-019')['id']}").json()["imports"] == []


def test_warehouse_edit_leaves_orders_and_delete_requires_unused_product(client):
    order = _order(client, "ORD-018")
    before = client.get(f"/orders/{order['id']}").json()["order"]
    blocked = client.delete("/warehouse", params={"product": "小型断路器"})
    assert blocked.status_code == 409
    message = blocked.json()["detail"]
    assert "不能删除" in message
    assert "小型断路器" in message
    assert "ORD-018" in message
    assert "青禾成套" in message
    assert _warehouse(client, "小型断路器")["on_hand"] == 120

    renamed = client.patch(
        "/warehouse",
        json={
            "product": "小型断路器",
            "name": "微型断路器",
            "on_hand": 70,
            "earliest_completion": "2026-11-25",
            "note": "不应写上",
        },
    )
    assert renamed.status_code == 409
    assert "不能修改商品名称" in renamed.json()["detail"]
    kept = _warehouse(client, "小型断路器")
    assert kept["on_hand"] == 120
    assert "不应写上" not in kept["note"]

    edited = client.patch(
        "/warehouse",
        json={
            "product": "小型断路器",
            "name": "小型断路器",
            "on_hand": 80,
            "earliest_completion": "2026-11-20",
            "note": "盘点备注",
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["on_hand"] == 80
    assert edited.json()["note"] == "盘点备注"
    assert edited.json()["earliest_completion"] == "2026-11-20"
    after = client.get(f"/orders/{order['id']}").json()["order"]
    assert after["quantity"] == before["quantity"] == 100
    assert after["delivery_date"] == before["delivery_date"] == "2026-11-10"
    assert after["status"] == before["status"]
    assert after["customer_id"] == before["customer_id"]
    assert after["customer_name"] == "青禾成套"
    assert after["product"] == "小型断路器"

    created = client.post(
        "/warehouse",
        json={"product": "样品弹簧", "on_hand": 15, "earliest_completion": "2026-12-15", "note": "新到货"},
    )
    assert created.status_code == 200, created.text
    removed = client.delete("/warehouse", params={"product": "样品弹簧"})
    assert removed.status_code == 200, removed.text
    assert all(item["product"] != "样品弹簧" for item in client.get("/warehouse").json())

    created = client.post(
        "/warehouse",
        json={"product": "样品弹簧", "on_hand": 15, "earliest_completion": "2026-12-15", "note": "新到货"},
    )
    assert created.status_code == 200, created.text
    linked = client.post(
        "/orders",
        json={
            "customer_id": order["customer_id"],
            "order_no": "ORD-088",
            "product": "样品弹簧",
            "quantity": 4,
            "delivery_date": "2026-12-28",
            "available_qty": 100,
            "earliest_completion": "2026-12-01",
        },
    )
    assert linked.status_code == 200, linked.text
    in_use = client.delete("/warehouse", params={"product": "样品弹簧"})
    assert in_use.status_code == 409
    assert "ORD-088" in in_use.json()["detail"]
    assert "不能删除" in in_use.json()["detail"]
    assert client.delete(f"/orders/{linked.json()['id']}").status_code == 200
    trashed = client.delete("/warehouse", params={"product": "样品弹簧"})
    assert trashed.status_code == 409
    assert "回收站" in trashed.json()["detail"]
    assert client.delete(f"/orders/{linked.json()['id']}/purge").status_code == 200
    gone = client.delete("/warehouse", params={"product": "样品弹簧"})
    assert gone.status_code == 200, gone.text
    assert all(item["product"] != "样品弹簧" for item in client.get("/warehouse").json())


def test_manual_reset_keeps_warehouse_and_empties_workbench(client):
    from fastapi.testclient import TestClient

    from app.db import open_session, reset_engine
    from app.main import create_app
    from app.seed import prepare_manual_workspace

    db = open_session()
    try:
        prepare_manual_workspace(db)
        db.commit()
    finally:
        db.close()
    reset_engine()
    with TestClient(create_app()) as again:
        assert again.get("/orders").json() == []
        assert again.get("/customers").json() == []
        warehouse = again.get("/warehouse").json()
    names = {item["product"] for item in warehouse}
    assert names == {
        "小型断路器",
        "塑壳断路器",
        "交流接触器",
        "热继电器",
    }
    breaker = next(item for item in warehouse if item["product"] == "小型断路器")
    assert breaker["on_hand"] == 120
    assert breaker["earliest_completion"] == "2026-11-08"
    assert breaker["note"] == "A公司存货"

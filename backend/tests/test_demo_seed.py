import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def rich_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DANJI_DATABASE", str(tmp_path / "rich.db"))
    monkeypatch.setenv("DANJI_FIXTURE", "demo.json")
    monkeypatch.setenv("EXTRACT_MODE", "fixture")
    monkeypatch.setenv("DANJI_SEED_TASKS", "1")
    from app.db import reset_engine
    from app.main import create_app

    reset_engine()
    with TestClient(create_app()) as test_client:
        yield test_client


def test_rich_demo_seeds_customers_orders_and_trash(rich_client):
    orders = rich_client.get("/orders").json()
    assert len(orders) >= 6
    statuses = {item["status"] for item in orders}
    assert {"已确认", "待确认", "待确认取消", "已取消", "已关闭"} <= statuses
    trash = rich_client.get("/orders", params={"trashed": True}).json()
    assert any(item["order_no"] == "ORD-025" for item in trash)


def test_rich_demo_task_board_covers_columns(rich_client):
    tasks = rich_client.get("/tasks").json()
    statuses = {item["status"] for item in tasks}
    assert "待确认" in statuses
    assert "待处理" in statuses
    assert "进行中" in statuses
    assert "已完成" in statuses or "已关闭" in statuses
    by_status = {}
    for item in tasks:
        by_status.setdefault(item["status"], []).append(item)
    assert by_status["待确认"]
    assert by_status["待处理"]
    assert by_status["进行中"]
    assert any(item["status"] in {"已完成", "已关闭"} for item in tasks)
    conflict = [item for item in by_status["待确认"] if item["order_no"] == "ORD-018"]
    assert any("分批" in item["title"] or "额外 30" in item["title"] for item in conflict)
    shipping = next(item for item in tasks if item["order_no"] == "ORD-021")
    assert shipping["status"] == "进行中"
    assert "50" in shipping["title"]
    order = next(item for item in rich_client.get("/orders").json() if item["order_no"] == "ORD-018")
    assert order["quantity"] == 100
    assert order["delivery_date"] == "2026-11-10"
    assert order["status"] == "已确认"
    detail = rich_client.get(f"/orders/{order['id']}").json()
    assert detail["supply"]["on_hand"] == 120
    assert detail["supply"]["available_qty"] == 120


def test_rich_demo_warehouse_lists_all_catalog_products(rich_client):
    rows = {item["product"]: item for item in rich_client.get("/warehouse").json()}
    for product in ("小型断路器", "塑壳断路器", "交流接触器", "热继电器", "框架断路器", "微型断路器"):
        assert product in rows

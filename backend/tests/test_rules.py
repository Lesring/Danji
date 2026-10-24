from app.services.rules import EventIn, OpenTask, OrderSnap, analyze


def main_order() -> OrderSnap:
    return OrderSnap(
        quantity=100,
        delivery_date="2026-11-10",
        status="已确认",
        available_qty=120,
        earliest_completion="2026-11-08",
    )


def codes(order, events, tasks=None):
    result = analyze(order, events, tasks or [], "2026-10-21")
    return result, {item.code for item in result.risks}, {item.suggestion_key for item in result.suggestions}


def test_quantity_and_delivery_conflict_creates_three_tasks():
    events = [
        EventIn("quantity_change", "quantity", "100", "150", "客户期望", "增加到 150 件"),
        EventIn("delivery_change", "delivery_date", "2026-11-10", "2026-11-06", "客户期望", "11 月 6 日"),
        EventIn("commitment", proposal_origin="销售承诺", evidence_excerpt="我先跟客户说了", proposed_value="150"),
        EventIn("inventory_fact", "available_qty", "120", "120", "库存事实", "当前库存 120 件"),
    ]
    result, risk_codes, keys = codes(main_order(), events)
    assert {"qty_over_stock", "delivery_before_capacity", "unverified_commitment"} <= risk_codes
    assert keys == {"confirm_split_or_date", "verify_capacity", "mark_pending"}
    capacity = next(item for item in result.suggestions if item.suggestion_key == "verify_capacity")
    assert "30" in capacity.title
    assert any("120" in item.message and "150" in item.message for item in result.risks)
    assert any("2026-11-08" in item.message for item in result.risks)


def test_quantity_within_stock_has_no_stock_conflict():
    order = OrderSnap(80, "2026-11-20", "已确认", 200, "2026-11-15")
    events = [EventIn("quantity_change", "quantity", "80", "100", "客户期望", "增加到 100 件")]
    _result, risk_codes, keys = codes(order, events)
    assert "qty_over_stock" not in risk_codes
    assert "delivery_before_capacity" not in risk_codes
    assert "confirm_quantity" in keys


def test_cancellation_marks_order_and_closes_open_tasks():
    events = [EventIn("cancellation", "status", "已确认", "待确认取消", "客户期望", "先取消吧")]
    tasks = [OpenTask(3, "confirm_quantity", "确认数量由 80 改为 100", "待处理", "订单负责人")]
    result, risk_codes, _keys = codes(main_order(), events, tasks)
    assert "cancellation" in risk_codes
    assert any(item.order_status == "待确认取消" for item in result.suggestions)
    closing = next(item for item in result.suggestions if item.action == "close")
    assert closing.target_task_id == 3


def test_contradictory_delivery_values_are_kept_without_auto_pick():
    events = [
        EventIn("delivery_change", "delivery_date", "2026-11-10", "2026-11-06", "客户期望", "11 月 6 日"),
        EventIn("delivery_change", "delivery_date", "2026-11-10", "2026-11-10", "客户期望", "11 月 10 日"),
    ]
    result, risk_codes, keys = codes(main_order(), events)
    assert "contradiction" in risk_codes
    assert "确认交期" not in "".join(item.title for item in result.suggestions if item.suggestion_key == "confirm_delivery")
    assert "confirm_final_value" in keys
    assert "2026-11-06" in result.risks[0].message or any("2026-11-06" in item.message for item in result.risks)


def test_missing_owner_leaves_task_unassigned():
    events = [EventIn("owner_unclear", proposal_origin="内部沟通", evidence_excerpt="谁跟进还没定", explanation="包装未指定责任人")]
    result, risk_codes, keys = codes(main_order(), events)
    assert "owner_missing" in risk_codes
    assert "unassigned_followup" in keys
    suggestion = next(item for item in result.suggestions if item.suggestion_key == "unassigned_followup")
    assert suggestion.owner == ""


def test_committed_quantity_over_available_creates_supply_tasks():
    order = OrderSnap(100, "2026-11-10", "已确认", 80, "2026-11-08")
    result, risk_codes, keys = codes(order, [])
    assert "qty_over_stock" in risk_codes
    assert {"confirm_split_or_date", "verify_capacity", "mark_pending"} <= keys
    capacity = next(item for item in result.suggestions if item.suggestion_key == "verify_capacity")
    assert "20" in capacity.title
    assert any("已确认数量" in item.message and "80" in item.message for item in result.risks)


def test_committed_delivery_before_capacity_without_new_request():
    order = OrderSnap(100, "2026-11-06", "已确认", 120, "2026-11-08")
    result, risk_codes, keys = codes(order, [])
    assert "delivery_before_capacity" in risk_codes
    assert "confirm_split_or_date" in keys
    assert any("2026-11-06" in item.message for item in result.risks)


def test_released_order_does_not_treat_its_quantity_as_a_new_conflict():
    order = OrderSnap(100, "2026-11-10", "待确认取消", 80, "2026-11-08", track_commitment=False)
    _result, risk_codes, keys = codes(order, [])
    assert "qty_over_stock" not in risk_codes
    assert "verify_capacity" not in keys


def test_new_message_updates_existing_open_task():
    events = [
        EventIn(
            "commitment",
            proposal_origin="销售承诺",
            evidence_excerpt="分批交付",
            suggestion_key="confirm_split_or_date",
            explanation="客户还在考虑分批交付",
        )
    ]
    tasks = [OpenTask(9, "confirm_split_or_date", "确认客户是否接受分批交付或调整交期", "待处理", "销售")]
    result, _codes, _keys = codes(main_order(), events, tasks)
    suggestion = next(item for item in result.suggestions if item.suggestion_key == "confirm_split_or_date")
    assert suggestion.action == "update"
    assert suggestion.target_task_id == 9
    assert "未关闭任务" in suggestion.reason

from datetime import datetime, timedelta

import pytest

from extensions import db
from models import Order, OrderItem
from services import telegram_bot
from services.orders import (
    KH_TZ, OrderError, advance_status, cost_incomplete, driver_payback, fill_missing_costs,
    needs_attention, order_profit, range_bounds, set_cash_received, set_grab_fee, set_item_cost, summary,
)

_REAL_NOTIFY_CUSTOMER = telegram_bot.notify_customer_status


@pytest.fixture(autouse=True)
def sent(monkeypatch):
    """Record customer notifications instead of sending them."""
    calls = []
    monkeypatch.setattr(telegram_bot, "notify_customer_status", lambda o: calls.append(o.status))
    monkeypatch.setattr(telegram_bot, "notify_admin_new_order",
                        lambda o: pytest.fail("status changes must not notify the admin"))
    return calls


def test_happy_path_transitions_and_logs(order_factory, sent):
    o = order_factory(status="new")
    for s in ("confirmed", "packed", "delivering", "delivered"):
        advance_status(o, s)
    assert [l.to_status for l in o.status_logs] == ["new", "confirmed", "packed", "delivering", "delivered"]
    assert [l.from_status for l in o.status_logs] == [None, "new", "confirmed", "packed", "delivering"]
    assert sent == ["confirmed", "packed", "delivering", "delivered"]


def test_advance_sets_status_changed_at_and_persists(order_factory):
    o = order_factory(status="new", created_at=datetime(2026, 10, 14, 1, 0))
    when = datetime(2026, 10, 14, 5, 0)
    advance_status(o, "confirmed", now=when)
    fresh = db.session.get(Order, o.id)
    assert fresh.status == "confirmed" and fresh.status_changed_at == when
    assert fresh.status_logs[-1].created_at == when


def test_invalid_transitions(order_factory, sent):
    o = order_factory(status="new")
    for bad in ("packed", "delivered", "new", "bogus"):
        with pytest.raises(OrderError) as exc:
            advance_status(o, bad)
        assert exc.value.code == "invalid_transition"
    advance_status(o, "cancelled")
    for bad in ("confirmed", "cancelled", "new"):
        with pytest.raises(OrderError):
            advance_status(o, bad)
    assert o.status == "cancelled" and sent == ["cancelled"]
    assert len(o.status_logs) == 2


def test_no_backward_moves(order_factory):
    o = order_factory(status="packed")
    with pytest.raises(OrderError):
        advance_status(o, "confirmed")


def test_cancel_allowed_from_every_non_cancelled_status(order_factory):
    for status in ("new", "confirmed", "packed", "delivering", "delivered"):
        o = order_factory(status=status)
        advance_status(o, "cancelled")
        assert o.status == "cancelled"


def test_notify_failure_is_recorded_not_raised(order_factory, monkeypatch):
    monkeypatch.setattr(telegram_bot, "notify_customer_status", lambda o: "blocked")
    o = order_factory(status="new")
    advance_status(o, "confirmed")
    assert o.status == "confirmed" and db.session.get(Order, o.id).notify_error == "blocked"


def test_notify_exception_is_recorded_not_raised(order_factory, monkeypatch):
    def boom(order):
        raise RuntimeError("network down")

    monkeypatch.setattr(telegram_bot, "notify_customer_status", boom)
    o = order_factory(status="new")
    advance_status(o, "confirmed")
    assert db.session.get(Order, o.id).status == "confirmed"
    assert db.session.get(Order, o.id).notify_error == "network down"


def test_successful_notification_round_clears_old_notify_error(order_factory):
    o = order_factory(status="new")
    o.notify_error = "forbidden"
    db.session.commit()
    advance_status(o, "confirmed")  # the autouse `sent` fixture makes the customer send succeed
    assert db.session.get(Order, o.id).notify_error is None


def test_skipped_customer_notification_also_clears_notify_error(order_factory, make_customer, monkeypatch):
    # The real sender, which skips (returns None) when can_message is False.
    monkeypatch.setattr(telegram_bot, "notify_customer_status", _REAL_NOTIFY_CUSTOMER)
    customer = make_customer(telegram_id=4242, can_message=False)
    o = order_factory(customer=customer, status="new")
    o.notify_error = "no_admin_chat"
    db.session.commit()
    advance_status(o, "confirmed")
    assert db.session.get(Order, o.id).notify_error is None


def test_profit_with_and_without_voucher(order_factory):
    assert order_profit(order_factory(subtotal=24, total_cost=14, fee_charged=2, fee_driver=2)) == 10.0
    assert order_profit(order_factory(subtotal=24, total_cost=14, fee_charged=0, fee_driver=2)) == 8.0


def test_profit_ignores_negative_subsidy_and_missing_fees(order_factory):
    assert order_profit(order_factory(subtotal=24, total_cost=14, fee_charged=5, fee_driver=2)) == 10.0
    assert order_profit(order_factory(subtotal=24, total_cost=14, fee_charged=None, fee_driver=None)) == 10.0


def test_grab_fee_sets_both_and_never_changes_profit(order_factory):
    o = order_factory(delivery_type="grab", subtotal=20, total_cost=12, fee_charged=None, fee_driver=None)
    set_grab_fee(o, 3.5)
    assert (o.delivery_fee_driver, o.delivery_fee_charged, o.total_to_collect, order_profit(o)) == (3.5, 3.5, 23.5, 8.0)
    assert db.session.get(Order, o.id).total_to_collect == 23.5


def test_grab_fee_rounds_and_can_be_corrected(order_factory):
    o = order_factory(delivery_type="grab", subtotal=20, fee_charged=None, fee_driver=None)
    set_grab_fee(o, 3.456)
    assert o.delivery_fee_driver == 3.46 and o.total_to_collect == 23.46
    set_grab_fee(o, 0)
    assert o.delivery_fee_driver == 0 and o.total_to_collect == 20.0


@pytest.mark.parametrize("fee", [-0.01, None, "abc", float("nan"), float("inf"), True])
def test_grab_fee_rejects_bad_values(order_factory, fee):
    o = order_factory(delivery_type="grab", fee_charged=None, fee_driver=None)
    with pytest.raises(OrderError) as exc:
        set_grab_fee(o, fee)
    assert exc.value.code == "invalid_fee" and o.delivery_fee_driver is None


def test_grab_fee_rejects_standard_orders(order_factory):
    o = order_factory(delivery_type="standard")
    with pytest.raises(OrderError) as exc:
        set_grab_fee(o, 3)
    assert exc.value.code == "invalid_fee" and o.delivery_fee_driver == 2.0


def test_cash_received_only_when_delivered(order_factory):
    o = order_factory(status="packed")
    with pytest.raises(OrderError) as exc:
        set_cash_received(o, True)
    assert exc.value.code == "invalid_transition" and not o.cash_received


def test_cash_received_mark_and_unmark(order_factory):
    o = order_factory(status="delivered")
    when = datetime(2026, 10, 14, 5, 0)
    set_cash_received(o, True, now=when)
    fresh = db.session.get(Order, o.id)
    assert fresh.cash_received is True and fresh.cash_received_at == when
    set_cash_received(o, False)
    fresh = db.session.get(Order, o.id)
    assert fresh.cash_received is False and fresh.cash_received_at is None


def test_driver_payback(order_factory):
    assert driver_payback(order_factory(subtotal=24, fee_charged=2, fee_driver=2)) == 24.0
    assert driver_payback(order_factory(subtotal=24, fee_charged=0, fee_driver=2)) == 22.0
    assert driver_payback(order_factory(subtotal=20, fee_charged=None, fee_driver=None)) == 20.0


def test_needs_attention_rules(order_factory):
    now = datetime(2026, 10, 14, 15, 0)  # UTC == 22:00 KH
    assert "stuck_new" in needs_attention(order_factory(status="new", created_at=now - timedelta(hours=3, minutes=1)), now)
    assert needs_attention(order_factory(status="new", created_at=now - timedelta(hours=2)), now) == []
    assert "stuck_delivering" in needs_attention(order_factory(status="delivering", status_changed_at=datetime(2026, 10, 14, 3, 0)), now)
    assert "cash_overdue" in needs_attention(order_factory(status="delivered", status_changed_at=now - timedelta(hours=25)), now)
    assert "missing_cost" in needs_attention(order_factory(item_cost=None), now)
    assert needs_attention(order_factory(status="cancelled", item_cost=None), now) == []


def test_needs_attention_boundaries(order_factory):
    now = datetime(2026, 10, 14, 15, 0)
    assert needs_attention(order_factory(status="delivered", status_changed_at=now - timedelta(hours=23)), now) == []
    paid = order_factory(status="delivered", status_changed_at=now - timedelta(hours=30), cash_received=True)
    assert needs_attention(paid, now) == []
    # Before 21:00 KH (13:59 UTC = 20:59 KH) a delivering order is fine; 14:00 UTC = 21:00 KH is stuck.
    delivering = order_factory(status="delivering", status_changed_at=datetime(2026, 10, 14, 3, 0))
    assert needs_attention(delivering, datetime(2026, 10, 14, 13, 59)) == []
    assert needs_attention(delivering, datetime(2026, 10, 14, 14, 0)) == ["stuck_delivering"]
    # Still delivering the next morning counts as stuck.
    assert needs_attention(delivering, datetime(2026, 10, 15, 3, 0)) == ["stuck_delivering"]


def test_stuck_delivering_uses_kh_date_of_status_change(order_factory):
    # 18:00 UTC on the 13th is 01:00 KH on the 14th: the deadline is 21:00 KH on the 14th.
    o = order_factory(status="delivering", status_changed_at=datetime(2026, 10, 13, 18, 0))
    assert needs_attention(o, datetime(2026, 10, 14, 13, 0)) == []
    assert needs_attention(o, datetime(2026, 10, 14, 14, 0)) == ["stuck_delivering"]


def test_grab_fee_missing(order_factory):
    now = datetime(2026, 10, 14, 5, 0)
    o = order_factory(status="delivered", delivery_type="grab", fee_charged=None, fee_driver=None, cash_received=True)
    assert needs_attention(o, now) == ["grab_fee_missing"]
    set_grab_fee(o, 3)
    assert needs_attention(o, now) == []
    pending = order_factory(status="delivering", delivery_type="grab", fee_charged=None, fee_driver=None)
    assert "grab_fee_missing" not in needs_attention(pending, now)


def test_needs_attention_reports_every_reason(order_factory):
    now = datetime(2026, 10, 14, 15, 0)
    o = order_factory(status="delivered", delivery_type="grab", fee_charged=None, fee_driver=None,
                      item_cost=None, status_changed_at=now - timedelta(hours=30))
    assert needs_attention(o, now) == ["missing_cost", "cash_overdue", "grab_fee_missing"]


def test_range_bounds_today_is_cambodia_day_in_naive_utc():
    now = datetime(2026, 10, 14, 15, 0)  # 22:00 KH on 14 Oct
    assert range_bounds("today", now) == (datetime(2026, 10, 13, 17, 0), datetime(2026, 10, 14, 17, 0))
    assert KH_TZ.utcoffset(None) == timedelta(hours=7)


def test_range_bounds_7d_and_month():
    now = datetime(2026, 10, 14, 15, 0)
    assert range_bounds("7d", now) == (datetime(2026, 10, 7, 17, 0), datetime(2026, 10, 14, 17, 0))
    assert range_bounds("month", now) == (datetime(2026, 9, 30, 17, 0), datetime(2026, 10, 31, 17, 0))
    # December rolls into the next year; 18:00 UTC on 31 Dec is already 1 Jan in Cambodia.
    assert range_bounds("month", datetime(2026, 12, 15, 0, 0)) == (datetime(2026, 11, 30, 17, 0), datetime(2026, 12, 31, 17, 0))
    assert range_bounds("month", datetime(2026, 12, 31, 18, 0)) == (datetime(2026, 12, 31, 17, 0), datetime(2027, 1, 31, 17, 0))


def test_range_bounds_rejects_unknown_key():
    with pytest.raises(OrderError) as exc:
        range_bounds("year")
    assert exc.value.code == "invalid_range"


def test_summary_uses_cambodia_day_and_excludes_cancelled(order_factory):
    now = datetime(2026, 10, 14, 15, 0)  # 22:00 KH on 14 Oct
    order_factory(created_at=datetime(2026, 10, 13, 18, 0), subtotal=10, total_cost=6)  # 01:00 KH 14 Oct -> today
    order_factory(created_at=datetime(2026, 10, 13, 16, 0), subtotal=99, total_cost=1)  # 23:00 KH 13 Oct -> not today
    order_factory(created_at=now, status="cancelled", subtotal=50, total_cost=10)
    assert summary("today", now) == {"orders": 1, "sales": 10.0, "profit": 4.0, "orders_missing_cost": 0,
                                     "cash_with_drivers": 0.0}


def test_summary_ranges_and_cash_with_drivers_ignores_range(order_factory):
    now = datetime(2026, 10, 14, 15, 0)
    order_factory(created_at=datetime(2026, 10, 10, 5, 0), subtotal=20, total_cost=10)   # in 7d, not today
    order_factory(created_at=datetime(2026, 10, 1, 5, 0), subtotal=30, total_cost=10)    # month only
    # Old delivered order with cash outstanding still counts as cash with drivers.
    order_factory(created_at=datetime(2026, 8, 1, 5, 0), status="delivered", subtotal=24, fee_charged=2, fee_driver=2)
    order_factory(created_at=now, status="delivered", subtotal=24, fee_charged=0, fee_driver=2, cash_received=True)
    seven = summary("7d", now)
    assert seven["orders"] == 2 and seven["sales"] == 44.0
    month = summary("month", now)
    assert month["orders"] == 3 and month["sales"] == 74.0
    assert month["cash_with_drivers"] == seven["cash_with_drivers"] == 24.0


def test_summary_counts_orders_missing_cost(order_factory):
    now = datetime(2026, 10, 14, 5, 0)
    order_factory(created_at=now, item_cost=None)
    order_factory(created_at=now, item_cost=None, status="cancelled")  # cancelled: not counted
    order_factory(created_at=now)
    assert summary("today", now)["orders_missing_cost"] == 1


# ---- Cost prices ---------------------------------------------------------------

def _add_item(order, unit_cost, quantity=1, product_id=None, variant_id=None):
    item = OrderItem(name="Extra", options_text="", unit_price=5.0, unit_cost=unit_cost,
                     quantity=quantity, product_id=product_id, variant_id=variant_id)
    order.items.append(item)
    db.session.commit()
    return item


def test_set_item_cost_sets_rounded_cost_and_recomputes_total(order_factory):
    o = order_factory(item_cost=6.0, total_cost=6.0)
    extra = _add_item(o, None, quantity=3)
    set_item_cost(o, extra, 1.239)
    o = db.session.get(Order, o.id)
    assert db.session.get(OrderItem, extra.id).unit_cost == 1.24
    assert o.total_cost == 9.72  # 6 + 1.24 * 3
    assert cost_incomplete(o) is False
    assert "missing_cost" not in needs_attention(o)


def test_set_item_cost_can_correct_an_existing_cost(order_factory):
    o = order_factory(item_cost=6.0, total_cost=6.0)
    set_item_cost(o, o.items[0], 4)
    assert db.session.get(Order, o.id).total_cost == 4.0


@pytest.mark.parametrize("cost", [-1, float("nan"), float("inf"), True, "abc", None])
def test_set_item_cost_rejects_bad_values(order_factory, cost):
    o = order_factory(item_cost=None, total_cost=0)
    with pytest.raises(OrderError) as exc:
        set_item_cost(o, o.items[0], cost)
    assert exc.value.code == "invalid_cost"
    assert db.session.get(OrderItem, o.items[0].id).unit_cost is None


def test_set_item_cost_rejects_item_of_another_order(order_factory):
    a = order_factory()
    b = order_factory()
    with pytest.raises(OrderError):
        set_item_cost(a, b.items[0], 1)


def test_fill_missing_costs_copies_current_product_and_variant_costs(order_factory, make_product):
    base, _ = make_product(cost=3.333)
    with_variant, variant = make_product(with_variant=True, variant_cost=4.0)
    no_cost, _ = make_product(cost=None)

    o = order_factory(item_cost=6.0, total_cost=6.0)
    from_base = _add_item(o, None, quantity=2, product_id=base.id)
    from_variant = _add_item(o, None, product_id=with_variant.id, variant_id=variant.id)
    still_missing = _add_item(o, None, product_id=no_cost.id)
    cancelled = order_factory(status="cancelled", item_cost=None)
    cancelled.items[0].product_id = base.id
    db.session.commit()

    assert fill_missing_costs() == 2
    assert db.session.get(OrderItem, from_base.id).unit_cost == 3.33
    assert db.session.get(OrderItem, from_variant.id).unit_cost == 4.0
    assert db.session.get(OrderItem, still_missing.id).unit_cost is None
    assert db.session.get(OrderItem, cancelled.items[0].id).unit_cost is None
    assert db.session.get(Order, o.id).total_cost == 16.66  # 6 + 3.33 * 2 + 4


def test_fill_missing_costs_with_nothing_to_fill(order_factory):
    order_factory()
    assert fill_missing_costs() == 0

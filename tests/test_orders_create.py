import pytest

from extensions import db
from models import Customer, Order, OrderItem, Setting, Voucher, VoucherRedemption
from models.ProductVariant import ProductVariantOption, VariantOption, VariantType
from tests.helpers import post_order


def make_voucher(code="FREE", min_spend=0.0, usage_limit=None):
    voucher = Voucher(code=code, min_spend=min_spend, status="true", usage_limit=usage_limit)
    db.session.add(voucher)
    db.session.commit()
    return voucher


def test_create_order_prices_from_database(client, login_as, make_customer, make_product):
    _, variant = make_product(with_variant=True, variant_price=12.0, variant_cost=7.0)
    login_as(make_customer())
    r = post_order(client, items=[{"variantId": variant.id, "quantity": 2, "price": 0.01}])
    assert r.status_code == 201
    o = Order.query.one()
    assert r.json == {"ok": True, "order_id": o.id, "url": f"/my-orders/{o.id}?new=1"}
    assert (o.subtotal, o.total_cost, o.delivery_fee_charged, o.delivery_fee_driver,
            o.total_to_collect) == (24.0, 14.0, 2.0, 2.0, 26.0)
    assert o.status == "new" and o.items[0].unit_price == 12.0 and o.status_logs[0].to_status == "new"
    assert o.status_logs[0].from_status is None and len(o.status_logs) == 1
    assert o.delivery_type == "standard" and o.voucher_code is None and o.notify_error is None


def test_create_order_base_product_and_snapshot(client, login_as, make_customer, make_product):
    product, _ = make_product(price=10.0, cost=None)
    login_as(make_customer())
    r = post_order(client, items=[{"variantId": f"base_{product.id}", "quantity": 3}])
    assert r.status_code == 201
    item = OrderItem.query.one()
    assert (item.product_id, item.variant_id, item.name, item.options_text) == (
        product.id, None, product.name, "")
    assert (item.unit_price, item.unit_cost, item.quantity) == (10.0, None, 3)
    o = Order.query.one()
    assert (o.subtotal, o.total_cost, o.total_to_collect) == (30.0, 0.0, 32.0)


def test_create_order_variant_options_text(client, login_as, make_customer, make_product):
    product, variant = make_product(with_variant=True)
    color = VariantType(name="Color", slug="color")
    kind = VariantType(name="Type", slug="type")
    db.session.add_all([color, kind])
    db.session.flush()
    black = VariantOption(variant_type_id=color.id, value="Black")
    mic = VariantOption(variant_type_id=kind.id, value="With Mic")
    db.session.add_all([black, mic])
    db.session.flush()
    db.session.add_all([ProductVariantOption(product_variant_id=variant.id, variant_option_id=black.id),
                        ProductVariantOption(product_variant_id=variant.id, variant_option_id=mic.id)])
    db.session.commit()
    login_as(make_customer())
    assert post_order(client, items=[{"variantId": variant.id, "quantity": 1}]).status_code == 201
    item = OrderItem.query.one()
    assert item.options_text == "Black / With Mic" and item.variant_id == variant.id
    assert item.product_id == product.id


def test_create_order_rounds_money(client, login_as, make_customer, make_product):
    _, variant = make_product(with_variant=True, variant_price=0.1, variant_cost=0.07)
    login_as(make_customer())
    assert post_order(client, items=[{"variantId": variant.id, "quantity": 3}]).status_code == 201
    o = Order.query.one()
    assert (o.subtotal, o.total_cost, o.total_to_collect) == (0.3, 0.21, 2.3)


def test_create_order_uses_fee_setting(client, login_as, make_customer, make_product):
    Setting.set_val("standard_delivery_fee", "1.50")
    _, variant = make_product(with_variant=True)
    login_as(make_customer())
    assert post_order(client, items=[{"variantId": variant.id, "quantity": 1}]).status_code == 201
    o = Order.query.one()
    assert (o.delivery_fee_charged, o.delivery_fee_driver, o.total_to_collect) == (1.5, 1.5, 13.5)


def test_create_order_rejects_bad_items(client, login_as, make_customer, make_product):
    p_inactive, _ = make_product(status="false")
    p_var, v = make_product(with_variant=True)
    login_as(make_customer())
    for items in ([], [{"variantId": 999999, "quantity": 1}], [{"variantId": f"base_{p_inactive.id}", "quantity": 1}],
                  [{"variantId": f"base_{p_var.id}", "quantity": 1}], [{"variantId": v.id, "quantity": 0}],
                  [{"variantId": v.id, "quantity": 100}], [{"variantId": v.id, "quantity": "2"}]):
        r = post_order(client, items=items)
        assert r.status_code == 400 and r.json["error"] == "invalid_items", items
    assert Order.query.count() == 0


def test_create_order_rejects_malformed_items(client, login_as, make_customer, make_product):
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    for items in (None, {"variantId": v.id, "quantity": 1}, ["x"], [{"quantity": 1}],
                  [{"variantId": v.id}], [{"variantId": v.id, "quantity": True}],
                  [{"variantId": v.id, "quantity": 1.5}], [{"variantId": True, "quantity": 1}],
                  [{"variantId": str(v.id), "quantity": 1}], [{"variantId": "base_x", "quantity": 1}],
                  [{"variantId": "base_", "quantity": 1}], [{"variantId": 1.0, "quantity": 1}],
                  [{"variantId": v.id, "quantity": 1}] * 51):
        r = post_order(client, items=items)
        assert r.status_code == 400 and r.json == {"ok": False, "error": "invalid_items"}, items
    assert Order.query.count() == 0
    assert post_order(client, items=[{"variantId": v.id, "quantity": 99}] * 50).status_code == 201


def test_create_order_rejects_inactive_variant_or_parent(client, login_as, make_customer, make_product):
    p1, inactive_variant = make_product(with_variant=True)
    inactive_variant.status = "false"
    p2, v_of_inactive_product = make_product(with_variant=True)
    p2.status = "false"
    db.session.commit()
    login_as(make_customer())
    for vid in (inactive_variant.id, v_of_inactive_product.id):
        r = post_order(client, items=[{"variantId": vid, "quantity": 1}])
        assert r.status_code == 400 and r.json["error"] == "invalid_items"
    # p1 has no active variants left, so its base product is orderable.
    assert post_order(client, items=[{"variantId": f"base_{p1.id}", "quantity": 1}]).status_code == 201


def test_order_requires_sign_in(client, make_product):
    _, v = make_product(with_variant=True)
    r = post_order(client, items=[{"variantId": v.id, "quantity": 1}])
    assert r.status_code == 401 and r.json == {"ok": False, "error": "sign_in_required"}
    assert Order.query.count() == 0


def test_order_requires_same_origin(client, login_as, make_customer, make_product):
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    r = client.post("/api/orders", json={"items": [{"variantId": v.id, "quantity": 1}],
                                         "delivery_type": "standard", "phone": "012345678",
                                         "address": "PP"})
    assert r.status_code == 403 and Order.query.count() == 0


def test_order_requires_phone_and_address(client, login_as, make_customer, make_product):
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    items = [{"variantId": v.id, "quantity": 1}]
    for phone in ("", "   ", None, 12345678):
        r = post_order(client, items=items, phone=phone)
        assert r.status_code == 400 and r.json["error"] == "phone_required"
    for address in ("", "  ", None):
        r = post_order(client, items=items, address=address)
        assert r.status_code == 400 and r.json["error"] == "address_required"
    assert Order.query.count() == 0


def test_order_rejects_unknown_delivery_type(client, login_as, make_customer, make_product):
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    for delivery_type in ("express", "", None, "Standard"):
        r = post_order(client, items=[{"variantId": v.id, "quantity": 1}], delivery_type=delivery_type)
        assert r.status_code == 400 and r.json["error"] == "invalid_delivery"
    assert Order.query.count() == 0


def test_items_are_validated_before_other_fields(client, login_as, make_customer):
    login_as(make_customer())
    r = post_order(client, items=[], delivery_type="x", phone="", address="", voucher_code="NOPE")
    assert r.json["error"] == "invalid_items"


def test_order_saves_contact_to_customer(client, login_as, make_customer, make_product):
    _, v = make_product(with_variant=True)
    customer = login_as(make_customer())
    r = post_order(client, items=[{"variantId": v.id, "quantity": 1}], phone=" 012 345 678 ",
                   address=" St 123 ", maps_link="https://maps.app.goo.gl/x")
    assert r.status_code == 201
    o = Order.query.one()
    assert (o.customer_id, o.phone, o.address, o.maps_link) == (
        customer.id, "012 345 678", "St 123", "https://maps.app.goo.gl/x")
    c = db.session.get(Customer, customer.id)
    assert (c.phone, c.address, c.maps_link) == ("012 345 678", "St 123", "https://maps.app.goo.gl/x")


def test_grab_order_fees_unset(client, login_as, make_customer, make_product):
    _, v = make_product(with_variant=True, variant_price=12.0)
    login_as(make_customer())
    r = post_order(client, items=[{"variantId": v.id, "quantity": 1}], delivery_type="grab")
    assert r.status_code == 201
    o = Order.query.one()
    assert o.delivery_type == "grab"
    assert o.delivery_fee_charged is None and o.delivery_fee_driver is None
    assert o.total_to_collect == o.subtotal == 12.0


def test_voucher_gives_free_standard_delivery(client, login_as, make_customer, make_product):
    voucher = make_voucher(min_spend=0.0)
    _, v = make_product(with_variant=True, variant_price=12.0)
    login_as(make_customer())
    r = post_order(client, items=[{"variantId": v.id, "quantity": 1}], voucher_code=" free ",
                   phone="+855 12 345 678")
    assert r.status_code == 201
    o = Order.query.one()
    assert (o.delivery_fee_charged, o.delivery_fee_driver, o.total_to_collect) == (0.0, 2.0, 12.0)
    assert o.voucher_code == "FREE"
    assert db.session.get(Voucher, voucher.id).usage_count == 1
    assert VoucherRedemption.query.one().phone_number == "012345678"


def test_blank_voucher_code_means_no_voucher(client, login_as, make_customer, make_product):
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    r = post_order(client, items=[{"variantId": v.id, "quantity": 1}], voucher_code="  ")
    assert r.status_code == 201 and Order.query.one().voucher_code is None


def test_voucher_errors_block_order(client, login_as, make_customer, make_product):
    make_voucher(min_spend=50.0)
    _, v = make_product(with_variant=True, variant_price=12.0)
    login_as(make_customer())
    items = [{"variantId": v.id, "quantity": 1}]
    for code, error in (("FREE", "voucher_min_spend"), ("NOPE", "voucher_invalid"), (5, "voucher_invalid")):
        r = post_order(client, items=items, voucher_code=code)
        assert r.status_code == 400 and r.json["error"] == error
    assert Order.query.count() == 0 and VoucherRedemption.query.count() == 0


def test_voucher_checked_after_contact_fields(client, login_as, make_customer, make_product):
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    r = post_order(client, items=[{"variantId": v.id, "quantity": 1}], address="", voucher_code="NOPE")
    assert r.json["error"] == "address_required"


def test_voucher_rejected_for_grab(client, login_as, make_customer, make_product):
    voucher = make_voucher()
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    r = post_order(client, items=[{"variantId": v.id, "quantity": 1}], delivery_type="grab",
                   voucher_code="FREE")
    assert r.status_code == 400 and r.json == {"ok": False, "error": "voucher_standard_only"}
    assert db.session.get(Voucher, voucher.id).usage_count == 0
    assert Order.query.count() == 0 and VoucherRedemption.query.count() == 0


def test_voucher_redeemed_once(client, login_as, make_customer, make_product):
    voucher = make_voucher()
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    items = [{"variantId": v.id, "quantity": 1}]
    assert post_order(client, items=items, voucher_code="FREE").status_code == 201
    r = post_order(client, items=items, voucher_code="FREE", phone="+85512345678")
    assert r.status_code == 400 and r.json["error"] == "voucher_already_used"
    assert db.session.get(Voucher, voucher.id).usage_count == 1
    assert Order.query.count() == 1 and VoucherRedemption.query.count() == 1


def test_failed_commit_rolls_back_redemption(client, login_as, make_customer, make_product, monkeypatch):
    voucher = make_voucher()
    _, v = make_product(with_variant=True)
    customer = login_as(make_customer())

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db.session, "commit", boom)
    with pytest.raises(RuntimeError):
        post_order(client, items=[{"variantId": v.id, "quantity": 1}], voucher_code="FREE")
    monkeypatch.undo()
    # Tests share one session with the app, so pending rows would still be visible here.
    assert Order.query.count() == 0 and OrderItem.query.count() == 0
    assert VoucherRedemption.query.count() == 0
    assert db.session.get(Voucher, voucher.id).usage_count == 0
    assert db.session.get(Customer, customer.id).phone is None


def test_voucher_used_up_since_check_rolls_back(client, login_as, make_customer, make_product, monkeypatch):
    import services.orders as orders

    voucher = Voucher(code="FREE", min_spend=0.0, status="true", usage_limit=1, usage_count=1)
    db.session.add(voucher)
    db.session.commit()
    _, v = make_product(with_variant=True)
    customer = login_as(make_customer())
    # Simulate another order taking the last use between check and redeem.
    monkeypatch.setattr(orders, "check_voucher", lambda *args: (voucher, None))
    r = post_order(client, items=[{"variantId": v.id, "quantity": 1}], voucher_code="FREE")
    assert r.status_code == 400 and r.json["error"] == "voucher_limit_reached"
    assert Order.query.count() == 0 and OrderItem.query.count() == 0
    assert VoucherRedemption.query.count() == 0
    assert db.session.get(Voucher, voucher.id).usage_count == 1
    assert db.session.get(Customer, customer.id).phone is None


def test_order_saved_when_telegram_fails(client, login_as, make_customer, make_product, monkeypatch):
    import services.telegram_bot as tb
    monkeypatch.setattr(tb, "notify_admin_new_order", lambda o: "timeout")
    monkeypatch.setattr(tb, "notify_customer_status", lambda o: None)
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    r = post_order(client, items=[{"variantId": v.id, "quantity": 1}])
    assert r.status_code == 201 and Order.query.one().notify_error == "timeout"


def test_order_saved_when_notify_raises(client, login_as, make_customer, make_product, monkeypatch):
    import services.telegram_bot as tb

    def boom(order):
        raise RuntimeError("network down")

    monkeypatch.setattr(tb, "notify_admin_new_order", boom)
    monkeypatch.setattr(tb, "notify_customer_status", lambda o: "blocked")
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    r = post_order(client, items=[{"variantId": v.id, "quantity": 1}])
    assert r.status_code == 201
    assert Order.query.one().notify_error == "network down; blocked"


def test_notifications_called_after_commit(client, login_as, make_customer, make_product, monkeypatch):
    import services.telegram_bot as tb
    seen = []

    def record(order):
        # The order is already committed when notifications run.
        seen.append(db.session.get(Order, order.id) is not None and order.id is not None)
        return None

    monkeypatch.setattr(tb, "notify_admin_new_order", record)
    monkeypatch.setattr(tb, "notify_customer_status", record)
    _, v = make_product(with_variant=True)
    login_as(make_customer())
    assert post_order(client, items=[{"variantId": v.id, "quantity": 1}]).status_code == 201
    assert seen == [True, True] and Order.query.one().notify_error is None


def test_order_rate_limited_per_customer(client, login_as, make_customer):
    from extensions import limiter

    login_as(make_customer())
    limiter.reset()
    limiter.enabled = True
    try:
        codes = [post_order(client, items=[]).status_code for _ in range(11)]
        login_as(make_customer(telegram_id=222))  # same IP, different customer
        other = post_order(client, items=[]).status_code
    finally:
        limiter.enabled = False
        limiter.reset()
    assert codes == [400] * 10 + [429] and other == 400


def test_use_voucher_endpoint_removed(client):
    assert client.post("/api/use_voucher", json={}).status_code in (404, 405)

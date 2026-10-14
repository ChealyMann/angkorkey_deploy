from datetime import datetime, timedelta

import pytest

from extensions import db
from models import Order, Setting
from services import telegram_bot

ORIGIN = {"Origin": "http://localhost"}
BASE = "/admin/mobile/orders"


def html_of(response):
    return response.get_data(as_text=True)


def test_orders_requires_admin(client):
    assert client.get(BASE + "/").status_code == 302


def test_list_shows_summary_and_cards(admin_client, order_factory):
    order_factory(status="new", subtotal=42, total_cost=33)
    html = html_of(admin_client.get(BASE + "/?range=today&tab=new"))
    assert "$42.00" in html and "$9.00" in html and "Confirm" in html


def test_card_content(admin_client, order_factory, make_customer):
    customer = make_customer(telegram_id=5, first_name="Pisey")
    customer.username = "pisey_k"
    db.session.commit()
    order_factory(customer=customer, status="new", subtotal=27, total_cost=21,
                  created_at=datetime(2026, 10, 14, 3, 12))
    html = html_of(admin_client.get(BASE + "/?range=7d&tab=new"))
    assert "Pisey" in html and "10:12" in html  # 03:12 UTC is 10:12 in Cambodia
    assert "https://t.me/pisey_k" in html and "Cancel" in html


def test_telegram_link_falls_back_to_user_id(admin_client, order_factory, make_customer):
    order_factory(customer=make_customer(telegram_id=777))
    assert "tg://user?id=777" in html_of(admin_client.get(BASE + "/"))


def test_cancel_wording_differs_for_delivered(admin_client, order_factory):
    order_factory(status="delivered")
    html = html_of(admin_client.get(BASE + "/?tab=done&range=today"))
    assert "already delivered" in html
    order_factory(status="new")
    assert "already delivered" not in html_of(admin_client.get(BASE + "/?tab=new"))


@pytest.mark.parametrize("query", ["range=bogus", "tab=bogus", "range=x&tab=y", "page=abc", "page=-3", "page=999"])
def test_unknown_query_values_fall_back(admin_client, order_factory, query):
    order_factory(status="new")
    response = admin_client.get(f"{BASE}/?{query}")
    assert response.status_code == 200
    assert 'id="order-1"' in html_of(response)


def test_tab_counts_and_filtering(admin_client, order_factory):
    order_factory(status="new")
    order_factory(status="new")
    order_factory(status="packed")
    html = html_of(admin_client.get(BASE + "/?tab=packed"))
    assert "New 2" in html and "Packed 1" in html


def test_active_tabs_show_old_orders_but_done_follows_range(admin_client, order_factory):
    old = datetime.utcnow() - timedelta(days=20)
    order_factory(status="confirmed", created_at=old, status_changed_at=old)
    done = order_factory(status="delivered", created_at=old, status_changed_at=old)
    assert f'id="order-{done.id}"' not in html_of(admin_client.get(BASE + "/?tab=done&range=today"))
    assert "Confirmed 1" in html_of(admin_client.get(BASE + "/?tab=confirmed&range=today"))


def test_pagination_30_per_page(admin_client, order_factory):
    for _ in range(31):
        order_factory(status="new")
    first = html_of(admin_client.get(BASE + "/?tab=new"))
    second = html_of(admin_client.get(BASE + "/?tab=new&page=2"))
    assert first.count('class="order-card') == 30
    assert second.count('class="order-card') == 1


def test_advance_and_invalid_transition(admin_client, order_factory):
    o = order_factory(status="new")
    admin_client.post(f"{BASE}/{o.id}/advance", data={"to_status": "confirmed"}, headers=ORIGIN)
    assert db.session.get(Order, o.id).status == "confirmed"
    r = admin_client.post(f"{BASE}/{o.id}/advance", data={"to_status": "delivered"},
                          headers=ORIGIN, follow_redirects=True)
    assert db.session.get(Order, o.id).status == "confirmed"
    assert "not allowed" in html_of(r)


def test_cancel_via_advance(admin_client, order_factory):
    o = order_factory(status="packed")
    admin_client.post(f"{BASE}/{o.id}/advance", data={"to_status": "cancelled"}, headers=ORIGIN)
    assert db.session.get(Order, o.id).status == "cancelled"


def test_post_routes_require_same_origin(admin_client, order_factory):
    o = order_factory(status="new")
    for path, data in [("advance", {"to_status": "confirmed"}), ("grab-fee", {"fee": "3"}),
                       ("cash", {"received": "1"}), ("note", {"admin_note": "x"})]:
        assert admin_client.post(f"{BASE}/{o.id}/{path}", data=data).status_code == 403
        assert admin_client.post(f"{BASE}/{o.id}/{path}", data=data,
                                 headers={"Origin": "https://evil.example"}).status_code == 403
    assert admin_client.post(f"{BASE}/test-message").status_code == 403
    assert db.session.get(Order, o.id).status == "new"


def test_post_requires_login(client, order_factory):
    o = order_factory(status="new")
    r = client.post(f"{BASE}/{o.id}/advance", data={"to_status": "confirmed"}, headers=ORIGIN)
    assert r.status_code == 302 and db.session.get(Order, o.id).status == "new"


def test_redirect_back_uses_same_host_referrer_only(admin_client, order_factory):
    o = order_factory(status="new")
    r = admin_client.post(f"{BASE}/{o.id}/advance", data={"to_status": "confirmed"},
                          headers={**ORIGIN, "Referer": f"http://localhost{BASE}/{o.id}"})
    assert r.headers["Location"].endswith(f"{BASE}/{o.id}")
    o2 = order_factory(status="new")
    r = admin_client.post(f"{BASE}/{o2.id}/advance", data={"to_status": "confirmed"},
                          headers={**ORIGIN, "Referer": "https://evil.example/x"})
    assert "evil.example" not in r.headers["Location"] and BASE in r.headers["Location"]


def test_unknown_order_is_404(admin_client):
    assert admin_client.get(BASE + "/999").status_code == 404
    assert admin_client.post(BASE + "/999/advance", data={"to_status": "confirmed"},
                             headers=ORIGIN).status_code == 404


def test_attention_tab_lists_missing_cost(admin_client, order_factory):
    order_factory(item_cost=None)
    html = html_of(admin_client.get(BASE + "/?tab=attention"))
    assert "no cost price" in html.lower()


def test_attention_tab_excludes_cancelled_and_ok_orders(admin_client, order_factory):
    bad = order_factory(item_cost=None, status="cancelled")
    ok = order_factory()
    html = html_of(admin_client.get(BASE + "/?tab=attention"))
    assert f'id="order-{bad.id}"' not in html and f'id="order-{ok.id}"' not in html
    assert "Needs a look 0" in html


def test_attention_labels(admin_client, order_factory):
    old = datetime.utcnow() - timedelta(hours=30)
    order_factory(status="new", created_at=old)
    order_factory(status="delivered", status_changed_at=old)
    order_factory(status="delivered", delivery_type="grab", fee_charged=None, fee_driver=None)
    html = html_of(admin_client.get(BASE + "/?tab=attention"))
    assert "New for over 3 hours" in html
    assert "Delivered, cash not received after 24h" in html
    assert "Grab fee not entered" in html


def test_detail_page(admin_client, order_factory):
    o = order_factory(status="new", subtotal=42, total_cost=33, item_cost=None)
    o.maps_link = "https://maps.google.com/?q=1"
    o.notify_error = "forbidden"
    db.session.commit()
    html = html_of(admin_client.get(f"{BASE}/{o.id}"))
    assert "Test Item" in html and "$42.00" in html
    assert "https://maps.google.com/?q=1" in html and "012345678" in html
    assert "Item has no cost price" in html
    assert "Telegram message failed: forbidden" in html


def test_detail_does_not_link_unsafe_maps_url(admin_client, order_factory):
    o = order_factory()
    o.maps_link = "javascript:alert(1)"
    db.session.commit()
    html = html_of(admin_client.get(f"{BASE}/{o.id}"))
    assert 'href="javascript:' not in html


def test_card_marks_notify_error(admin_client, order_factory):
    o = order_factory(status="new")
    o.notify_error = "boom"
    db.session.commit()
    assert "notify-warn" in html_of(admin_client.get(BASE + "/?tab=new"))


def test_grab_fee_and_cash(admin_client, order_factory):
    grab = order_factory(status="delivered", delivery_type="grab", fee_charged=None, fee_driver=None)
    admin_client.post(f"{BASE}/{grab.id}/grab-fee", data={"fee": "3.5"}, headers=ORIGIN)
    grab = db.session.get(Order, grab.id)
    assert grab.delivery_fee_driver == 3.5 and grab.delivery_fee_charged == 3.5

    for bad in ("abc", "-1", "nan", ""):
        r = admin_client.post(f"{BASE}/{grab.id}/grab-fee", data={"fee": bad},
                              headers=ORIGIN, follow_redirects=True)
        assert r.status_code == 200
        assert db.session.get(Order, grab.id).delivery_fee_driver == 3.5

    admin_client.post(f"{BASE}/{grab.id}/cash", data={"received": "1"}, headers=ORIGIN)
    assert db.session.get(Order, grab.id).cash_received is True
    admin_client.post(f"{BASE}/{grab.id}/cash", data={"received": "0"}, headers=ORIGIN)
    assert db.session.get(Order, grab.id).cash_received is False

    new = order_factory(status="new")
    r = admin_client.post(f"{BASE}/{new.id}/cash", data={"received": "1"}, headers=ORIGIN,
                          follow_redirects=True)
    assert db.session.get(Order, new.id).cash_received is False
    assert "not allowed" in html_of(r)
    admin_client.post(f"{BASE}/{new.id}/grab-fee", data={"fee": "3"}, headers=ORIGIN)
    assert db.session.get(Order, new.id).delivery_fee_driver == 2.0  # standard order untouched


def test_note_saved_and_cleared(admin_client, order_factory):
    o = order_factory()
    admin_client.post(f"{BASE}/{o.id}/note", data={"admin_note": "  call first  "}, headers=ORIGIN)
    assert db.session.get(Order, o.id).admin_note == "call first"
    assert "call first" in html_of(admin_client.get(f"{BASE}/{o.id}"))
    admin_client.post(f"{BASE}/{o.id}/note", data={"admin_note": ""}, headers=ORIGIN)
    assert db.session.get(Order, o.id).admin_note is None


def test_settings_saves_admin_chat_and_fee(admin_client):
    r = admin_client.post("/admin/mobile/settings", data={
        "telegram_admin_chat_id": " 12345 ", "standard_delivery_fee": "2.5",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert Setting.get_val("telegram_admin_chat_id") == "12345"
    assert float(Setting.get_val("standard_delivery_fee")) == 2.5
    html = html_of(admin_client.get("/admin/mobile/settings"))
    assert 'value="12345"' in html and 'value="2.50"' in html


@pytest.mark.parametrize("bad", ["-1", "abc", "", "nan", "inf"])
def test_settings_rejects_bad_fee(admin_client, bad):
    Setting.set_val("standard_delivery_fee", "2.00")
    r = admin_client.post("/admin/mobile/settings", data={
        "telegram_admin_chat_id": "999", "standard_delivery_fee": bad,
    }, follow_redirects=True)
    assert Setting.get_val("standard_delivery_fee") == "2.00"
    assert Setting.get_val("telegram_admin_chat_id") is None
    assert "fee" in html_of(r).lower()


def test_test_message_sends_to_admin_chat(admin_client, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_bot, "send_message", lambda chat, text, markup=None: sent.append((chat, text)))
    Setting.set_val("telegram_admin_chat_id", "42")
    r = admin_client.post(f"{BASE}/test-message", headers=ORIGIN)
    assert r.headers["Location"].endswith("/admin/mobile/settings")
    assert sent == [("42", "✅ Angkorkey test message")]


def test_test_message_flashes_error(admin_client, monkeypatch):
    monkeypatch.setattr(telegram_bot, "send_message", lambda chat, text, markup=None: "forbidden")
    Setting.set_val("telegram_admin_chat_id", "42")
    r = admin_client.post(f"{BASE}/test-message", headers=ORIGIN, follow_redirects=True)
    assert "forbidden" in html_of(r)


def test_test_message_without_chat_id(admin_client, monkeypatch):
    monkeypatch.setattr(telegram_bot, "send_message", lambda *a, **k: pytest.fail("must not send"))
    r = admin_client.post(f"{BASE}/test-message", headers=ORIGIN, follow_redirects=True)
    assert "chat" in html_of(r).lower()


def test_new_order_badge_in_nav(admin_client, order_factory):
    order_factory(status="new")
    order_factory(status="new")
    order_factory(status="confirmed")
    html = html_of(admin_client.get("/admin/mobile/dashboard"))
    assert '<span class="nav-badge">2</span>' in html


def test_no_badge_without_new_orders(admin_client):
    assert '<span class="nav-badge">' not in html_of(admin_client.get("/admin/mobile/dashboard"))


def test_new_order_count_is_zero_outside_admin(app, order_factory):
    from blueprint.admin.orders import inject_new_order_count

    order_factory(status="new")
    with app.test_request_context("/"):
        assert inject_new_order_count() == {"new_order_count": 0}
    with app.test_request_context("/admin/mobile/orders/"):
        assert inject_new_order_count() == {"new_order_count": 1}

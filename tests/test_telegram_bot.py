import io
import json
import urllib.error
import urllib.request

import pytest

from extensions import db
from models import Setting
from services.telegram_bot import notify_admin_new_order, notify_customer_status, send_message
from translations import TRANSLATIONS


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def requests(monkeypatch, bot_token):
    """Replace urlopen; set `requests.reply` to the response dict or exception to produce."""
    class Recorder(list):
        reply = {"ok": True}

    recorder = Recorder()

    def fake_urlopen(req, timeout=None):
        recorder.append({"url": req.full_url, "body": json.loads(req.data), "timeout": timeout})
        if isinstance(recorder.reply, Exception):
            raise recorder.reply
        return FakeResponse(recorder.reply)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return recorder


def http_error(code, description):
    body = io.BytesIO(json.dumps({"ok": False, "description": description}).encode())
    return urllib.error.HTTPError("https://api.telegram.org/x", code, "err", {}, body)


def test_send_message_ok(requests):
    assert send_message(42, "hi <b>x</b>", {"inline_keyboard": []}) is None
    sent = requests[0]
    assert sent["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert sent["timeout"] == 5
    assert sent["body"] == {"chat_id": 42, "text": "hi <b>x</b>", "parse_mode": "HTML",
                            "reply_markup": {"inline_keyboard": []}}


def test_send_message_without_markup_omits_it(requests):
    send_message("42", "hi")
    assert "reply_markup" not in requests[0]["body"]


def test_send_message_no_token(app, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("no network without a token"))
    assert send_message(42, "hi") == "no_bot_token"


def test_send_message_not_ok_returns_description(requests):
    requests.reply = {"ok": False, "description": "chat not found"}
    assert send_message(42, "hi") == "chat not found"


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), urllib.error.URLError("dns"), ValueError("boom")])
def test_send_message_catches_every_exception(requests, exc):
    requests.reply = exc
    error = send_message(42, "hi")
    assert isinstance(error, str) and error


def test_send_message_error_never_leaks_token(requests):
    requests.reply = urllib.error.URLError("failed for https://api.telegram.org/bot123:ABC/sendMessage")
    assert "123:ABC" not in send_message(42, "hi")


def test_send_message_403_is_forbidden(requests):
    requests.reply = http_error(403, "Forbidden: bot was blocked by the user")
    assert send_message(42, "hi") == "forbidden"


def test_send_message_other_http_error(requests):
    requests.reply = http_error(400, "Bad Request: chat not found")
    assert send_message(42, "hi") == "Bad Request: chat not found"


def test_admin_message_escapes_customer_values(requests, order_factory):
    Setting.set_val("telegram_admin_chat_id", "777")
    order = order_factory()
    order.address = "<b>Street</b> & 5"
    order.phone = "<i>012</i>"
    order.maps_link = "https://maps.example/?a=1&b=<2>"
    order.customer.first_name = "<script>"
    order.items[0].name = "Cable <x>"
    order.items[0].options_text = "Red & <Blue>"
    order.items[0].quantity = 3
    db.session.commit()
    assert notify_admin_new_order(order) is None
    body = requests[0]["body"]
    text = body["text"]
    assert body["chat_id"] == "777"
    assert "&lt;b&gt;Street&lt;/b&gt; &amp; 5" in text and "<b>Street</b>" not in text
    assert "&lt;i&gt;012&lt;/i&gt;" in text
    assert "&lt;script&gt;" in text and "<script>" not in text
    assert "Cable &lt;x&gt; (Red &amp; &lt;Blue&gt;) ×3" in text
    assert "a=1&amp;b=&lt;2&gt;" in text
    assert f"#{order.id}" in text and "$12.00" in text
    assert f"https://angkorkey.store/admin/mobile/orders/{order.id}" in text


def test_admin_message_without_chat_id(requests, order_factory):
    assert notify_admin_new_order(order_factory()) == "no_admin_chat"
    assert requests == []


def test_customer_skipped_when_cannot_message(requests, order_factory, make_customer):
    order = order_factory(customer=make_customer(telegram_id=5, can_message=False))
    assert notify_customer_status(order) is None
    assert requests == []


def test_customer_message_content_and_keyboard(requests, order_factory, make_customer):
    Setting.set_val("telegram_username", "@MyShop")
    order = order_factory(customer=make_customer(telegram_id=5), status="packed")
    assert notify_customer_status(order) is None
    body = requests[0]["body"]
    assert body["chat_id"] == 5 and f"#{order.id}" in body["text"]
    buttons = [b for row in body["reply_markup"]["inline_keyboard"] for b in row]
    # "My orders" is a Mini App button (signed in there); "Chat with us" stays a plain link.
    assert buttons == [
        {"text": "My orders", "web_app": {"url": "https://angkorkey.store/my-orders"}},
        {"text": "Chat with us", "url": "https://t.me/MyShop"},
    ]


def test_admin_message_has_no_web_app_button(requests, order_factory):
    Setting.set_val("telegram_admin_chat_id", "777")
    order = order_factory()
    notify_admin_new_order(order)
    body = requests[0]["body"]
    assert "reply_markup" not in body
    assert f"https://angkorkey.store/admin/mobile/orders/{order.id}" in body["text"]


def test_customer_message_uses_order_received_copy_for_new(requests, order_factory, make_customer):
    order = order_factory(customer=make_customer(telegram_id=5), status="new")
    notify_customer_status(order)
    assert requests[0]["body"]["text"] == TRANSLATIONS["en"]["bot_order_received"].format(id=order.id)


def test_customer_message_in_khmer_and_fallback(requests, order_factory, make_customer):
    km = make_customer(telegram_id=5)
    km.lang = "km"
    order = order_factory(customer=km, status="delivered")
    notify_customer_status(order)
    assert requests[0]["body"]["text"] == TRANSLATIONS["km"]["bot_status_delivered"].format(id=order.id)
    assert requests[0]["body"]["reply_markup"]["inline_keyboard"][0][0]["text"] == TRANSLATIONS["km"]["bot_btn_my_orders"]

    other = make_customer(telegram_id=6)
    other.lang = "fr"
    order = order_factory(customer=other, status="delivered")
    notify_customer_status(order)
    assert requests[1]["body"]["text"] == TRANSLATIONS["en"]["bot_status_delivered"].format(id=order.id)


@pytest.mark.parametrize("status", ["new", "confirmed", "packed", "delivering", "delivered", "cancelled"])
def test_every_status_has_copy_in_both_languages(status):
    key = "bot_order_received" if status == "new" else f"bot_status_{status}"
    for lang in ("en", "km"):
        assert "{id}" in TRANSLATIONS[lang][key]
    for key in ("bot_btn_my_orders", "bot_btn_chat"):
        assert TRANSLATIONS["en"][key] and TRANSLATIONS["km"][key]


def test_customer_403_marks_cannot_message(requests, order_factory, make_customer):
    requests.reply = http_error(403, "Forbidden: bot was blocked by the user")
    customer = make_customer(telegram_id=5)
    order = order_factory(customer=customer, status="confirmed")
    assert notify_customer_status(order) == "forbidden"
    db.session.expire_all()
    assert customer.can_message is False


def test_customer_other_failure_keeps_can_message(requests, order_factory, make_customer):
    requests.reply = TimeoutError("timed out")
    customer = make_customer(telegram_id=5)
    order = order_factory(customer=customer, status="confirmed")
    assert notify_customer_status(order)
    db.session.expire_all()
    assert customer.can_message is True

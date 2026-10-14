import json
import time
from unittest import mock

from extensions import db
from models import Customer
from services import customer_session
from tests.helpers import sign_webapp, sign_widget

ORIGIN = {"Origin": "http://localhost"}


def signed_init_data(token, user_id=42, first_name="Sokha", allows_write=True, user=None):
    if user is None:
        user = {"id": user_id, "first_name": first_name}
        if allows_write is not None:
            user["allows_write_to_pm"] = allows_write
    fields = {
        "query_id": "AAH",
        "user": json.dumps(user),
        "auth_date": str(int(time.time()) - 30),
    }
    return sign_webapp(fields, token)


def test_webapp_login_creates_customer_and_sets_cookie(client, bot_token):
    r = client.post("/auth/telegram/webapp", json={"init_data": signed_init_data(bot_token)},
                    headers=ORIGIN)
    assert r.status_code == 200 and r.json["ok"] is True
    assert "ak_customer=" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
    assert "SameSite=Lax" in r.headers["Set-Cookie"]
    customer = Customer.query.one()
    assert customer.telegram_id == 42 and customer.can_message is True
    assert customer.last_login_at is not None
    assert r.json["customer"] == {"id": customer.id, "display_name": "Sokha", "can_message": True}
    assert client.get("/api/me").json["signed_in"] is True


def test_webapp_login_rejects_bad_hash(client, bot_token):
    r = client.post("/auth/telegram/webapp", json={"init_data": "user=%7B%7D&auth_date=1&hash=00"},
                    headers=ORIGIN)
    assert r.status_code == 401 and Customer.query.count() == 0
    assert r.json == {"ok": False}
    assert "ak_customer" not in r.headers.get("Set-Cookie", "")


def test_webapp_login_without_user_id_is_401(client, bot_token):
    for user in ({"first_name": "NoId"}, {"id": True}, {"id": "42"}):
        init = signed_init_data(bot_token, user=user)
        r = client.post("/auth/telegram/webapp", json={"init_data": init}, headers=ORIGIN)
        assert r.status_code == 401 and r.json == {"ok": False}
    assert Customer.query.count() == 0


def test_webapp_login_bad_bodies_are_401(client, bot_token):
    for body in ({}, {"init_data": 5}, [1, 2]):
        r = client.post("/auth/telegram/webapp", json=body, headers=ORIGIN)
        assert r.status_code == 401
    r = client.post("/auth/telegram/webapp", data="nope", headers=ORIGIN)
    assert r.status_code == 401


def test_login_without_bot_token_is_401(client, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    # Signed with the empty key: must not be accepted when no token is configured.
    r = client.post("/auth/telegram/webapp", json={"init_data": signed_init_data("")},
                    headers=ORIGIN)
    assert r.status_code == 401
    assert Customer.query.count() == 0


def test_bot_token_falls_back_to_setting(app, monkeypatch):
    from models import Setting
    from services.telegram_bot import get_bot_token

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert get_bot_token() == ""
    Setting.set_val("telegram_bot_token", "777:XYZ")
    assert get_bot_token() == "777:XYZ"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    assert get_bot_token() == "123:ABC"


def test_login_twice_updates_same_customer(client, bot_token):
    for name in ("Sokha", "Sokha Renamed"):
        r = client.post("/auth/telegram/webapp",
                        json={"init_data": signed_init_data(bot_token, first_name=name)},
                        headers=ORIGIN)
        assert r.status_code == 200
    assert Customer.query.count() == 1
    assert Customer.query.one().first_name == "Sokha Renamed"


def test_login_keeps_can_message_when_not_reported(client, bot_token, make_customer):
    make_customer(telegram_id=42, can_message=True)
    init = signed_init_data(bot_token, allows_write=None)
    r = client.post("/auth/telegram/webapp", json={"init_data": init}, headers=ORIGIN)
    assert r.status_code == 200
    assert Customer.query.one().can_message is True


def test_login_records_session_language(client, bot_token):
    client.get("/set_lang/km")
    client.post("/auth/telegram/webapp", json={"init_data": signed_init_data(bot_token)},
                headers=ORIGIN)
    assert Customer.query.one().lang == "km"


def test_widget_login_sets_can_message(client, bot_token):
    fields = {"id": "77", "first_name": "Dara", "username": "dara",
              "auth_date": str(int(time.time()))}
    body = sign_widget(fields, bot_token)
    body["id"] = 77  # the widget callback delivers id as a number
    r = client.post("/auth/telegram/widget", json=body, headers=ORIGIN)
    assert r.status_code == 200 and r.json["ok"] is True
    customer = Customer.query.one()
    assert customer.telegram_id == 77 and customer.can_message is True
    assert customer.username == "dara"
    assert client.get("/api/me").json["signed_in"] is True


def test_widget_login_rejects_tampering(client, bot_token):
    fields = {"id": "77", "first_name": "Dara", "auth_date": str(int(time.time()))}
    body = sign_widget(fields, bot_token)
    body["first_name"] = "Mallory"
    assert client.post("/auth/telegram/widget", json=body, headers=ORIGIN).status_code == 401
    assert Customer.query.count() == 0


def test_cross_origin_post_forbidden(client, bot_token):
    r = client.post("/auth/telegram/webapp", json={"init_data": signed_init_data(bot_token)},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert Customer.query.count() == 0


def test_post_without_origin_or_referer_forbidden(client, bot_token):
    r = client.post("/auth/telegram/webapp", json={"init_data": signed_init_data(bot_token)})
    assert r.status_code == 403


def test_referer_is_used_when_origin_missing(client, bot_token):
    r = client.post("/auth/telegram/webapp", json={"init_data": signed_init_data(bot_token)},
                    headers={"Referer": "http://localhost/cart"})
    assert r.status_code == 200
    r = client.post("/auth/telegram/webapp", json={"init_data": signed_init_data(bot_token)},
                    headers={"Referer": "https://evil.example/cart"})
    assert r.status_code == 403


def test_test_skip_origin_header_only_when_testing(app, client, bot_token):
    body = {"init_data": signed_init_data(bot_token)}
    r = client.post("/auth/telegram/webapp", json=body, headers={"X-Test-Skip-Origin": "1"})
    assert r.status_code == 200
    app.config["TESTING"] = False
    try:
        r = client.post("/auth/telegram/webapp", json=body, headers={"X-Test-Skip-Origin": "1"})
        assert r.status_code == 403
    finally:
        app.config["TESTING"] = True


def test_customer_cookie_does_not_grant_admin(client, login_as, make_customer):
    login_as(make_customer())
    r = client.get("/admin/mobile/dashboard")
    assert r.status_code == 302 and "/0964246058" in r.headers["Location"]


def test_tampered_cookie_is_anonymous(client):
    client.set_cookie("ak_customer", "garbage")
    assert client.get("/api/me").json == {"signed_in": False}


def test_deleted_customer_is_anonymous(client, login_as, make_customer):
    customer = make_customer()
    login_as(customer)
    db.session.delete(customer)
    db.session.commit()
    assert client.get("/api/me").json == {"signed_in": False}


def test_expired_cookie_is_anonymous(client, make_customer, monkeypatch):
    customer = make_customer()
    token = customer_session._serializer().dumps(customer.id)
    monkeypatch.setattr(customer_session, "MAX_AGE", -1)
    client.set_cookie("ak_customer", token)
    assert client.get("/api/me").json == {"signed_in": False}


def test_cookie_signed_with_other_salt_is_rejected(app, client, make_customer):
    from itsdangerous import URLSafeTimedSerializer

    customer = make_customer()
    token = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="other").dumps(customer.id)
    client.set_cookie("ak_customer", token)
    assert client.get("/api/me").json == {"signed_in": False}


def test_me_returns_profile_fields(client, login_as, make_customer):
    customer = make_customer(first_name="Sokha", can_message=False)
    customer.phone, customer.address, customer.maps_link = "012", "Street 1", "https://maps/x"
    db.session.commit()
    login_as(customer)
    assert client.get("/api/me").json == {
        "signed_in": True, "first_name": "Sokha", "can_message": False,
        "phone": "012", "address": "Street 1", "maps_link": "https://maps/x",
    }


def test_logout_clears(client, login_as, make_customer):
    login_as(make_customer())
    assert client.get("/api/me").json["signed_in"] is True
    r = client.post("/auth/logout", headers=ORIGIN)
    assert r.status_code == 200
    assert "ak_customer=;" in r.headers["Set-Cookie"]
    assert client.get("/api/me").json == {"signed_in": False}


def test_logout_cross_origin_forbidden(client, login_as, make_customer):
    login_as(make_customer())
    r = client.post("/auth/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.get("/api/me").json["signed_in"] is True


def test_write_access_updates_flag(client, login_as, make_customer):
    login_as(make_customer(can_message=False))
    r = client.post("/api/me/write-access", json={"allowed": True}, headers=ORIGIN)
    assert r.status_code == 200
    assert Customer.query.one().can_message is True
    client.post("/api/me/write-access", json={"allowed": False}, headers=ORIGIN)
    assert Customer.query.one().can_message is False


def test_write_access_requires_sign_in_origin_and_bool(client, login_as, make_customer):
    r = client.post("/api/me/write-access", json={"allowed": True}, headers=ORIGIN)
    assert r.status_code == 401
    login_as(make_customer(can_message=False))
    r = client.post("/api/me/write-access", json={"allowed": True},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/me/write-access", json={"allowed": "yes"}, headers=ORIGIN)
    assert r.status_code == 400
    assert Customer.query.one().can_message is False


def _aged_token(customer_id, age_seconds):
    """A validly signed token that was issued age_seconds ago."""
    issued = time.time() - age_seconds
    with mock.patch("itsdangerous.timed.time.time", return_value=issued):
        return customer_session._serializer().dumps(customer_id)


def test_old_cookie_is_refreshed(client, make_customer):
    customer = make_customer()
    client.set_cookie("ak_customer", _aged_token(customer.id, 2 * 24 * 3600))
    r = client.get("/api/me")
    assert r.json["signed_in"] is True
    assert "ak_customer=" in r.headers.get("Set-Cookie", "")


def test_fresh_cookie_is_not_refreshed(client, make_customer):
    customer = make_customer()
    client.set_cookie("ak_customer", _aged_token(customer.id, 3600))
    r = client.get("/api/me")
    assert r.json["signed_in"] is True
    assert "Set-Cookie" not in r.headers


def test_old_cookie_of_deleted_customer_is_not_refreshed(client):
    client.set_cookie("ak_customer", _aged_token(999, 2 * 24 * 3600))
    r = client.get("/api/me")
    assert r.json == {"signed_in": False}
    assert "Set-Cookie" not in r.headers


def test_placeholder_secret_key_disables_customer_auth(app, client, bot_token, make_customer):
    from services.customer_session import PLACEHOLDER_SECRET_KEY

    customer = make_customer()
    valid_cookie = _aged_token(customer.id, 60)
    assert app.config["SECRET_KEY"] == PLACEHOLDER_SECRET_KEY  # test env uses the fallback
    app.config["TESTING"] = False
    try:
        client.set_cookie("ak_customer", valid_cookie)
        assert client.get("/api/me").json == {"signed_in": False}
        body = {"init_data": signed_init_data(bot_token)}
        r = client.post("/auth/telegram/webapp", json=body, headers=ORIGIN)
        assert r.status_code == 503 and r.json == {"ok": False, "error": "auth_unavailable"}
        fields = {"id": "77", "first_name": "Dara", "auth_date": str(int(time.time()))}
        r = client.post("/auth/telegram/widget", json=sign_widget(fields, bot_token),
                        headers=ORIGIN)
        assert r.status_code == 503
    finally:
        app.config["TESTING"] = True
    assert Customer.query.count() == 1  # only the fixture customer
    assert client.get("/api/me").json["signed_in"] is True  # same cookie valid again


def test_real_secret_key_keeps_auth_enabled_outside_testing(app, client, make_customer):
    customer = make_customer()
    app.config["SECRET_KEY"] = "a-real-secret"
    app.config["TESTING"] = False
    try:
        client.set_cookie("ak_customer", _aged_token(customer.id, 60))
        assert client.get("/api/me").json["signed_in"] is True
    finally:
        app.config["SECRET_KEY"] = "change-this-secret-key-now"
        app.config["TESTING"] = True


def test_public_host_origin_accepted_behind_proxy(client, bot_token):
    body = {"init_data": signed_init_data(bot_token)}
    proxied = {"base_url": "http://127.0.0.1:5000"}
    r = client.post("/auth/telegram/webapp", json=body,
                    headers={"Origin": "https://angkorkey.store"}, **proxied)
    assert r.status_code == 200
    r = client.post("/auth/telegram/webapp", json=body,
                    headers={"Referer": "https://www.angkorkey.store/cart"}, **proxied)
    assert r.status_code == 200
    for evil in ("https://evil.example", "https://angkorkey.store.evil.example"):
        r = client.post("/auth/telegram/webapp", json=body, headers={"Origin": evil}, **proxied)
        assert r.status_code == 403

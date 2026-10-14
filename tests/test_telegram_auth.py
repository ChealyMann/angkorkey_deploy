import json
from urllib.parse import urlencode

from services.telegram_auth import verify_login_widget, verify_webapp_init_data
from tests.helpers import sign_webapp, sign_widget

NOW = 1_800_000_000
FIELDS = {
    "query_id": "AAH",
    "user": json.dumps({"id": 42, "first_name": "Sokha", "allows_write_to_pm": True}),
    "auth_date": str(NOW - 60),
}


def test_verify_webapp_accepts_valid():
    user = verify_webapp_init_data(sign_webapp(FIELDS, "123:ABC"), "123:ABC", now=NOW)
    assert user["id"] == 42 and user["allows_write_to_pm"] is True


def test_verify_rejects_tampered_hash():
    s = sign_webapp(FIELDS, "123:ABC").replace("Sokha", "Dara")
    assert verify_webapp_init_data(s, "123:ABC", now=NOW) is None


def test_verify_rejects_other_bot_token():
    assert verify_webapp_init_data(sign_webapp(FIELDS, "999:XYZ"), "123:ABC", now=NOW) is None


def test_verify_rejects_stale_auth_date():
    stale = dict(FIELDS, auth_date=str(NOW - 86401))
    assert verify_webapp_init_data(sign_webapp(stale, "123:ABC"), "123:ABC", now=NOW) is None


def test_verify_rejects_empty_token_and_garbage():
    assert verify_webapp_init_data(sign_webapp(FIELDS, ""), "", now=NOW) is None
    assert verify_webapp_init_data("not a query", "123:ABC", now=NOW) is None


def test_verify_rejects_future_auth_date():
    future = dict(FIELDS, auth_date=str(NOW + 120))
    assert verify_webapp_init_data(sign_webapp(future, "123:ABC"), "123:ABC", now=NOW) is None


def test_verify_rejects_missing_or_bad_fields():
    no_date = {k: v for k, v in FIELDS.items() if k != "auth_date"}
    assert verify_webapp_init_data(sign_webapp(no_date, "123:ABC"), "123:ABC", now=NOW) is None
    bad_date = dict(FIELDS, auth_date="yesterday")
    assert verify_webapp_init_data(sign_webapp(bad_date, "123:ABC"), "123:ABC", now=NOW) is None
    bad_user = dict(FIELDS, user="{not json")
    assert verify_webapp_init_data(sign_webapp(bad_user, "123:ABC"), "123:ABC", now=NOW) is None
    no_hash = urlencode(FIELDS)
    assert verify_webapp_init_data(no_hash, "123:ABC", now=NOW) is None


def test_verify_widget_accepts_valid_and_rejects_tampered():
    data = sign_widget({"id": "42", "first_name": "Sokha", "auth_date": str(NOW - 5)}, "123:ABC")
    assert verify_login_widget(data, "123:ABC", now=NOW)["id"] == 42
    assert verify_login_widget(dict(data, first_name="X"), "123:ABC", now=NOW) is None


def test_verify_widget_returns_profile_fields_and_accepts_int_values():
    fields = {"id": 42, "first_name": "Sokha", "username": "sokha",
              "photo_url": "https://t.me/i/u.jpg", "auth_date": NOW - 5}
    result = verify_login_widget(sign_widget(fields, "123:ABC"), "123:ABC", now=NOW)
    assert result == {"id": 42, "first_name": "Sokha", "last_name": None,
                      "username": "sokha", "photo_url": "https://t.me/i/u.jpg"}


def test_verify_widget_rejects_stale_future_wrong_token_and_empty_token():
    stale = sign_widget({"id": "42", "auth_date": str(NOW - 86401)}, "123:ABC")
    assert verify_login_widget(stale, "123:ABC", now=NOW) is None
    future = sign_widget({"id": "42", "auth_date": str(NOW + 120)}, "123:ABC")
    assert verify_login_widget(future, "123:ABC", now=NOW) is None
    other = sign_widget({"id": "42", "auth_date": str(NOW - 5)}, "999:XYZ")
    assert verify_login_widget(other, "123:ABC", now=NOW) is None
    ok = sign_widget({"id": "42", "auth_date": str(NOW - 5)}, "")
    assert verify_login_widget(ok, "", now=NOW) is None
    assert verify_login_widget({"id": "42"}, "123:ABC", now=NOW) is None

"""Shared test helpers."""
import hashlib
import hmac
from urllib.parse import urlencode


def sign_webapp(fields: dict, token: str) -> str:
    """Build a Mini App initData query string signed the way Telegram does."""
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(dict(fields, hash=digest))


def post_order(client, items=None, delivery_type="standard", phone="012345678",
               address="Phnom Penh", maps_link="", voucher_code=None):
    """POST /api/orders as same-origin JSON. `items` is sent as given."""
    payload = {
        "items": items,
        "delivery_type": delivery_type,
        "phone": phone,
        "address": address,
        "maps_link": maps_link,
        "voucher_code": voucher_code,
    }
    return client.post("/api/orders", json=payload, headers={"Origin": "http://localhost"})


def sign_widget(fields: dict, token: str) -> dict:
    """Return Login Widget data with a valid hash added."""
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()) if v is not None)
    secret = hashlib.sha256(token.encode()).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return dict(fields, hash=digest)

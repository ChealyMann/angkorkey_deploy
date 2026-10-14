"""Verification of Telegram Mini App initData and Login Widget signatures."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_CLOCK_SKEW = 60  # seconds an auth_date may be ahead of our clock


def _auth_date_ok(raw, max_age, now):
    """True when raw (str/int) is an integer timestamp within the allowed window."""
    try:
        auth_date = int(raw)
    except (TypeError, ValueError):
        return False
    now = time.time() if now is None else now
    return -MAX_CLOCK_SKEW <= now - auth_date <= max_age


def _hash_matches(check_string, secret, received):
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(received))


def verify_webapp_init_data(init_data, bot_token, max_age=86400, now=None):
    """Return the parsed Mini App `user` dict if initData is authentic and fresh, else None."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True)
        fields = dict(pairs)
        received = fields.get("hash")
        if not received or not _auth_date_ok(fields.get("auth_date"), max_age, now):
            return None
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs) if k != "hash")
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        if not _hash_matches(check_string, secret, received):
            return None
        user = json.loads(fields["user"])
        return user if isinstance(user, dict) else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def verify_login_widget(data, bot_token, max_age=86400, now=None):
    """Return the Login Widget profile dict if the data is authentic and fresh, else None."""
    if not data or not bot_token:
        return None
    try:
        received = data.get("hash")
        if not received or not _auth_date_ok(data.get("auth_date"), max_age, now):
            return None
        check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(data.items()) if k != "hash" and v is not None
        )
        secret = hashlib.sha256(bot_token.encode()).digest()
        if not _hash_matches(check_string, secret, received):
            return None
        return {
            "id": int(data["id"]),
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "username": data.get("username"),
            "photo_url": data.get("photo_url"),
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        return None

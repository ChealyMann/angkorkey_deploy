"""Signed, HttpOnly customer cookie (separate from the admin session)."""
from datetime import datetime, timezone

from flask import current_app, g, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from extensions import db
from models import Customer

COOKIE_NAME = "ak_customer"
MAX_AGE = 30 * 24 * 3600
REFRESH_AFTER = 24 * 3600  # re-issue tokens older than one day
PLACEHOLDER_SECRET_KEY = "change-this-secret-key-now"  # app.py's fallback when SECRET_KEY is unset

_warned_placeholder_key = False


def customer_auth_enabled() -> bool:
    """False when cookies would be signed with the public placeholder key (forgeable)."""
    global _warned_placeholder_key
    if current_app.testing or current_app.config["SECRET_KEY"] != PLACEHOLDER_SECRET_KEY:
        return True
    if not _warned_placeholder_key:
        _warned_placeholder_key = True
        current_app.logger.warning(
            "Customer sign-in disabled: SECRET_KEY is the placeholder value. Set SECRET_KEY."
        )
    return False


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="customer-auth")


def issue_customer_cookie(response, customer_id: int) -> None:
    response.set_cookie(
        COOKIE_NAME,
        _serializer().dumps(customer_id),
        max_age=MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
    )
    g.customer_cookie_written = True


def clear_customer_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="Lax", secure=request.is_secure)
    g.customer_cookie_written = True


def _read_token():
    """Return (customer_id, issued_at) from a valid cookie, else None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        customer_id, issued_at = _serializer().loads(token, max_age=MAX_AGE, return_timestamp=True)
    except BadSignature:  # includes SignatureExpired
        return None
    if not isinstance(customer_id, int) or isinstance(customer_id, bool):
        return None
    return customer_id, issued_at


def reset_customer_state():
    """before_app_request hook: never reuse a previous request's cached customer.

    Normally flask.g is per request, but it is shared across requests when an
    outer app context is already active (as in the test client fixtures).
    """
    for key in ("customer", "customer_token_issued_at", "customer_cookie_written"):
        g.pop(key, None)


def current_customer():
    """The signed-in Customer for this request, or None. Cached on flask.g."""
    if "customer" in g:
        return g.customer
    customer = None
    token = _read_token() if customer_auth_enabled() else None
    if token:
        customer = db.session.get(Customer, token[0])
        g.customer_token_issued_at = token[1]
    g.customer = customer
    return customer


def refresh_customer_cookie(response):
    """after_app_request hook: keep active customers signed in past 30 days."""
    if g.get("customer_cookie_written") or COOKIE_NAME not in request.cookies:
        return response
    customer = current_customer()
    issued_at = g.get("customer_token_issued_at")
    if customer and issued_at:
        age = (datetime.now(timezone.utc) - issued_at).total_seconds()
        if age > REFRESH_AFTER:
            issue_customer_cookie(response, customer.id)
    return response

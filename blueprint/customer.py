from datetime import datetime

from flask import Blueprint, jsonify, make_response, request, session

from extensions import db
from models import Customer
from services.customer_session import (
    customer_auth_enabled,
    clear_customer_cookie,
    current_customer,
    issue_customer_cookie,
    refresh_customer_cookie,
    reset_customer_state,
)
from services.security import require_same_origin
from services.telegram_auth import verify_login_widget, verify_webapp_init_data
from services.telegram_bot import get_bot_token

customer_bp = Blueprint('customer', __name__)
customer_bp.before_app_request(reset_customer_state)
customer_bp.after_app_request(refresh_customer_cookie)

WIDGET_KEYS = ("id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash")


def upsert_customer(tg_user: dict, can_message):
    """Create or refresh the Customer for a verified Telegram user."""
    customer = Customer.query.filter_by(telegram_id=tg_user["id"]).first()
    if customer is None:
        customer = Customer(telegram_id=tg_user["id"], can_message=False)
        db.session.add(customer)
    customer.first_name = tg_user.get("first_name")
    customer.last_name = tg_user.get("last_name")
    customer.username = tg_user.get("username")
    customer.photo_url = tg_user.get("photo_url")
    customer.lang = session.get("lang", "en")
    customer.last_login_at = datetime.utcnow()
    if can_message is not None:
        customer.can_message = bool(can_message)
    db.session.commit()
    return customer


def _json_body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _valid_id(tg_user) -> bool:
    return (isinstance(tg_user, dict) and isinstance(tg_user.get("id"), int)
            and not isinstance(tg_user["id"], bool))


def _auth_unavailable():
    return jsonify({"ok": False, "error": "auth_unavailable"}), 503


def _sign_in_response(tg_user, can_message):
    if not _valid_id(tg_user):
        return jsonify({"ok": False}), 401
    customer = upsert_customer(tg_user, can_message)
    response = make_response(jsonify({
        "ok": True,
        "customer": {
            "id": customer.id,
            "display_name": customer.display_name,
            "can_message": customer.can_message,
        },
    }))
    issue_customer_cookie(response, customer.id)
    return response


@customer_bp.route('/auth/telegram/webapp', methods=['POST'])
def telegram_webapp_login():
    require_same_origin()
    if not customer_auth_enabled():
        return _auth_unavailable()
    init_data = _json_body().get("init_data")
    if not isinstance(init_data, str):
        return jsonify({"ok": False}), 401
    tg_user = verify_webapp_init_data(init_data, get_bot_token())
    if not _valid_id(tg_user):
        return jsonify({"ok": False}), 401
    allows_write = tg_user.get("allows_write_to_pm")
    return _sign_in_response(tg_user, None if allows_write is None else bool(allows_write))


@customer_bp.route('/auth/telegram/widget', methods=['POST'])
def telegram_widget_login():
    require_same_origin()
    if not customer_auth_enabled():
        return _auth_unavailable()
    body = _json_body()
    data = {k: body[k] for k in WIDGET_KEYS if k in body}
    tg_user = verify_login_widget(data, get_bot_token())
    # The widget is configured with data-request-access="write".
    return _sign_in_response(tg_user, True)


@customer_bp.route('/auth/logout', methods=['POST'])
def customer_logout():
    require_same_origin()
    response = make_response(jsonify({"ok": True}))
    clear_customer_cookie(response)
    return response


@customer_bp.route('/api/me')
def me():
    customer = current_customer()
    if customer is None:
        return jsonify({"signed_in": False})
    return jsonify({
        "signed_in": True,
        "first_name": customer.first_name,
        "can_message": customer.can_message,
        "phone": customer.phone,
        "address": customer.address,
        "maps_link": customer.maps_link,
    })


@customer_bp.route('/api/me/write-access', methods=['POST'])
def me_write_access():
    require_same_origin()
    customer = current_customer()
    if customer is None:
        return jsonify({"ok": False}), 401
    allowed = _json_body().get("allowed")
    if not isinstance(allowed, bool):
        return jsonify({"ok": False}), 400
    customer.can_message = allowed
    db.session.commit()
    return jsonify({"ok": True, "can_message": customer.can_message})

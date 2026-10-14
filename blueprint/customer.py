import math
import os
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, make_response, render_template, request, session
from sqlalchemy.orm import selectinload

from extensions import db, limiter
from models import Customer, Order, Setting
from services.customer_session import (
    customer_auth_enabled,
    clear_customer_cookie,
    current_customer,
    issue_customer_cookie,
    refresh_customer_cookie,
    reset_customer_state,
)
from services.orders import KH_TZ, OrderError, create_order
from services.security import require_same_origin
from services.telegram_auth import verify_login_widget, verify_webapp_init_data
from services.telegram_bot import get_bot_token

customer_bp = Blueprint('customer', __name__)
customer_bp.before_app_request(reset_customer_state)
customer_bp.after_app_request(refresh_customer_cookie)

WIDGET_KEYS = ("id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash")
DEFAULT_BOT_USERNAME = "angkorkeywebsite_bot"
MY_ORDERS_PAGE_SIZE = 20


@customer_bp.app_context_processor
def inject_bot_username():
    """The Login Widget's bot. The same for every visitor, so safe in cached pages.

    BOT_USERNAME in the environment wins, so a local dev bot can be used
    without touching the database (pair it with TELEGRAM_BOT_TOKEN).
    """
    username = os.environ.get("BOT_USERNAME") or Setting.get_val("bot_username", DEFAULT_BOT_USERNAME)
    username = str(username or "").strip().lstrip("@")
    # The bot's numeric id (the public part of the token before ":"), for Telegram's login popup.
    token_id = get_bot_token().split(":", 1)[0]
    return {
        "bot_username": username or DEFAULT_BOT_USERNAME,
        "bot_id": token_id if token_id.isdigit() else "",
    }


@customer_bp.app_template_filter("kh_date")
def kh_date(value) -> str:
    """Naive-UTC timestamp as a Cambodia-local date and time, e.g. 14/10/2026 01:30."""
    if not value:
        return ""
    return value.replace(tzinfo=timezone.utc).astimezone(KH_TZ).strftime("%d/%m/%Y %H:%M")


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
    if "lang" in session:  # the language the customer picked on the site
        customer.lang = session["lang"]
    else:  # otherwise their Telegram app language (Mini App only; the Login Widget does not send it)
        customer.lang = "km" if tg_user.get("language_code") == "km" else "en"
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


# The sign-in routes and /api/me are exempt from the default per-IP limit: they are cheap or
# HMAC-verified, and behind the proxy every customer can share one address.
@customer_bp.route('/auth/telegram/webapp', methods=['POST'])
@limiter.exempt
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
@limiter.exempt
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
@limiter.exempt
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


@customer_bp.route('/api/orders', methods=['POST'])
@limiter.limit("10 per minute", key_func=lambda: f"customer:{request.cookies.get('ak_customer','anon')}")
def create_order_api():
    require_same_origin()
    customer = current_customer()
    if customer is None:
        return jsonify({"ok": False, "error": "sign_in_required"}), 401
    try:
        order = create_order(customer, _json_body())
    except OrderError as exc:
        return jsonify({"ok": False, "error": exc.code}), exc.status
    return jsonify({"ok": True, "order_id": order.id, "url": f"/my-orders/{order.id}?new=1"}), 201


def _page_number() -> int:
    try:
        return max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        return 1


@customer_bp.route('/my-orders')
def my_orders():
    customer = current_customer()
    if customer is None:
        return render_template('frontend/pages/my_orders.html', signed_in=False, orders=[], page=1, pages=1)
    query = Order.query.filter_by(customer_id=customer.id)
    pages = max(math.ceil(query.count() / MY_ORDERS_PAGE_SIZE), 1)
    page = min(_page_number(), pages)
    orders = (query.options(selectinload(Order.items))
              .order_by(Order.created_at.desc(), Order.id.desc())
              .limit(MY_ORDERS_PAGE_SIZE).offset((page - 1) * MY_ORDERS_PAGE_SIZE).all())
    return render_template('frontend/pages/my_orders.html', signed_in=True, orders=orders, page=page, pages=pages,
                           customer=customer)


@customer_bp.route('/my-orders/<int:order_id>')
def my_order_detail(order_id):
    customer = current_customer()
    order = db.session.get(Order, order_id)
    if customer is None or order is None or order.customer_id != customer.id:
        abort(404)
    return render_template('frontend/pages/my_order_detail.html', order=order,
                           is_new=request.args.get("new") == "1")

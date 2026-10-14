"""Mobile admin: orders screen, order detail, order actions and the Telegram test message."""
import math
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import (
    Blueprint, abort, current_app, flash, has_request_context, redirect, render_template, request, url_for,
)
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from extensions import db
from models import Order, OrderItem, Setting
from services import orders as order_service
from services import telegram_bot
from services.orders import KH_TZ, NEXT_STATUS, OrderError
from services.security import require_same_origin

admin_orders_bp = Blueprint("admin_orders", __name__, url_prefix="/admin/mobile/orders")

PAGE_SIZE = 30
ATTENTION_SCAN = 500
NOTE_MAX = 2000
RANGES = {"today": "Today", "7d": "7 days", "month": "This month"}
DEFAULT_RANGE = "today"
DEFAULT_TAB = "new"
# Tab key -> order status. "attention" is computed, not a status.
TAB_STATUS = {
    "new": "new", "confirmed": "confirmed", "packed": "packed",
    "delivering": "delivering", "done": "delivered", "cancelled": "cancelled",
}
TAB_LABELS = {
    "attention": "⚠ Needs a look", "new": "New", "confirmed": "Confirmed", "packed": "Packed",
    "delivering": "Delivering", "done": "Done", "cancelled": "Cancelled",
}
# Finished stages are limited to the selected range; working stages always show everything.
RANGE_LIMITED = ("delivered", "cancelled")
NEXT_LABELS = {
    "confirmed": "Confirm", "packed": "Mark packed", "delivering": "Out for delivery", "delivered": "Mark delivered",
}
STATUS_LABELS = {
    "new": "New", "confirmed": "Confirmed", "packed": "Packed",
    "delivering": "Delivering", "delivered": "Delivered", "cancelled": "Cancelled",
}
ATTENTION_LABELS = {
    "missing_cost": "Item has no cost price",
    "cash_overdue": "Delivered, cash not received after 24h",
    "stuck_new": "New for over 3 hours",
    "stuck_delivering": "Still delivering after 9 pm",
    "grab_fee_missing": "Grab fee not entered",
}
ERROR_MESSAGES = {
    "invalid_transition": "That change is not allowed at this stage of the order.",
    "invalid_fee": "Enter a Grab fee of 0 or more (Grab orders only).",
    "invalid_cost": "Enter a cost price of 0 or more.",
}


# ============================================================
# Template helpers
# ============================================================
@admin_orders_bp.app_context_processor
def inject_new_order_count():
    """Badge for the admin nav; only computed on admin pages."""
    if not has_request_context() or not request.path.startswith("/admin/"):
        return {"new_order_count": 0}
    return {"new_order_count": Order.query.filter_by(status="new").count()}


def _to_kh(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc).astimezone(KH_TZ)  # stored naive UTC


@admin_orders_bp.app_template_filter("order_money")
def order_money(value) -> str:
    return f"${(value or 0):.2f}"


@admin_orders_bp.app_template_filter("kh_time")
def kh_time(value) -> str:
    return _to_kh(value).strftime("%H:%M") if value else ""


@admin_orders_bp.app_template_filter("kh_datetime")
def kh_datetime(value) -> str:
    return _to_kh(value).strftime("%d %b, %H:%M") if value else ""


@admin_orders_bp.app_template_filter("safe_http_url")
def safe_http_url(value):
    """The URL if it is plain http(s), else None. Maps links are customer-supplied."""
    parsed = urlparse(value or "")
    return value if parsed.scheme in ("http", "https") and parsed.netloc else None


def _telegram_link(customer) -> str:
    username = (customer.username or "").strip().lstrip("@")
    return f"https://t.me/{username}" if username else f"tg://user?id={customer.telegram_id}"


def _card(order: Order, reasons: list[str]) -> dict:
    return {
        "order": order,
        "item_count": sum(item.quantity or 0 for item in order.items),
        "profit": order_service.order_profit(order),
        "profit_incomplete": order_service.cost_incomplete(order),
        "reasons": [ATTENTION_LABELS[code] for code in reasons],
        "next_status": NEXT_STATUS.get(order.status),
        "next_label": NEXT_LABELS.get(NEXT_STATUS.get(order.status)),
        "telegram_link": _telegram_link(order.customer),
    }


# ============================================================
# Request helpers
# ============================================================
def _back(fallback_endpoint: str = "admin_orders.order_list", **values) -> str:
    """Where to go after an action: the page the form was on (same site only), else the fallback."""
    parsed = urlparse(request.referrer or "")
    allowed = parsed.netloc == request.host or (
        parsed.hostname and parsed.hostname in current_app.config.get("PUBLIC_HOSTS", ()))
    if allowed and parsed.path.startswith("/admin/"):
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return url_for(fallback_endpoint, **values)


def _positive_int(value, default=1) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


def _flash_error(exc: OrderError) -> None:
    flash(ERROR_MESSAGES.get(exc.code, f"Could not do that ({exc.code})."), "error")


def _order_or_404(order_id: int) -> Order:
    order = db.session.get(Order, order_id)
    if order is None:
        abort(404)
    return order


# ============================================================
# Orders list
# ============================================================
def _tab_query(status: str, start: datetime, end: datetime):
    query = Order.query.filter(Order.status == status)
    if status in RANGE_LIMITED:
        query = query.filter(Order.status_changed_at >= start, Order.status_changed_at < end)
    return query


def _attention_cards() -> list[dict]:
    recent = (Order.query.filter(Order.status != "cancelled")
              .options(selectinload(Order.items), selectinload(Order.customer))
              .order_by(Order.id.desc()).limit(ATTENTION_SCAN).all())
    now = datetime.utcnow()
    cards = []
    for order in recent:
        reasons = order_service.needs_attention(order, now)
        if reasons:
            cards.append(_card(order, reasons))
    return cards


@admin_orders_bp.route("/")
def order_list():
    range_key = request.args.get("range")
    if range_key not in RANGES:
        range_key = DEFAULT_RANGE
    tab = request.args.get("tab")
    if tab != "attention" and tab not in TAB_STATUS:
        tab = DEFAULT_TAB
    page = _positive_int(request.args.get("page"))

    stats = order_service.summary(range_key)
    start, end = order_service.range_bounds(range_key)

    counts = {}
    for key, status in TAB_STATUS.items():
        counts[key] = _tab_query(status, start, end).count()
    attention = _attention_cards()
    counts["attention"] = len(attention)

    if tab == "attention":
        total = len(attention)
        pages = max(math.ceil(total / PAGE_SIZE), 1)
        page = min(page, pages)
        cards = attention[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    else:
        query = _tab_query(TAB_STATUS[tab], start, end)
        total = counts[tab]
        pages = max(math.ceil(total / PAGE_SIZE), 1)
        page = min(page, pages)
        rows = (query.options(selectinload(Order.items), selectinload(Order.customer))
                .order_by(Order.created_at.desc(), Order.id.desc())
                .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE).all())
        now = datetime.utcnow()
        cards = [_card(order, order_service.needs_attention(order, now)) for order in rows]

    awaiting_cash_orders = Order.query.filter(
        Order.status == "delivered", Order.cash_received.isnot(True)).count()

    return render_template(
        "backend/admin/mobile/order/list.html",
        range_key=range_key, ranges=RANGES, tab=tab, tab_labels=TAB_LABELS, counts=counts,
        stats=stats, awaiting_cash_orders=awaiting_cash_orders, cards=cards,
        page=page, pages=pages, total=total,
        today_label=_to_kh(datetime.utcnow()).strftime("%a %d %b"),
    )


# ============================================================
# Order detail
# ============================================================
@admin_orders_bp.route("/<int:order_id>")
def order_detail(order_id):
    order = _order_or_404(order_id)
    card = _card(order, order_service.needs_attention(order))
    return render_template(
        "backend/admin/mobile/order/detail.html",
        card=card, order=order, status_labels=STATUS_LABELS,
        driver_payback=order_service.driver_payback(order),
        maps_url=safe_http_url(order.maps_link),
    )


# ============================================================
# Actions (all POST, same-origin only)
# ============================================================
@admin_orders_bp.route("/<int:order_id>/advance", methods=["POST"])
def advance(order_id):
    require_same_origin()
    order = _order_or_404(order_id)
    to_status = request.form.get("to_status", "")
    try:
        order_service.advance_status(order, to_status)
        flash(f"Order #{order_id} is now {STATUS_LABELS.get(to_status, to_status).lower()}.", "success")
    except OrderError as exc:
        _flash_error(exc)
    return redirect(_back())


@admin_orders_bp.route("/<int:order_id>/grab-fee", methods=["POST"])
def grab_fee(order_id):
    require_same_origin()
    order = _order_or_404(order_id)
    try:
        try:
            fee = float(request.form.get("fee", "").strip())
        except ValueError:
            raise OrderError("invalid_fee")
        order_service.set_grab_fee(order, fee)
        flash(f"Grab fee for order #{order_id} saved.", "success")
    except OrderError as exc:
        _flash_error(exc)
    return redirect(_back())


@admin_orders_bp.route("/<int:order_id>/cash", methods=["POST"])
def cash(order_id):
    require_same_origin()
    order = _order_or_404(order_id)
    received = request.form.get("received") == "1"
    try:
        order_service.set_cash_received(order, received)
        flash(f"Order #{order_id} cash marked {'received' if received else 'not received'}.", "success")
    except OrderError as exc:
        _flash_error(exc)
    return redirect(_back())


@admin_orders_bp.route("/<int:order_id>/item/<int:item_id>/cost", methods=["POST"])
def item_cost(order_id, item_id):
    require_same_origin()
    order = _order_or_404(order_id)
    item = db.session.get(OrderItem, item_id)
    if item is None or item.order_id != order.id:
        abort(404)
    try:
        try:
            cost = float(request.form.get("cost", "").strip())
        except ValueError:
            raise OrderError("invalid_cost")
        order_service.set_item_cost(order, item, cost)
        flash(f"Cost for {item.name} saved.", "success")
    except OrderError as exc:
        _flash_error(exc)
    return redirect(_back("admin_orders.order_detail", order_id=order_id))


@admin_orders_bp.route("/fill-costs", methods=["POST"])
def fill_costs():
    require_same_origin()
    filled = order_service.fill_missing_costs()
    flash(f"Filled {filled} item cost(s).", "success")
    return redirect(_back(tab="attention"))


@admin_orders_bp.route("/<int:order_id>/note", methods=["POST"])
def note(order_id):
    require_same_origin()
    order = _order_or_404(order_id)
    order.admin_note = request.form.get("admin_note", "").strip()[:NOTE_MAX] or None
    db.session.commit()
    flash("Note saved.", "success")
    return redirect(_back())


@admin_orders_bp.route("/test-message", methods=["POST"])
def test_message():
    require_same_origin()
    chat_id = str(Setting.get_val("telegram_admin_chat_id", "") or "").strip()
    if not chat_id:
        flash("Enter and save the admin chat ID first.", "danger")
    else:
        error = telegram_bot.send_message(chat_id, "✅ Angkorkey test message")
        if error:
            flash(f"Test message failed: {error}", "danger")
        else:
            flash("Test message sent.", "success")
    return redirect(url_for("mobile.settings"))

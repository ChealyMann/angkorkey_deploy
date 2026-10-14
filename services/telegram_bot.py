"""Telegram bot helpers: sending messages and order notifications."""
import html
import json
import logging
import os
import urllib.error
import urllib.request

from extensions import db
from models import Setting
from translations import TRANSLATIONS

SITE_URL = "https://angkorkey.store"
SEND_TIMEOUT = 5
ERROR_MAX = 200
DEFAULT_TELEGRAM_USERNAME = "Angkorkey_Store"

logger = logging.getLogger(__name__)


def get_bot_token() -> str:
    """Bot token: environment first, then the admin setting, else empty string."""
    return os.environ.get("TELEGRAM_BOT_TOKEN") or Setting.get_val("telegram_bot_token", "") or ""


def _short(text, secret: str = "") -> str:
    """One line of at most ERROR_MAX chars. `secret` is masked first, so truncation can't leave part of it."""
    text = " ".join(str(text).split())
    if secret:
        text = text.replace(secret, "***")
    return text[:ERROR_MAX] or "error"


def _http_error_text(exc: urllib.error.HTTPError) -> str:
    if exc.code == 403:
        return "forbidden"
    try:
        description = json.loads(exc.read().decode("utf-8")).get("description")
    except Exception:
        description = None
    return _short(description or f"http_{exc.code}")


def send_message(chat_id, text: str, reply_markup: dict | None = None) -> str | None:
    """Send an HTML message. Returns None on success, else a short error string.

    "forbidden" means Telegram answered 403 (the user blocked the bot). Never raises.
    """
    token = get_bot_token().strip()
    if not token:
        return "no_bot_token"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("ok"):
            return None
        if result.get("error_code") == 403:
            return "forbidden"
        return _short(result.get("description") or "telegram_error")
    except urllib.error.HTTPError as exc:
        return _http_error_text(exc)
    except Exception as exc:
        # URLError and friends can echo the request URL, which contains the token.
        return _short(str(exc) or exc.__class__.__name__, token)


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def notify_admin_new_order(order) -> str | None:
    """Tell the shop admin about a new order. Returns an error string, or None on success."""
    chat_id = str(Setting.get_val("telegram_admin_chat_id", "") or "").strip()
    if not chat_id:
        return "no_admin_chat"
    lines = [f"<b>New order #{order.id}</b>", ""]
    for item in order.items:
        options = f" ({_e(item.options_text)})" if item.options_text else ""
        lines.append(f"• {_e(item.name)}{options} ×{item.quantity}")
    delivery = (order.delivery_type or "").capitalize()
    if order.delivery_fee_charged is not None:
        delivery += f" (${order.delivery_fee_charged:.2f})"
    elif order.delivery_type == "grab":
        delivery += " (fee to be set)"
    lines += [
        "",
        f"Total to collect: ${order.total_to_collect or 0:.2f}",
        f"Delivery: {_e(delivery)}",
        f"Customer: {_e(order.customer.display_name)}",
    ]
    if order.phone:
        lines.append(f"Phone: {_e(order.phone)}")
    lines += [
        f"Address: {_e(order.address)}",
    ]
    if order.maps_link:
        lines.append(f"Map: {_e(order.maps_link)}")
    lines += ["", f"{SITE_URL}/admin/mobile/orders/{order.id}"]
    return send_message(chat_id, "\n".join(lines))


def _copy(lang: str, key: str) -> str:
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key) or TRANSLATIONS["en"][key]


def notify_customer_status(order) -> str | None:
    """Tell the customer their order's current status. Returns an error string, or None.

    Skipped (None) when the customer has not allowed messages. A 403 from Telegram
    means they blocked the bot, so `can_message` is switched off.
    """
    customer = order.customer
    if not customer.can_message:
        return None
    lang = customer.lang if customer.lang in TRANSLATIONS else "en"
    key = "bot_order_received" if order.status == "new" else f"bot_status_{order.status}"
    if key not in TRANSLATIONS["en"]:
        return None
    order_id = order.id
    username = str(Setting.get_val("telegram_username", DEFAULT_TELEGRAM_USERNAME)
                   or DEFAULT_TELEGRAM_USERNAME).strip().lstrip("@")
    # "My orders" opens as a Mini App (web_app) so it gets initData and the shop's cookie; a plain
    # url button would open Telegram's in-app browser, where the customer is signed out.
    keyboard = {"inline_keyboard": [[
        {"text": _copy(lang, "bot_btn_my_orders"), "web_app": {"url": f"{SITE_URL}/my-orders"}},
        {"text": _copy(lang, "bot_btn_chat"), "url": f"https://t.me/{username}"},
    ]]}
    error = send_message(customer.telegram_id, _copy(lang, key).format(id=order_id), keyboard)
    if error == "forbidden":
        try:
            customer.can_message = False
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Could not clear can_message after 403 for order %s", order_id)
    return error

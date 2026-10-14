"""Telegram bot helpers."""
import os

from models import Setting


def get_bot_token() -> str:
    """Bot token: environment first, then the admin setting, else empty string."""
    return os.environ.get("TELEGRAM_BOT_TOKEN") or Setting.get_val("telegram_bot_token", "") or ""


def notify_admin_new_order(order) -> str | None:
    """Tell the shop admin about a new order. Returns an error string, or None on success.

    Stub: Task 6 sends the real message.
    """
    return None


def notify_customer_status(order) -> str | None:
    """Tell the customer their order's current status. Returns an error string, or None.

    Stub: Task 6 sends the real message.
    """
    return None

"""Telegram bot helpers."""
import os

from models import Setting


def get_bot_token() -> str:
    """Bot token: environment first, then the admin setting, else empty string."""
    return os.environ.get("TELEGRAM_BOT_TOKEN") or Setting.get_val("telegram_bot_token", "") or ""

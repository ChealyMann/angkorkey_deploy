"""Voucher rules: one place for validation and redemption.

A voucher means free Standard delivery. It cannot be used with any other
delivery type, and each phone number may redeem a given voucher once.
"""
from extensions import db
from models import Voucher, VoucherRedemption


class VoucherError(Exception):
    """A voucher that passed check_voucher could not be redeemed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def normalize_phone(phone):
    if not phone:
        return ""
    # A Telegram account identity ("tg:<id>") is used as-is when there is no phone.
    if phone.startswith("tg:"):
        return phone
    # Remove all non-digits except +
    clean = "".join(c for c in phone if c.isdigit() or c == '+')
    # If it starts with +855, replace with 0
    if clean.startswith("+855"):
        clean = "0" + clean[4:]
    # If it starts with 855, replace with 0
    elif clean.startswith("855"):
        clean = "0" + clean[3:]
    return clean


def _already_redeemed(voucher: Voucher, normalized_phone: str) -> bool:
    return VoucherRedemption.query.filter_by(
        voucher_id=voucher.id, phone_number=normalized_phone
    ).first() is not None


def check_voucher(code: str, subtotal: float, phone: str, delivery_type: str = "standard"):
    """Return (voucher, None) if usable, else (None, error_code).

    An empty phone skips the per-phone check (used by the checkout preview).
    """
    code = (code or "").strip().upper()
    if not code:
        return None, "voucher_required"
    voucher = Voucher.query.filter_by(code=code).first()
    if voucher is None:
        return None, "voucher_invalid"
    if voucher.status != "true":
        return None, "voucher_inactive"
    if voucher.usage_limit is not None and voucher.usage_count >= voucher.usage_limit:
        return None, "voucher_limit_reached"
    if subtotal < voucher.min_spend:
        return None, "voucher_min_spend"
    normalized = normalize_phone(phone)
    if normalized and _already_redeemed(voucher, normalized):
        return None, "voucher_already_used"
    if delivery_type != "standard":
        return None, "voucher_standard_only"
    return voucher, None


def redeem_voucher(voucher: Voucher, phone: str) -> None:
    """Record a redemption and bump usage_count, without committing.

    The increment is a conditional UPDATE so two concurrent orders cannot both
    take the last use; it also takes the database write lock, so the per-phone
    re-check below sees any redemption committed since check_voucher ran.
    Raises VoucherError if the voucher was used up in the meantime.
    """
    usage_ok = db.or_(Voucher.usage_limit.is_(None), Voucher.usage_count < Voucher.usage_limit)
    result = db.session.execute(
        db.update(Voucher)
        .where(Voucher.id == voucher.id, usage_ok)
        .values(usage_count=Voucher.usage_count + 1)
    )
    if result.rowcount != 1:
        raise VoucherError("voucher_limit_reached")
    normalized = normalize_phone(phone)
    if _already_redeemed(voucher, normalized):
        raise VoucherError("voucher_already_used")
    db.session.add(VoucherRedemption(voucher_id=voucher.id, phone_number=normalized))

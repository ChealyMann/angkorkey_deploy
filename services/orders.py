"""Server-side order creation: prices and costs always come from the database."""
import re

from flask import current_app

from extensions import db
from models import Order, OrderItem, OrderStatusLog, Product, Setting
from models.ProductVariant import ProductVariant
from services import telegram_bot
from services.vouchers import VoucherError, check_voucher, redeem_voucher

MAX_QTY = 99
MAX_LINES = 50
DEFAULT_STANDARD_FEE = 2.00
DELIVERY_TYPES = ("standard", "grab")
NOTIFY_ERROR_MAX = 255

_BASE_ID = re.compile(r"base_(\d+)")


class OrderError(Exception):
    """A client error: the order was not created. `code` is sent to the client."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _money(value) -> float:
    return round(float(value), 2)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def standard_delivery_fee() -> float:
    """The `standard_delivery_fee` setting, falling back to 2.00 if unset or invalid."""
    try:
        fee = _money(Setting.get_val("standard_delivery_fee", DEFAULT_STANDARD_FEE))
    except (TypeError, ValueError):
        return DEFAULT_STANDARD_FEE
    return fee if fee >= 0 else DEFAULT_STANDARD_FEE


def _item_for_variant(variant_id: int, quantity: int) -> OrderItem:
    variant = db.session.get(ProductVariant, variant_id)
    if variant is None or variant.status != "true" or variant.product.status != "true":
        raise OrderError("invalid_items")
    options = " / ".join(sel.option.value for sel in variant.selected_options)
    return OrderItem(
        product_id=variant.product_id,
        variant_id=variant.id,
        name=variant.product.name,
        options_text=options,
        unit_price=_money(variant.price),
        unit_cost=None if variant.cost is None else _money(variant.cost),
        quantity=quantity,
    )


def _item_for_base_product(product_id: int, quantity: int) -> OrderItem:
    product = db.session.get(Product, product_id)
    # A product with active variants must be ordered through one of them.
    if product is None or product.status != "true" or product.has_variants:
        raise OrderError("invalid_items")
    return OrderItem(
        product_id=product.id,
        variant_id=None,
        name=product.name,
        options_text="",
        unit_price=_money(product.price),
        unit_cost=None if product.cost is None else _money(product.cost),
        quantity=quantity,
    )


def price_items(items) -> list:
    """Turn [{"variantId", "quantity"}] into unsaved OrderItems priced from the DB.

    Any other keys (such as a client-sent price) are ignored.
    """
    if not isinstance(items, list) or not items or len(items) > MAX_LINES:
        raise OrderError("invalid_items")
    priced = []
    for item in items:
        if not isinstance(item, dict):
            raise OrderError("invalid_items")
        quantity = item.get("quantity")
        if not _is_int(quantity) or not 1 <= quantity <= MAX_QTY:
            raise OrderError("invalid_items")
        variant_id = item.get("variantId")
        if _is_int(variant_id):
            priced.append(_item_for_variant(variant_id, quantity))
            continue
        match = _BASE_ID.fullmatch(variant_id) if isinstance(variant_id, str) else None
        if match is None:
            raise OrderError("invalid_items")
        priced.append(_item_for_base_product(int(match.group(1)), quantity))
    return priced


def _required_text(payload: dict, key: str, error: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OrderError(error)
    return value.strip()


def _notify(order: Order) -> None:
    """Send Telegram notifications; record failures on the order. Never raises."""
    errors = []
    for send in (telegram_bot.notify_admin_new_order, telegram_bot.notify_customer_status):
        try:
            error = send(order)
        except Exception as exc:  # a send failure must never undo the order
            current_app.logger.exception("Telegram notification failed for order %s", order.id)
            error = str(exc) or exc.__class__.__name__
        if error:
            errors.append(error)
    if not errors:
        return
    try:
        order.notify_error = "; ".join(errors)[:NOTIFY_ERROR_MAX]
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Could not record notify_error for order %s", order.id)


def create_order(customer, payload: dict) -> Order:
    """Validate, price and save an order in one transaction, then notify.

    Raises OrderError for client errors; nothing is saved in that case.
    """
    items = price_items(payload.get("items"))

    delivery_type = payload.get("delivery_type")
    if delivery_type not in DELIVERY_TYPES:
        raise OrderError("invalid_delivery")
    phone = _required_text(payload, "phone", "phone_required")
    address = _required_text(payload, "address", "address_required")
    maps_link = payload.get("maps_link")
    maps_link = (maps_link.strip() or None) if isinstance(maps_link, str) else None

    subtotal = _money(sum(i.unit_price * i.quantity for i in items))
    total_cost = _money(sum((i.unit_cost or 0) * i.quantity for i in items))

    voucher = None
    code = payload.get("voucher_code")
    if code is not None and not isinstance(code, str):
        raise OrderError("voucher_invalid")
    if code and code.strip():
        voucher, error = check_voucher(code, subtotal, phone, delivery_type)
        if error:
            raise OrderError(error)

    if delivery_type == "standard":
        fee_driver = standard_delivery_fee()
        fee_charged = 0.0 if voucher else fee_driver
    else:  # grab: the admin fills in the fee later
        fee_driver = fee_charged = None

    order = Order(
        customer_id=customer.id,
        status="new",
        delivery_type=delivery_type,
        delivery_fee_charged=fee_charged,
        delivery_fee_driver=fee_driver,
        voucher_code=voucher.code if voucher else None,
        subtotal=subtotal,
        total_cost=total_cost,
        total_to_collect=_money(subtotal + (fee_charged or 0)),
        phone=phone,
        address=address,
        maps_link=maps_link,
    )
    try:
        order.items.extend(items)
        order.status_logs.append(OrderStatusLog(from_status=None, to_status="new"))
        db.session.add(order)
        customer.phone = phone
        customer.address = address
        customer.maps_link = maps_link
        if voucher:
            redeem_voucher(voucher, phone)
        db.session.commit()
    except VoucherError as exc:
        db.session.rollback()
        raise OrderError(exc.code) from exc
    except Exception:
        db.session.rollback()
        raise

    _notify(order)
    return order

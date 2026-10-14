"""Server-side order creation: prices and costs always come from the database."""
import math
import re
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy.orm import selectinload

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

NEXT_STATUS = {"new": "confirmed", "confirmed": "packed", "packed": "delivering", "delivering": "delivered"}
KH_TZ = timezone(timedelta(hours=7))  # Cambodia has no daylight saving
_KH_OFFSET = KH_TZ.utcoffset(None)
STUCK_NEW_AFTER = timedelta(hours=3)
CASH_OVERDUE_AFTER = timedelta(hours=24)
DELIVERY_DEADLINE_HOUR = 21

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


def _notify(order: Order, include_admin: bool = True) -> None:
    """Send Telegram notifications; record failures on the order (and clear them on a clean round). Never raises.

    Senders are looked up on `telegram_bot` at call time so tests can replace them.
    """
    order_id = order.id  # read first: later commits expire the instance
    senders = [telegram_bot.notify_customer_status]
    if include_admin:
        senders.insert(0, telegram_bot.notify_admin_new_order)
    errors = []
    for send in senders:
        try:
            error = send(order)
        except Exception as exc:  # a send failure must never undo the order
            current_app.logger.exception("Telegram notification failed for order %s", order_id)
            error = str(exc) or exc.__class__.__name__
        if error:
            errors.append(error)
    try:
        if errors:
            order.notify_error = "; ".join(errors)[:NOTIFY_ERROR_MAX]
        elif order.notify_error is not None:  # every send worked (or was skipped): clear the old warning
            order.notify_error = None
        else:
            return
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Could not record notify_error for order %s", order_id)


def create_order(customer, payload: dict) -> Order:
    """Validate, price and save an order in one transaction, then notify.

    Raises OrderError for client errors; nothing is saved in that case.
    """
    items = price_items(payload.get("items"))

    delivery_type = payload.get("delivery_type")
    if delivery_type not in DELIVERY_TYPES:
        raise OrderError("invalid_delivery")
    # Phone is optional: customers are reachable on Telegram.
    phone = payload.get("phone")
    phone = (phone.strip()[:20] or None) if isinstance(phone, str) else None
    address = _required_text(payload, "address", "address_required")
    # Who may redeem a voucher once: the phone if given, else the Telegram account.
    voucher_holder = phone or f"tg:{customer.telegram_id}"
    maps_link = payload.get("maps_link")
    maps_link = (maps_link.strip() or None) if isinstance(maps_link, str) else None

    subtotal = _money(sum(i.unit_price * i.quantity for i in items))
    total_cost = _money(sum((i.unit_cost or 0) * i.quantity for i in items))

    voucher = None
    code = payload.get("voucher_code")
    if code is not None and not isinstance(code, str):
        raise OrderError("voucher_invalid")
    if code and code.strip():
        voucher, error = check_voucher(code, subtotal, voucher_holder, delivery_type)
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
        if phone:
            customer.phone = phone
        customer.address = address
        customer.maps_link = maps_link
        if voucher:
            redeem_voucher(voucher, voucher_holder)
        db.session.commit()
    except VoucherError as exc:
        db.session.rollback()
        raise OrderError(exc.code) from exc
    except Exception:
        db.session.rollback()
        raise

    _notify(order)
    return order


def advance_status(order: Order, to_status: str, now: datetime | None = None) -> None:
    """Move an order one stage forward, or cancel it, then tell the customer.

    Cancelling a delivered order is allowed here; the admin UI asks for confirmation.
    """
    if to_status != "cancelled" and NEXT_STATUS.get(order.status) != to_status:
        raise OrderError("invalid_transition")
    if to_status == "cancelled" and order.status == "cancelled":
        raise OrderError("invalid_transition")
    now = now or datetime.utcnow()
    order.status_logs.append(OrderStatusLog(from_status=order.status, to_status=to_status, created_at=now))
    order.status = to_status
    order.status_changed_at = now
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    _notify(order, include_admin=False)


def set_grab_fee(order: Order, fee: float) -> None:
    """Record the Grab driver's fee. The customer pays it in full, so profit is unaffected."""
    try:
        valid = order.delivery_type == "grab" and not isinstance(fee, bool) and math.isfinite(float(fee)) and fee >= 0
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise OrderError("invalid_fee")
    fee = _money(fee)
    order.delivery_fee_driver = order.delivery_fee_charged = fee
    order.total_to_collect = _money((order.subtotal or 0) + fee)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def set_cash_received(order: Order, received: bool, now: datetime | None = None) -> None:
    """Mark (or un-mark) the cash for a delivered order as handed over by the driver."""
    if order.status != "delivered":
        raise OrderError("invalid_transition")
    order.cash_received = bool(received)
    order.cash_received_at = (now or datetime.utcnow()) if received else None
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def _recompute_total_cost(order: Order) -> None:
    order.total_cost = _money(sum((item.unit_cost or 0) * (item.quantity or 0) for item in order.items))


def set_item_cost(order: Order, item: OrderItem, cost: float) -> None:
    """Set (or correct) one item's cost price, then recompute the order's total cost."""
    try:
        valid = (item.order_id == order.id and not isinstance(cost, bool)
                 and math.isfinite(float(cost)) and cost >= 0)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise OrderError("invalid_cost")
    item.unit_cost = _money(cost)
    _recompute_total_cost(order)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def fill_missing_costs() -> int:
    """Copy today's product/variant cost into non-cancelled order items that have none.

    Items whose product or variant still has no cost are left alone. Returns how many items were filled.
    """
    items = (OrderItem.query.join(Order, OrderItem.order_id == Order.id)
             .filter(Order.status != "cancelled", OrderItem.unit_cost.is_(None)).all())
    filled = 0
    touched = set()
    for item in items:
        if item.variant_id is not None:
            source = db.session.get(ProductVariant, item.variant_id)
        else:
            source = db.session.get(Product, item.product_id) if item.product_id is not None else None
        if source is None or source.cost is None:
            continue
        item.unit_cost = _money(source.cost)
        touched.add(item.order)
        filled += 1
    if not filled:
        return 0
    for order in touched:
        _recompute_total_cost(order)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return filled


def cost_incomplete(order: Order) -> bool:
    """True when any item has no cost price, so the order's profit is overstated."""
    return any(item.unit_cost is None for item in order.items)


def order_profit(order: Order) -> float:
    """subtotal - cost - delivery subsidy (what the shop pays the driver beyond what the customer paid)."""
    subsidy = max(0, (order.delivery_fee_driver or 0) - (order.delivery_fee_charged or 0))
    return round((order.subtotal or 0) - (order.total_cost or 0) - subsidy, 2)


def driver_payback(order: Order) -> float:
    """What the driver hands back to the shop: cash collected minus the driver's own fee."""
    return round((order.total_to_collect or 0) - (order.delivery_fee_driver or 0), 2)


def needs_attention(order: Order, now: datetime | None = None) -> list[str]:
    """Reason codes for why an order needs the admin's attention. Cancelled orders never do."""
    if order.status == "cancelled":
        return []
    now = now or datetime.utcnow()
    reasons = []
    if cost_incomplete(order):
        reasons.append("missing_cost")
    if (order.status == "delivered" and not order.cash_received and order.status_changed_at
            and now - order.status_changed_at > CASH_OVERDUE_AFTER):
        reasons.append("cash_overdue")
    if order.status == "new" and order.created_at and now - order.created_at > STUCK_NEW_AFTER:
        reasons.append("stuck_new")
    if order.status == "delivering" and order.status_changed_at:
        # Deliveries should arrive by 21:00 Cambodia time on the day the order went out.
        delivery_day = (order.status_changed_at + _KH_OFFSET).replace(
            hour=DELIVERY_DEADLINE_HOUR, minute=0, second=0, microsecond=0)
        if now + _KH_OFFSET >= delivery_day:
            reasons.append("stuck_delivering")
    if order.status == "delivered" and order.delivery_type == "grab" and order.delivery_fee_driver is None:
        reasons.append("grab_fee_missing")
    return reasons


def range_bounds(range_key: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Naive-UTC [start, end) for "today", "7d" or "month", on the Cambodia calendar."""
    now_kh = (now or datetime.utcnow()) + _KH_OFFSET
    today = now_kh.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == "today":
        start, end = today, today + timedelta(days=1)
    elif range_key == "7d":
        start, end = today - timedelta(days=6), today + timedelta(days=1)
    elif range_key == "month":
        start = today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
    else:
        raise OrderError("invalid_range")
    return start - _KH_OFFSET, end - _KH_OFFSET


def summary(range_key: str, now: datetime | None = None) -> dict:
    """Order count, sales and profit (non-cancelled, by created_at) plus cash still with drivers.

    `orders_missing_cost` counts the orders in the range whose profit is incomplete (an item has no cost).
    """
    start, end = range_bounds(range_key, now)
    orders = Order.query.filter(
        Order.created_at >= start, Order.created_at < end, Order.status != "cancelled"
    ).options(selectinload(Order.items)).all()
    awaiting_cash = Order.query.filter(
        Order.status == "delivered", Order.cash_received.isnot(True)
    ).all()
    return {
        "orders": len(orders),
        "sales": round(sum(o.subtotal or 0 for o in orders), 2),
        "profit": round(sum(order_profit(o) for o in orders), 2),
        "orders_missing_cost": sum(1 for o in orders if cost_incomplete(o)),
        "cash_with_drivers": round(sum(driver_payback(o) for o in awaiting_cash), 2),
    }

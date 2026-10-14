import os
import tempfile

import pytest

# Must run BEFORE `from app import app`: app.py creates tables and probes
# Redis at import time.
_TMP_DIR = tempfile.mkdtemp(prefix="angkorkey_test_")
_DB_PATH = os.path.join(_TMP_DIR, "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH.replace("\\", "/")
os.environ["REDIS_URL"] = "redis://127.0.0.1:1"  # unreachable -> non-Redis fallback

from app import app as flask_app  # noqa: E402
from extensions import db, limiter, cache  # noqa: E402


def _refuse_unless_temp_db():
    uri = flask_app.config["SQLALCHEMY_DATABASE_URI"]
    repo_instance = os.path.realpath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance")
    )
    path = os.path.realpath(uri.split("sqlite:///", 1)[-1])
    try:
        inside_instance = os.path.commonpath([path, repo_instance]) == repo_instance
    except ValueError:  # different drives -> cannot be inside
        inside_instance = False
    if uri != os.environ["DATABASE_URL"] or inside_instance:
        pytest.exit("refusing to run: tests not pointed at temp DB", returncode=2)


# Hard guard: runs at conftest import, before any fixture can touch the DB.
_refuse_unless_temp_db()


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    """Never reach the real Telegram API: no env token by default, and urlopen is blocked.

    Tests that need a token use `bot_token`; tests that need urlopen patch it themselves.
    """
    import urllib.request

    def _blocked(*args, **kwargs):
        raise AssertionError("real network call")

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)


@pytest.fixture
def app():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        limiter.enabled = False
        cache.clear()
        yield flask_app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(app):
    """A test client signed in as an admin (session user_id)."""
    from models import User

    user = User(username="tester", password="x")
    db.session.add(user)
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user.id
    return client


@pytest.fixture
def db_session(app):
    return db


@pytest.fixture
def make_product(app):
    def _make(price=10.0, cost=6.0, status="true", with_variant=False,
              variant_price=12.0, variant_cost=7.0):
        from models import Category, Product
        from models.ProductVariant import ProductVariant, StockStatus

        category = Category.query.filter_by(name="Test Category").first()
        if category is None:
            category = Category(name="Test Category", status="true")
            db.session.add(category)
            db.session.flush()

        count = Product.query.count() + 1
        product = Product(
            name=f"Test Product {count}",
            category_id=category.id,
            price=price,
            cost=cost,
            status=status,
        )
        db.session.add(product)
        db.session.flush()

        variant = None
        if with_variant:
            variant = ProductVariant(
                product_id=product.id,
                price=variant_price,
                cost=variant_cost,
                stock=StockStatus.IN_STOCK,
                status="true",
                option_key="default",
            )
            db.session.add(variant)

        db.session.commit()
        return product, variant

    return _make


@pytest.fixture
def make_customer(app):
    def _make(telegram_id=111, first_name="Sokha", can_message=True):
        from models import Customer

        customer = Customer(
            telegram_id=telegram_id,
            first_name=first_name,
            can_message=can_message,
        )
        db.session.add(customer)
        db.session.commit()
        return customer

    return _make


@pytest.fixture
def bot_token(monkeypatch):
    token = "123:ABC"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    return token


@pytest.fixture
def login_as(app, client):
    """Sign a customer in by setting a real, signed ak_customer cookie on `client`."""
    from flask import make_response

    from services.customer_session import COOKIE_NAME, issue_customer_cookie

    def _login(customer):
        with app.test_request_context("/"):
            response = make_response("")
            issue_customer_cookie(response, customer.id)
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        client.set_cookie(COOKIE_NAME, cookie.split("=", 1)[1])
        return customer

    return _login


@pytest.fixture
def order_factory(app, make_customer):
    """Build and save a full Order with one item. Keyword overrides replace the defaults."""
    import itertools
    from datetime import datetime

    from models import Order, OrderItem, OrderStatusLog

    telegram_ids = itertools.count(900001)

    def _make(customer=None, status="new", delivery_type="standard", subtotal=10.0,
              total_cost=6.0, fee_charged=2.0, fee_driver=2.0, item_cost=6.0,
              created_at=None, status_changed_at=None, cash_received=False):
        if customer is None:
            customer = make_customer(telegram_id=next(telegram_ids))
        now = datetime.utcnow()
        created_at = created_at or now
        status_changed_at = status_changed_at or now
        order = Order(
            customer_id=customer.id,
            status=status,
            delivery_type=delivery_type,
            delivery_fee_charged=fee_charged,
            delivery_fee_driver=fee_driver,
            subtotal=subtotal,
            total_cost=total_cost,
            total_to_collect=round(subtotal + (fee_charged or 0), 2),
            phone="012345678",
            address="Phnom Penh",
            cash_received=cash_received,
            created_at=created_at,
            status_changed_at=status_changed_at,
        )
        order.items.append(OrderItem(
            name="Test Item", options_text="", unit_price=subtotal,
            unit_cost=item_cost, quantity=1,
        ))
        order.status_logs.append(OrderStatusLog(
            from_status=None, to_status=status, created_at=created_at,
        ))
        db.session.add(order)
        db.session.commit()
        return order

    return _make

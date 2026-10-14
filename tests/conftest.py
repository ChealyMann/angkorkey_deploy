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
        from models import Customer  # added in Task 2

        customer = Customer(
            telegram_id=telegram_id,
            first_name=first_name,
            can_message=can_message,
        )
        db.session.add(customer)
        db.session.commit()
        return customer

    return _make

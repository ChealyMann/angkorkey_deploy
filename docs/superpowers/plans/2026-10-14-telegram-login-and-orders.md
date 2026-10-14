# Telegram Login & Order Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Customers sign in with Telegram (silently in the Mini App, with the Login Widget in a browser), and every order is saved on the server. The owner manages orders, profit and "needs a look" problems from the mobile admin panel, and the bot notifies the owner and the customer.

**Architecture:**
- New `Customer`, `Order`, `OrderItem` and `OrderStatusLog` models, with an idempotent Alembic migration.
- Pure service modules hold all logic: Telegram signature checks, the customer cookie, vouchers, order pricing and creation, lifecycle and reporting, and bot messages.
- Thin Flask blueprints (`customer_bp` for the storefront and API, `admin_orders_bp` for the mobile admin) call those services.
- Frontend changes are Jinja templates plus one small JS file.

**Tech Stack:**
- Flask 3.1, Flask-SQLAlchemy 3.1 (SQLite), Flask-Migrate/Alembic, Flask-Limiter, `itsdangerous`
- Jinja2 + Tailwind (storefront), the existing Bootstrap-ish mobile admin CSS (`keyz-*` classes)
- pytest (new, dev only)
- Telegram Bot API over `urllib` (as `blueprint/admin/mobile.py` already does) and `telegram-web-app.js`

**Spec:** `docs/superpowers/specs/2026-10-14-telegram-login-and-orders-design.md`. Read it before starting any task.

## Global Constraints

**Git and data safety**
- Work on branch `feature/telegram-login-orders`. **Never push to `main`**, because it triggers the cPanel deploy.
- **No test may touch `instance/app.db`.** Tests run against a temp SQLite file through the `DATABASE_URL` env var (Task 1). Never run `test_crud.py` directly.

**Prices, money and time**
- Prices and costs always come from the database (`ProductVariant.price/cost`, or `Product.price/cost` for `base_<id>` items). Never use client-sent prices.
- Money is stored as `db.Float` (matching the codebase) and rounded to 2 decimals whenever it is computed.
- Store timestamps as naive UTC (`datetime.utcnow()`, as existing models do).
- Reports and "today" use Cambodia time: a fixed `timezone(timedelta(hours=7))`, since Cambodia has no DST. Don't use `zoneinfo`, which needs `tzdata` on Windows.

**Fixed values from the spec**
- Standard delivery fee: setting `standard_delivery_fee`, default `2.00`.
- Vouchers apply to Standard delivery only and mean free standard delivery.
- Telegram auth data older than 24h (86400s) is rejected.
- Customer cookie: name `ak_customer`, lifetime 30 days, `HttpOnly`, `SameSite=Lax`, `Secure` when `request.is_secure`. It is separate from the admin `session`.
- The admin session (`session['user_id']`, 30 min) is unchanged.
- `POST /api/orders` rate limit: `10 per minute`, keyed by customer.
- Status values: `new`, `confirmed`, `packed`, `delivering`, `delivered`, `cancelled`.
- Bot sends use `sendMessage` with a 5-second timeout. A send failure never raises to the caller or undoes an order.
- "Needs a look" thresholds:
  - `new` for more than 3h
  - `delivering` after 21:00 Cambodia time on the order's delivery day
  - delivered with no cash after 24h
  - any item with a null cost
  - Grab delivered with no driver fee

**Code and copy rules**
- Every customer-facing string has `en` and `km` entries in `translations.py`. Use the `_('key')` helper in templates.
- The bot token comes from `os.environ["TELEGRAM_BOT_TOKEN"]` if set, else `Setting.get_val("telegram_bot_token")`.
- No new runtime dependencies. Dev-only: `pytest`.
- Follow existing style: Blueprints, `Model.query`, 4-space PEP 8, `snake_case`.

## Review Focus

1. **Cached storefront pages must never show a customer's identity.** `home`, `all_categories` and `all_brands` use `@cache.cached`, so any per-customer server-side markup in `main.html`/`header.html` would be served to everyone. The account link must be static, with identity fetched client-side from `/api/me`. Test: Task 8, `test_cached_home_does_not_leak_customer`.
2. **Tampered cart payloads.** Extra `price` fields are ignored. Unknown, inactive or other-product variant IDs, `base_<id>` for a product that has variants, quantity ≤ 0, non-integer or over 99, and an empty cart are all rejected with 400 and nothing is saved. Test: Task 5, `test_create_order_rejects_bad_items`.
3. **Double-tap confirm or replayed submissions with a voucher.** The voucher is redeemed once, and the second order with the same voucher and phone is rejected with `voucher_already_used`. Test: Task 5, `test_voucher_redeemed_once`.
4. **Forged, stale or other-bot Telegram auth data** is rejected, and no customer is created. Test: Task 3, `test_verify_rejects_*`, and Task 4, `test_webapp_login_rejects_bad_hash`.
5. **Telegram API down or slow during checkout.** The order is still saved and returned, and `notify_error` is recorded on the order. Test: Task 5, `test_order_saved_when_telegram_fails`.

---

## File Structure

| File | Responsibility |
|---|---|
| `app.py` (modify) | `DATABASE_URL` override, register new blueprints |
| `pytest.ini`, `requirements-dev.txt`, `tests/conftest.py` (create) | Test harness on a temp DB |
| `models/Customer.py` (create) | `Customer` model |
| `models/Order.py` (create) | `Order`, `OrderItem`, `OrderStatusLog`, status constants |
| `models/__init__.py` (modify) | export new models |
| `migrations/versions/7c1e4b2a9f30_add_customer_and_orders.py` (create) | idempotent migration |
| `services/telegram_auth.py` (create) | Mini App and Login Widget signature checks |
| `services/customer_session.py` (create) | signed `ak_customer` cookie, `current_customer()` |
| `services/security.py` (create) | `require_same_origin()` for JSON POSTs |
| `services/vouchers.py` (create) | shared voucher rules, extracted from `blueprint/home.py` |
| `services/telegram_bot.py` (create) | bot token, `send_message`, order and status messages |
| `services/orders.py` (create) | pricing, `create_order`, lifecycle, profit, needs-a-look, summaries |
| `blueprint/customer.py` (create) | `/auth/telegram/*`, `/auth/logout`, `/api/me*`, `/api/orders`, `/my-orders*` |
| `blueprint/admin/orders.py` (create) | `/admin/mobile/orders*`, settings additions, test message |
| `blueprint/home.py` (modify) | use `services/vouchers.py`, drop `/api/use_voucher`, stop exposing `cost` |
| `blueprint/admin/mobile.py` (modify) | settings: save two new keys |
| `templates/backend/admin/mobile/order/list.html`, `detail.html` (create) | admin Orders screens |
| `templates/backend/admin/mobile/mobile_master.html`, `settings.html` (modify) | Orders nav tab + badge; new settings fields |
| `static/js/telegram-auth.js` (create) | Mini App silent sign-in, Login Widget glue |
| `templates/frontend/main.html`, `layouts/header.html` (modify) | load the scripts; static account link |
| `templates/frontend/pages/cart.html` (modify) | submit to `/api/orders` |
| `templates/frontend/pages/my_orders.html`, `my_order_detail.html`, `sign_in.html` (create) | customer pages |
| `translations.py` (modify) | new en/km keys |

---

### Task 1: Test harness on a temp database

**Files:**
- Modify: `app.py` (the `SQLALCHEMY_DATABASE_URI` line, ~line 72)
- Create: `pytest.ini`, `requirements-dev.txt`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_harness.py`

**Interfaces:**
- Produces fixtures:
  - `app` (Flask app bound to a fresh temp SQLite DB, all tables created, limiter disabled, cache cleared)
  - `client`
  - `db_session` (the `db` from `extensions`)
  - `make_product(price=10.0, cost=6.0, status="true", with_variant=False, variant_price=12.0, variant_cost=7.0) -> (Product, ProductVariant|None)`
  - `make_customer(telegram_id=111, first_name="Sokha", can_message=True) -> Customer`, which is added in Task 2 and is safe to define now behind an import inside the fixture
- Produces env vars that tests rely on: `DATABASE_URL`, and `REDIS_URL=redis://127.0.0.1:1` to force the non-Redis fallback.

- [ ] **Step 1: Set up a local venv.**
  Run `python -m venv .venv`, then `.venv/Scripts/python -m pip install -r requirements.txt -r requirements-dev.txt`. `requirements-dev.txt` contains `pytest==8.3.3`.
  Expected: the install succeeds. `gunicorn` installs but is unused on Windows.

- [ ] **Step 2: Write the failing test** `tests/test_harness.py`

```python
import os

def test_app_uses_temp_database(app):
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    assert uri == os.environ["DATABASE_URL"]
    assert "instance" not in uri.replace("\\", "/").split("/")[-2:]
```

- [ ] **Step 3: Write `pytest.ini` and `tests/conftest.py`.**
  - `pytest.ini`: `testpaths = tests`, so the root `test_crud.py` is never collected.
  - `conftest.py`:
    1. At import time, *before* `from app import app`, set `os.environ["DATABASE_URL"] = "sqlite:///" + <tmp file path>` and `os.environ["REDIS_URL"] = "redis://127.0.0.1:1"`.
    2. The `app` fixture sets `TESTING=True` and `WTF_CSRF_ENABLED=False`, runs `db.drop_all(); db.create_all()` inside `app.app_context()`, sets `limiter.enabled = False` and runs `cache.clear()`.
    3. It yields inside the app context.

- [ ] **Step 4: Run** `.venv/Scripts/python -m pytest tests/test_harness.py -v`
  Expected: FAIL. The URI is still `instance/app.db`.

- [ ] **Step 5: In `app.py`, read the DB URI from the environment.**
  `app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(INSTANCE_DIR, "app.db"))`

- [ ] **Step 6: Run** the same command. Expected: PASS. Then run `git status` and confirm `instance/app.db` is not modified.

- [ ] **Step 7: Commit**

```bash
git add app.py pytest.ini requirements-dev.txt tests/
git commit -m "test: add pytest harness on a temp database"
```

---

### Task 2: Customer and Order models + idempotent migration

**Files:**
- Create: `models/Customer.py`, `models/Order.py`, `migrations/versions/7c1e4b2a9f30_add_customer_and_orders.py`, `tests/test_models.py`, `tests/test_migration.py`
- Modify: `models/__init__.py` (add `from .Customer import Customer` and `from .Order import Order, OrderItem, OrderStatusLog, ORDER_STATUSES`)

**Interfaces:**
- Produces `Customer`, table `customer`:
  - `id`
  - `telegram_id` (BigInteger, unique, index, not null)
  - `first_name` (String 128), `last_name` (String 128), `username` (String 64), `photo_url` (String 512)
  - `phone` (String 20), `address` (Text), `maps_link` (String 512)
  - `can_message` (Boolean, default False, not null)
  - `lang` (String 2, default `"en"`, not null): the language used for bot messages
  - `created_at`, `last_login_at` (DateTime)
  - property `display_name -> str`: `first_name` + `" " + last_name` if set, else `"@"+username`, else `f"#{telegram_id}"`
- Produces `Order`, table `orders`. **Note the plural: `order` is an SQL keyword.**
  - `id`
  - `customer_id` (FK `customer.id`, index, not null)
  - `status` (String 16, index, default `"new"`)
  - `delivery_type` (String 16)
  - `delivery_fee_charged` (Float, nullable), `delivery_fee_driver` (Float, nullable)
  - `voucher_code` (String 50, nullable)
  - `subtotal` (Float), `total_cost` (Float), `total_to_collect` (Float)
  - `phone` (String 20), `address` (Text), `maps_link` (String 512)
  - `admin_note` (Text)
  - `cash_received` (Boolean, default False), `cash_received_at`
  - `notify_error` (String 255, nullable)
  - `created_at` (index), `updated_at`, `status_changed_at`
  - relationships: `customer`, `items` (cascade all, delete-orphan), `status_logs` (ordered by `created_at`)
- Produces `OrderItem`, table `order_item`:
  - `id`, `order_id` (FK, index)
  - `product_id` (FK `product.id`), `variant_id` (FK `product_variant.id`, nullable)
  - `name` (String 100), `options_text` (String 255)
  - `unit_price` (Float), `unit_cost` (Float, nullable), `quantity` (Integer)
- Produces `OrderStatusLog`, table `order_status_log`: `id`, `order_id` (FK, index), `from_status`, `to_status`, `created_at`.
- Produces `ORDER_STATUSES = ("new", "confirmed", "packed", "delivering", "delivered", "cancelled")`.
- The migration has `revision = "7c1e4b2a9f30"` and `down_revision = "d0f67f256061"`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_models.py
from models import Customer, Order, OrderItem

def test_customer_display_name(db_session):
    c = Customer(telegram_id=5, first_name="Sokha", last_name="Chan")
    assert c.display_name == "Sokha Chan"
    assert Customer(telegram_id=6, username="dara").display_name == "@dara"
    assert Customer(telegram_id=7).display_name == "#7"

def test_order_with_items_persists(db_session, make_product, make_customer):
    product, _ = make_product()
    customer = make_customer()
    order = Order(customer=customer, status="new", delivery_type="standard",
                  subtotal=10.0, total_cost=6.0, total_to_collect=12.0,
                  delivery_fee_charged=2.0, delivery_fee_driver=2.0,
                  phone="012345678", address="PP")
    order.items.append(OrderItem(product_id=product.id, name=product.name,
                                 unit_price=10.0, unit_cost=6.0, quantity=1))
    db_session.session.add(order); db_session.session.commit()
    assert Order.query.one().items[0].unit_cost == 6.0
    assert Order.__tablename__ == "orders"
```

```python
# tests/test_migration.py
from flask_migrate import stamp, upgrade
from sqlalchemy import inspect

def test_migration_is_idempotent_over_create_all(app, db_session):
    # Mirrors production: create_all() already made every table, DB stamped at old head.
    stamp(revision="d0f67f256061")
    upgrade()  # must not raise "table already exists"
    tables = inspect(db_session.engine).get_table_names()
    for t in ("customer", "orders", "order_item", "order_status_log"):
        assert t in tables
    assert db_session.session.execute(db_session.text("select version_num from alembic_version")).scalar() == "7c1e4b2a9f30"

def test_migration_creates_tables_when_missing(app, db_session):
    for t in ("order_status_log", "order_item", "orders", "customer"):
        db_session.session.execute(db_session.text(f"DROP TABLE IF EXISTS {t}"))
    db_session.session.commit()
    stamp(revision="d0f67f256061")
    upgrade()
    assert "orders" in inspect(db_session.engine).get_table_names()
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_models.py tests/test_migration.py -v`. Expected: FAIL with an ImportError.

- [ ] **Step 3: Implement the models** with exactly the fields above, and export them from `models/__init__.py`. Add the `make_customer` fixture to `conftest.py`.

- [ ] **Step 4: Write the migration.**
  - In `upgrade()`, get `insp = sa.inspect(op.get_bind())` and call `op.create_table(...)` only when `not insp.has_table(name)`.
  - Create indexes only when they are missing from `insp.get_indexes(name)`. Order: `customer`, `orders`, `order_item`, `order_status_log`.
  - `downgrade()` drops the four tables in reverse order.

- [ ] **Step 5: Run** the tests. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add models/ migrations/versions/7c1e4b2a9f30_add_customer_and_orders.py tests/
git commit -m "feat(orders): add customer and order models with idempotent migration"
```

---

### Task 3: Telegram signature verification

**Files:**
- Create: `services/telegram_auth.py`, `tests/test_telegram_auth.py`

**Interfaces:**
- Produces `verify_webapp_init_data(init_data: str, bot_token: str, max_age: int = 86400, now: float | None = None) -> dict | None`. It returns the parsed `user` dict (with `allows_write_to_pm` included if present) on success, else `None`.
- Produces `verify_login_widget(data: dict, bot_token: str, max_age: int = 86400, now: float | None = None) -> dict | None`. It returns `{"id", "first_name", "last_name", "username", "photo_url"}` on success, else `None`.
- Both use `hmac.compare_digest`, both return `None` on any parse error, and both return `None` if `bot_token` is empty.

Algorithms (fixed by Telegram, copy exactly):

```python
# Mini App: data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs) if k != "hash")
# pairs come from urllib.parse.parse_qsl(init_data, keep_blank_values=True)
secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()

# Login Widget: data_check_string over all fields except "hash", sorted, "k=v" joined by "\n"
secret = hashlib.sha256(bot_token.encode()).digest()
expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
```

- [ ] **Step 1: Write failing tests.**
  - Include a test helper `sign_webapp(fields: dict, token) -> str` that builds a valid query string using the algorithm above, and a matching `sign_widget(fields, token) -> dict`.
  - Use `now=1_800_000_000` with `auth_date=now-60`.
  - The `user` field is `json.dumps({"id": 42, "first_name": "Sokha", "allows_write_to_pm": True})`.

```python
def test_verify_webapp_accepts_valid():
    user = verify_webapp_init_data(sign_webapp(FIELDS, "123:ABC"), "123:ABC", now=NOW)
    assert user["id"] == 42 and user["allows_write_to_pm"] is True

def test_verify_rejects_tampered_hash():
    s = sign_webapp(FIELDS, "123:ABC").replace("Sokha", "Dara")
    assert verify_webapp_init_data(s, "123:ABC", now=NOW) is None

def test_verify_rejects_other_bot_token():
    assert verify_webapp_init_data(sign_webapp(FIELDS, "999:XYZ"), "123:ABC", now=NOW) is None

def test_verify_rejects_stale_auth_date():
    stale = dict(FIELDS, auth_date=str(NOW - 86401))
    assert verify_webapp_init_data(sign_webapp(stale, "123:ABC"), "123:ABC", now=NOW) is None

def test_verify_rejects_empty_token_and_garbage():
    assert verify_webapp_init_data(sign_webapp(FIELDS, ""), "", now=NOW) is None
    assert verify_webapp_init_data("not a query", "123:ABC", now=NOW) is None

def test_verify_widget_accepts_valid_and_rejects_tampered():
    data = sign_widget({"id": "42", "first_name": "Sokha", "auth_date": str(NOW - 5)}, "123:ABC")
    assert verify_login_widget(data, "123:ABC", now=NOW)["id"] == 42
    assert verify_login_widget(dict(data, first_name="X"), "123:ABC", now=NOW) is None
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_telegram_auth.py -v`. Expected: FAIL with an ImportError.
- [ ] **Step 3: Implement** both functions. Return `id` as an `int`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat(auth): verify Telegram Mini App and Login Widget signatures"`

---

### Task 4: Customer sign-in endpoints and cookie

**Files:**
- Create: `services/customer_session.py`, `services/security.py`, `blueprint/customer.py`, `tests/test_customer_auth.py`
- Modify: `app.py` (register `customer_bp`), `tests/conftest.py` (add fixtures `bot_token` and `login_as(customer)`)

**Interfaces:**
- Consumes: Task 3's verify functions, Task 2's `Customer`.
- Produces in `services/customer_session.py`:
  - `COOKIE_NAME = "ak_customer"`, `MAX_AGE = 30 * 24 * 3600`
  - `issue_customer_cookie(response, customer_id: int) -> None`. It uses `URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="customer-auth")`. The cookie flags are as in Global Constraints.
  - `current_customer() -> Customer | None`. It caches on `flask.g.customer` and returns None for a bad or expired signature or a deleted customer.
  - `clear_customer_cookie(response) -> None`
  - Refresh: an `after_app_request` hook on `customer_bp` re-issues the cookie when the token is older than 1 day and the customer is valid.
- Produces in `services/security.py`: `require_same_origin() -> None`.
  - It calls `abort(403)` unless the `Origin` header (or, failing that, `Referer`) has a host equal to `request.host`.
  - It is skipped when `current_app.testing` is true **and** the header `X-Test-Skip-Origin: 1` is present. Tests normally send `Origin: http://localhost`.
- Produces in `services/telegram_bot.py`: `get_bot_token() -> str`, which is env first, then setting, else `""`. Place it here now; Task 6 extends the file.
- Produces `upsert_customer(tg_user: dict, can_message: bool | None) -> Customer`. It lives in `blueprint/customer.py`, sets `last_login_at`, refreshes the name, username and photo, sets `lang = session.get("lang", "en")`, and updates `can_message` only when the value is not None.
- Produces these routes on `customer_bp` (no url_prefix):

| Route | Behaviour |
|---|---|
| `POST /auth/telegram/webapp` | JSON `{"init_data": str}` → 200 `{"ok": true, "customer": {...}}` and sets the cookie; 401 `{"ok": false}` on a bad signature. `can_message` comes from `allows_write_to_pm`. |
| `POST /auth/telegram/widget` | JSON is the widget's user object → same responses. Sets `can_message=True` (the widget uses `data-request-access="write"`). |
| `POST /auth/logout` | clears the cookie, 200 |
| `GET /api/me` | `{"signed_in": bool, "first_name": str?, "can_message": bool?, "phone": str?, "address": str?, "maps_link": str?}` |
| `POST /api/me/write-access` | JSON `{"allowed": bool}` → sets `can_message`, 200; 401 if not signed in |

  The customer JSON is `{"id", "display_name", "can_message"}`. All POSTs call `require_same_origin()`.

- [ ] **Step 1: Write failing tests** in `tests/test_customer_auth.py`. Reuse the Task 3 signing helpers by moving them into `tests/helpers.py`.

```python
def test_webapp_login_creates_customer_and_sets_cookie(client, bot_token):
    r = client.post("/auth/telegram/webapp", json={"init_data": signed_init_data(bot_token)},
                    headers={"Origin": "http://localhost"})
    assert r.status_code == 200 and r.json["ok"] is True
    assert "ak_customer=" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
    assert Customer.query.one().telegram_id == 42
    assert client.get("/api/me").json["signed_in"] is True

def test_webapp_login_rejects_bad_hash(client, bot_token):
    r = client.post("/auth/telegram/webapp", json={"init_data": "user=%7B%7D&auth_date=1&hash=00"},
                    headers={"Origin": "http://localhost"})
    assert r.status_code == 401 and Customer.query.count() == 0

def test_login_twice_updates_same_customer(client, bot_token): ...  # count stays 1, first_name refreshed

def test_cross_origin_post_forbidden(client, bot_token):
    r = client.post("/auth/telegram/webapp", json={"init_data": signed_init_data(bot_token)},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403

def test_customer_cookie_does_not_grant_admin(client, login_as, make_customer):
    login_as(make_customer())
    r = client.get("/admin/mobile/dashboard")
    assert r.status_code == 302 and "/0964246058" in r.headers["Location"]

def test_tampered_cookie_is_anonymous(client):
    client.set_cookie("ak_customer", "garbage")
    assert client.get("/api/me").json == {"signed_in": False}

def test_logout_clears(client, login_as, make_customer): ...  # /api/me signed_in False afterwards
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_customer_auth.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `services/customer_session.py`, `services/security.py`, `get_bot_token()` and `blueprint/customer.py` with the routes above. Register the blueprint in `app.py` next to the others.
- [ ] **Step 4: Run** the tests. Expected: PASS. Then run the full suite: `.venv/Scripts/python -m pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with `git commit -m "feat(auth): customer sign-in via Telegram with signed 30-day cookie"`

---

### Task 5: Order creation (`POST /api/orders`) with server-side pricing and vouchers

**Files:**
- Create: `services/vouchers.py`, `services/orders.py`, `tests/test_orders_create.py`, `tests/test_vouchers.py`
- Modify:
  - `blueprint/home.py`: `api_validate_voucher` uses `services.vouchers`. Delete `api_use_voucher` and move `normalize_phone` into `services/vouchers.py`, importing it back where it's used.
  - `blueprint/customer.py`: add the route.
  - `services/telegram_bot.py`: add notify stubs that Task 6 will fill in.

**Interfaces:**
- Consumes: `current_customer()` (Task 4), the models (Task 2), `Setting.get_val`.
- Produces in `services/vouchers.py`:
  - `normalize_phone(phone: str) -> str` (moved verbatim)
  - `check_voucher(code: str, subtotal: float, phone: str, delivery_type: str = "standard") -> tuple[Voucher | None, str | None]`. It returns `(voucher, None)` or `(None, error_code)`. Error codes:
    - `voucher_required`
    - `voucher_invalid`
    - `voucher_inactive`
    - `voucher_limit_reached`
    - `voucher_min_spend`
    - `voucher_already_used`
    - `voucher_standard_only` (any non-`standard` delivery)
  - `redeem_voucher(voucher: Voucher, phone: str) -> None`. It adds a `VoucherRedemption` and increments `usage_count`, with **no commit**.
- `/api/validate_voucher` keeps its JSON shape. It maps codes to the existing English messages and adds a `delivery` query param, which defaults to `standard`.
- Produces in `services/orders.py`:
  - `class OrderError(Exception)` with `.code: str` and `.status: int = 400`
  - `MAX_QTY = 99`
  - `price_items(items: list[dict]) -> list[OrderItem]` (unsaved). Each input is `{"variantId": int | "base_<productId>", "quantity": int}`.
    - It raises `OrderError("invalid_items")` for: an empty list, more than 50 lines, bad quantities, unknown or inactive products/variants, `base_<id>` where the product has active variants, or a variant whose parent product is inactive.
    - `options_text` for variants is the variant's option values joined with `" / "`, else `""`.
  - `create_order(customer: Customer, payload: dict) -> Order`. It commits and returns the order. Payload keys: `items`, `delivery_type` (`standard`|`grab`, else `invalid_delivery`), `phone` (required, else `phone_required`), `address` (required, else `address_required`), `maps_link`, `voucher_code`.
    - **Standard:** `delivery_fee_driver = standard fee`, and `delivery_fee_charged` is `0.0` with a voucher, else the standard fee.
    - **Grab:** both fees are `None`.
    - `total_to_collect = subtotal + (delivery_fee_charged or 0)`.
    - `total_cost` is the sum of `(unit_cost or 0) * qty`.
    - It writes the initial `OrderStatusLog(from_status=None, to_status="new")`, saves `phone`, `address` and `maps_link` to the customer, and commits once.
    - **After the commit** it calls `telegram_bot.notify_admin_new_order(order)` and `telegram_bot.notify_customer_status(order)`. Each returns `str | None` (an error). If either errors, it sets `order.notify_error` and commits again. It never raises.
- Route `POST /api/orders`: `require_same_origin()`; 401 `{"ok": false, "error": "sign_in_required"}` without a customer; `@limiter.limit("10 per minute", key_func=lambda: f"customer:{request.cookies.get('ak_customer','anon')}")`. Responses: 201 `{"ok": true, "order_id": int, "url": "/my-orders/<id>?new=1"}`, or 400 `{"ok": false, "error": code}`.

- [ ] **Step 1: Write failing tests**

```python
def test_create_order_prices_from_database(client, login_as, make_customer, make_product):
    _, variant = make_product(with_variant=True, variant_price=12.0, variant_cost=7.0)
    login_as(make_customer())
    r = post_order(client, items=[{"variantId": variant.id, "quantity": 2, "price": 0.01}])
    assert r.status_code == 201
    o = Order.query.one()
    assert (o.subtotal, o.total_cost, o.delivery_fee_charged, o.delivery_fee_driver, o.total_to_collect) == (24.0, 14.0, 2.0, 2.0, 26.0)
    assert o.status == "new" and o.items[0].unit_price == 12.0 and o.status_logs[0].to_status == "new"

def test_create_order_rejects_bad_items(client, login_as, make_customer, make_product):
    p_inactive, _ = make_product(status="false")
    p_var, v = make_product(with_variant=True)
    login_as(make_customer())
    for items in ([], [{"variantId": 999999, "quantity": 1}], [{"variantId": f"base_{p_inactive.id}", "quantity": 1}],
                  [{"variantId": f"base_{p_var.id}", "quantity": 1}], [{"variantId": v.id, "quantity": 0}],
                  [{"variantId": v.id, "quantity": 100}], [{"variantId": v.id, "quantity": "2"}]):
        r = post_order(client, items=items)
        assert r.status_code == 400 and r.json["error"] == "invalid_items", items
    assert Order.query.count() == 0

def test_order_requires_sign_in(client, make_product): ...      # 401 sign_in_required
def test_order_requires_phone_and_address(...): ...             # 400 phone_required / address_required
def test_grab_order_fees_unset(...): ...                       # both fees None, total_to_collect == subtotal

def test_voucher_gives_free_standard_delivery(...):
    # voucher min_spend 0 → delivery_fee_charged 0.0, delivery_fee_driver 2.0, usage_count 1, one VoucherRedemption
    ...

def test_voucher_rejected_for_grab(...):
    # 400 voucher_standard_only, usage_count unchanged, no Order
    ...

def test_voucher_redeemed_once(...):
    # same customer posts twice with same voucher+phone → first 201, second 400 voucher_already_used; usage_count == 1
    ...

def test_order_saved_when_telegram_fails(client, login_as, make_customer, make_product, monkeypatch):
    import services.telegram_bot as tb
    monkeypatch.setattr(tb, "notify_admin_new_order", lambda o: "timeout")
    monkeypatch.setattr(tb, "notify_customer_status", lambda o: None)
    ...
    assert r.status_code == 201 and Order.query.one().notify_error == "timeout"

def test_use_voucher_endpoint_removed(client):
    assert client.post("/api/use_voucher", json={}).status_code in (404, 405)
```

`tests/test_vouchers.py` covers `check_voucher` for each error code, and `normalize_phone("+855 12 345 678") == "012345678"`.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_orders_create.py tests/test_vouchers.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `services/vouchers.py`, then `services/orders.py`. Add `notify_admin_new_order` and `notify_customer_status` stubs that return `None` to `services/telegram_bot.py`. Add the route. Refactor `home.py` voucher validation to use `check_voucher`.
- [ ] **Step 4: Run** the full suite: `.venv/Scripts/python -m pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with `git commit -m "feat(orders): create orders server-side with DB pricing and atomic voucher redemption"`

---

### Task 6: Order lifecycle, profit, needs-a-look, summaries, bot messages

**Files:**
- Modify: `services/orders.py`, `services/telegram_bot.py`, `translations.py` (status labels and bot copy)
- Create: `tests/test_orders_lifecycle.py`, `tests/test_telegram_bot.py`

**Interfaces:**
- Produces in `services/orders.py`:
  - `NEXT_STATUS = {"new": "confirmed", "confirmed": "packed", "packed": "delivering", "delivering": "delivered"}`
  - `KH_TZ = timezone(timedelta(hours=7))`
  - `advance_status(order, to_status: str, now: datetime | None = None) -> None`
    - Allowed moves: `NEXT_STATUS[order.status] == to_status`, or `to_status == "cancelled"` from any non-cancelled status.
    - Anything else raises `OrderError("invalid_transition")`.
    - It writes an `OrderStatusLog`, sets `status_changed_at`, commits, then calls `telegram_bot.notify_customer_status(order)`, storing any error in `notify_error`.
    - Cancelling a `delivered` order is allowed here; the UI confirms it.
  - `set_grab_fee(order, fee: float) -> None`. It raises `invalid_fee` if the order isn't Grab or the fee is < 0, and sets `delivery_fee_driver = delivery_fee_charged = round(fee, 2)` and `total_to_collect = subtotal + fee`.
  - `set_cash_received(order, received: bool, now=None) -> None`. It raises `invalid_transition` unless the status is `delivered`.
  - `order_profit(order) -> float`: `round(subtotal - total_cost - max(0, (delivery_fee_driver or 0) - (delivery_fee_charged or 0)), 2)`
  - `driver_payback(order) -> float`: `round(total_to_collect - (delivery_fee_driver or 0), 2)`
  - `needs_attention(order, now: datetime | None = None) -> list[str]` returns reason codes:
    - `missing_cost`: any item with `unit_cost is None`
    - `cash_overdue`: delivered, `not cash_received`, and `status_changed_at` older than 24h
    - `stuck_new`: `new`, and `created_at` older than 3h
    - `stuck_delivering`: `delivering`, and the KH local time now is ≥ 21:00 on or after the KH date of `status_changed_at`
    - `grab_fee_missing`: Grab, delivered, and `delivery_fee_driver is None`
    - Cancelled orders always return `[]`.
  - `range_bounds(range_key: str, now=None) -> tuple[datetime, datetime]` returns naive-UTC `[start, end)` for `today`, `7d` (today plus the previous 6 days) and `month` (KH calendar month).
  - `summary(range_key: str, now=None) -> dict` returns `{"orders": int, "sales": float, "profit": float, "cash_with_drivers": float}`.
    - The first three exclude cancelled orders and are filtered by `created_at` in range.
    - `cash_with_drivers` is the sum of `driver_payback` over **all** delivered, not-cash-received orders, regardless of range.
- Produces in `services/telegram_bot.py`:
  - `send_message(chat_id: str | int, text: str, reply_markup: dict | None = None) -> str | None`. It POSTs to `https://api.telegram.org/bot{token}/sendMessage` with `parse_mode="HTML"` and `timeout=5`, and returns `None` on `ok`, else a short error string. It catches every exception. With no token it returns `"no_bot_token"`.
  - `notify_admin_new_order(order) -> str | None`. It sends to `Setting.get_val("telegram_admin_chat_id")`, returning `"no_admin_chat"` if that is unset. The message includes `#id`, each item `name (options) ×qty`, the total, the delivery, phone, address and maps link, and a link to `https://angkorkey.store/admin/mobile/orders/<id>`. HTML-escape every customer-supplied value with `html.escape`.
  - `notify_customer_status(order) -> str | None`.
    - It returns `None` without sending when `not order.customer.can_message`.
    - Copy comes from `translations.py` keys `bot_status_<status>`, in `order.customer.lang` (falling back to `en`).
    - The message has an inline keyboard with "My orders" (`https://angkorkey.store/my-orders`) and "Chat with us" (`https://t.me/<telegram_username>`).
    - A Telegram 403 response sets `customer.can_message = False`.

- [ ] **Step 1: Write failing tests**

```python
def test_happy_path_transitions_and_logs(order_factory):
    o = order_factory(status="new")
    for s in ("confirmed", "packed", "delivering", "delivered"):
        advance_status(o, s)
    assert [l.to_status for l in o.status_logs] == ["new", "confirmed", "packed", "delivering", "delivered"]

def test_invalid_transitions(order_factory):
    o = order_factory(status="new")
    with pytest.raises(OrderError): advance_status(o, "packed")
    advance_status(o, "cancelled")
    with pytest.raises(OrderError): advance_status(o, "confirmed")

def test_profit_with_and_without_voucher(order_factory):
    assert order_profit(order_factory(subtotal=24, total_cost=14, fee_charged=2, fee_driver=2)) == 10.0
    assert order_profit(order_factory(subtotal=24, total_cost=14, fee_charged=0, fee_driver=2)) == 8.0

def test_grab_fee_sets_both_and_never_changes_profit(order_factory):
    o = order_factory(delivery_type="grab", subtotal=20, total_cost=12, fee_charged=None, fee_driver=None)
    set_grab_fee(o, 3.5)
    assert (o.delivery_fee_driver, o.delivery_fee_charged, o.total_to_collect, order_profit(o)) == (3.5, 3.5, 23.5, 8.0)

def test_needs_attention_rules(order_factory):
    now = datetime(2026, 10, 14, 15, 0)  # UTC == 22:00 KH
    assert "stuck_new" in needs_attention(order_factory(status="new", created_at=now - timedelta(hours=3, minutes=1)), now)
    assert needs_attention(order_factory(status="new", created_at=now - timedelta(hours=2)), now) == []
    assert "stuck_delivering" in needs_attention(order_factory(status="delivering", status_changed_at=datetime(2026, 10, 14, 3, 0)), now)
    assert "cash_overdue" in needs_attention(order_factory(status="delivered", status_changed_at=now - timedelta(hours=25)), now)
    assert "missing_cost" in needs_attention(order_factory(item_cost=None), now)
    assert needs_attention(order_factory(status="cancelled", item_cost=None), now) == []

def test_summary_uses_cambodia_day_and_excludes_cancelled(order_factory):
    now = datetime(2026, 10, 14, 15, 0)  # 22:00 KH on 14 Oct
    order_factory(created_at=datetime(2026, 10, 13, 18, 0), subtotal=10, total_cost=6)  # 01:00 KH 14 Oct → today
    order_factory(created_at=datetime(2026, 10, 13, 16, 0), subtotal=99, total_cost=1)  # 23:00 KH 13 Oct → not today
    order_factory(created_at=now, status="cancelled", subtotal=50, total_cost=10)
    assert summary("today", now) == {"orders": 1, "sales": 10.0, "profit": 4.0, "cash_with_drivers": 0.0}
```

`tests/test_telegram_bot.py`:
- Monkeypatch `urllib.request.urlopen`. Assert that `send_message` returns `None` on `{"ok": true}`, an error string on an exception or timeout, and `"no_bot_token"` without a token.
- Assert that the admin message HTML-escapes `<b>` in an address.
- Assert that `notify_customer_status` skips the send when `can_message` is False, and sets it to False on a 403.

Add an `order_factory` fixture to `conftest.py`. It builds and saves a full `Order` with one item and accepts the keyword overrides used above.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_orders_lifecycle.py tests/test_telegram_bot.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the functions above, and add the translation keys `bot_status_confirmed|packed|delivering|delivered|cancelled`, `bot_order_received`, `bot_btn_my_orders` and `bot_btn_chat` in `en` and `km`.
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Commit** with `git commit -m "feat(orders): lifecycle, profit, needs-a-look rules, summaries and bot notifications"`

---

### Task 7: Admin Orders screens, settings and nav

**Files:**
- Create: `blueprint/admin/orders.py`, `templates/backend/admin/mobile/order/list.html`, `templates/backend/admin/mobile/order/detail.html`, `tests/test_admin_orders.py`
- Modify:
  - `app.py`: register `admin_orders_bp`
  - `blueprint/admin/mobile.py`: `settings()` saves and renders `telegram_admin_chat_id` and `standard_delivery_fee`, validating the fee as a float ≥ 0 and flashing an error otherwise
  - `templates/backend/admin/mobile/settings.html`: two fields plus a "Send test message" button posting to `admin_orders.test_message`
  - `templates/backend/admin/mobile/mobile_master.html`: an Orders tab with a new-order badge in the bottom nav, before Settings

**Interfaces:**
- Consumes: everything in Task 6.
- Produces `admin_orders_bp = Blueprint("admin_orders", __name__, url_prefix="/admin/mobile/orders")`. These routes are already protected by `app.before_request`.

| Route | Behaviour |
|---|---|
| `GET /` (`admin_orders.order_list`) | Query args: `range` (`today` default, `7d`, `month`) and `tab` (`attention`, `new`, `confirmed`, `packed`, `delivering`, `done`, `cancelled`; default `new`). `done` means `delivered`. Shows the summary cards, tab counts and order cards, with 30 per page via `?page=`. |
| `GET /<int:order_id>` (`admin_orders.order_detail`) | Items with unit price, cost and quantity; contact details with a maps link; status history; profit; `needs_attention` reasons; `notify_error` as a warning |
| `POST /<int:order_id>/advance` | form `to_status`, flash on `OrderError`, redirect back (`request.referrer` or the list) |
| `POST /<int:order_id>/grab-fee` | form `fee` |
| `POST /<int:order_id>/cash` | form `received` = `"1"`/`"0"` |
| `POST /<int:order_id>/note` | form `admin_note` |
| `POST /test-message` (`admin_orders.test_message`) | sends "✅ Angkorkey test message" to the admin chat, flashes success or the returned error, redirects to `mobile.settings` |

- Produces `admin_orders_bp.app_context_processor` → `{"new_order_count": int}`, computed only when `request.path.startswith("/admin/")`, else `0`.
- Card content follows the approved mockup `.superpowers/brainstorm/1706-1791960356/content/admin-today-v2.html`:
  - `#id · customer.display_name`, the total, the item count, delivery, the KH-local time and the profit
  - buttons: next stage (label from `NEXT_STATUS`), 💬 Telegram (`https://t.me/<username>` if a username is set, else `tg://user?id=<telegram_id>`), and Cancel with a JS `confirm()`, using different wording when the order is delivered
- The attention tab lists non-cancelled orders where `needs_attention` is non-empty, scanning the newest 500. Reason codes map to readable labels in English (the admin side is English).
- Admin forms are plain HTML forms. Include a CSRF token if the existing mobile templates do; otherwise match their pattern.

- [ ] **Step 1: Write failing tests**

```python
def test_orders_requires_admin(client):
    assert client.get("/admin/mobile/orders/").status_code == 302

def test_list_shows_summary_and_cards(admin_client, order_factory):
    order_factory(status="new", subtotal=42, total_cost=33)
    html = admin_client.get("/admin/mobile/orders/?range=today&tab=new").get_data(as_text=True)
    assert "$42.00" in html and "$9.00" in html and "Confirm" in html

def test_advance_and_invalid_transition(admin_client, order_factory):
    o = order_factory(status="new")
    admin_client.post(f"/admin/mobile/orders/{o.id}/advance", data={"to_status": "confirmed"})
    assert db.session.get(Order, o.id).status == "confirmed"
    r = admin_client.post(f"/admin/mobile/orders/{o.id}/advance", data={"to_status": "delivered"}, follow_redirects=True)
    assert db.session.get(Order, o.id).status == "confirmed"

def test_attention_tab_lists_missing_cost(admin_client, order_factory):
    order_factory(item_cost=None)
    assert "no cost price" in admin_client.get("/admin/mobile/orders/?tab=attention").get_data(as_text=True).lower()

def test_grab_fee_and_cash(admin_client, order_factory): ...   # posts set values; cash rejected unless delivered
def test_settings_saves_admin_chat_and_fee(admin_client): ...  # Setting values persisted; "-1" fee rejected
def test_new_order_badge_in_nav(admin_client, order_factory): ... # 2 new orders → badge "2" on dashboard page
```

Add an `admin_client` fixture to `conftest.py` that creates a `User` and sets `session["user_id"]`.

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_admin_orders.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the blueprint, templates, settings changes and nav tab. Templates extend `backend/admin/mobile/mobile_master.html` and reuse its `keyz-*` classes.
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Check it by hand.**
  1. Run `DATABASE_URL=sqlite:///$(pwd)/instance/dev.db REDIS_URL=redis://127.0.0.1:1 .venv/Scripts/flask --app app run`.
  2. Create an admin with `flask create-admin`, using the same env.
  3. Insert two orders with a short Python snippet that uses `order_factory`-like code.
  4. Open `/admin/mobile/orders/` at phone width and confirm the layout matches the mockup.
  5. Add `instance/dev.db` to `.gitignore` if it isn't covered already.
- [ ] **Step 6: Commit** with `git commit -m "feat(admin): mobile orders screen with summary, stages, needs-a-look and settings"`

---

### Task 8: Storefront: sign-in, checkout, My orders, cost-leak fix

**Files:**
- Create: `static/js/telegram-auth.js`, `templates/frontend/pages/my_orders.html`, `templates/frontend/pages/my_order_detail.html`, `templates/frontend/pages/sign_in.html` (a partial included by `cart.html` and `my_orders.html`), `tests/test_storefront.py`
- Modify:
  - `templates/frontend/main.html`: load `https://telegram.org/js/telegram-web-app.js` and `static/js/telegram-auth.js` before `{% block content %}` scripts run
  - `templates/frontend/layouts/header.html`: a static account icon link to `/my-orders` next to the cart link, with `aria-label` from `_('my_orders')`
  - `templates/frontend/pages/cart.html`: the confirm handler
  - `blueprint/customer.py`: `/my-orders`, `/my-orders/<id>`
  - `blueprint/home.py`: remove `"cost": variant.cost` from `variants_data` (~line 196)
  - `translations.py`

**Interfaces:**
- Consumes: `/auth/telegram/webapp`, `/auth/telegram/widget`, `/api/me`, `/api/me/write-access` (Task 4), and `/api/orders` (Task 5).
- Produces a global `window.AngkorAuth` from `static/js/telegram-auth.js`:
  - `isMiniApp() -> bool`: true when `window.Telegram?.WebApp?.initData` is non-empty.
  - `ensureSignedIn() -> Promise<bool>`. In the Mini App it POSTs `initData` to `/auth/telegram/webapp` once per page load and memoises the result. In a browser it resolves to the `/api/me` `signed_in` value.
  - `requestWriteAccessIfNeeded(me) -> Promise<void>`. In the Mini App, when `me.can_message` is false, it calls `Telegram.WebApp.requestWriteAccess(cb)` and POSTs the result to `/api/me/write-access`.
  - `onWidgetAuth(user) -> void`. This is the Login Widget `data-onauth` callback: it POSTs to `/auth/telegram/widget`, then reloads.
  - On load in the Mini App it calls `Telegram.WebApp.ready()` and then `ensureSignedIn()` silently. That is the whole silent sign-in.
- `sign_in.html` renders the Login Widget script tag with `data-telegram-login="{{ bot_username }}"`, `data-size="large"`, `data-request-access="write"` and `data-onauth="AngkorAuth.onWidgetAuth(user)"`.
  - Add a `bot_username` setting with default `angkorkeywebsite_bot`, read via `Setting.get_val`, and inject it through `customer_bp.app_context_processor`. This is not per-customer, so it is safe in cached pages.
  - The widget is hidden when `isMiniApp()`.
- Cart confirm handler, replacing lines ~1058–1096:
  1. Validate as today.
  2. `await AngkorAuth.ensureSignedIn()`. If false, show the sign-in partial inside the checkout modal and stop.
  3. `requestWriteAccessIfNeeded`.
  4. POST `{items: cart.map(i => ({variantId: i.variantId, quantity: i.quantity})), delivery_type, phone, address, maps_link, voucher_code}` to `/api/orders`, with the confirm button disabled while it is in flight.
  5. On 201: clear the cart and the `localStorage` voucher list entry for the code, then `location.href = data.url`.
  6. On an error: show `_('err_' + code)`, falling back to `_('err_order_failed')`, and re-enable the button.
  - Remove the `/api/use_voucher` call and `window.open(t.me...)`. Keep `buildTelegramMessage` only if still referenced, otherwise delete it.
  - The voucher check passes `&delivery=` and is re-validated when the delivery option changes.
  - Prefill phone, address and maps from `/api/me` when signed in, with `localStorage` as the fallback.
- `GET /my-orders`: signed out, it renders `my_orders.html` with the sign-in partial (the Mini App JS signs in and reloads). Signed in, it lists the customer's orders newest first, 20 per page, with number, KH-local date, total and a translated status badge.
- `GET /my-orders/<id>`: 404 unless the order belongs to `current_customer()`. With `?new=1` it shows a confirmation banner (`_('order_placed')` and the order number) and a "Chat with us" link to `https://t.me/{{ telegram_username }}`.
- Grab orders whose fee isn't set yet show the total as `subtotal + _('grab_fee_pending')` ("+ Grab fee"), in both My orders and the admin card.
- Never show cost or profit in customer templates.
- Desktop admin pages are out of scope for this plan. The spec makes them optional, and the mobile panel is the priority.

- [ ] **Step 1: Write failing tests**

```python
def test_cached_home_does_not_leak_customer(client, login_as, make_customer, app):
    login_as(make_customer(first_name="Zzyzx"))
    assert "Zzyzx" not in client.get("/").get_data(as_text=True)
    client.delete_cookie("ak_customer")
    assert "Zzyzx" not in client.get("/").get_data(as_text=True)

def test_product_page_hides_cost(client, make_product):
    p, v = make_product(with_variant=True, variant_cost=7.77)
    html = client.get(f"/product_detail/{p.id}").get_data(as_text=True)
    assert '"cost"' not in html and "7.77" not in html

def test_my_orders_lists_only_own(client, login_as, make_customer, order_factory):
    me, other = make_customer(telegram_id=1), make_customer(telegram_id=2)
    mine, theirs = order_factory(customer=me), order_factory(customer=other)
    login_as(me)
    html = client.get("/my-orders").get_data(as_text=True)
    assert f"#{mine.id}" in html and f"#{theirs.id}" not in html
    assert client.get(f"/my-orders/{theirs.id}").status_code == 404

def test_my_orders_signed_out_shows_sign_in(client):
    html = client.get("/my-orders").get_data(as_text=True)
    assert "telegram-widget.js" in html and 'data-request-access="write"' in html

def test_order_detail_never_shows_cost(client, login_as, make_customer, order_factory):
    me = make_customer(); o = order_factory(customer=me, item_cost=3.33); login_as(me)
    assert "3.33" not in client.get(f"/my-orders/{o.id}?new=1").get_data(as_text=True)

def test_every_new_translation_key_has_km(app):
    from translations import TRANSLATIONS
    assert set(TRANSLATIONS["en"]) <= set(TRANSLATIONS["km"])
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_storefront.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** everything above. Add the translation keys:
  - `my_orders`, `order_placed`, `order_number`, `chat_with_us`, `sign_in_with_telegram`, `sign_in_to_order`
  - `status_new|confirmed|packed|delivering|delivered|cancelled`
  - `err_sign_in_required`, `err_invalid_items`, `err_phone_required`, `err_address_required`, `err_voucher_standard_only`, `err_voucher_already_used`, `err_order_failed`, and the other `err_voucher_*` codes from Task 5
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Check the browser flow by hand** with the Task 7 dev server.
  1. Add to cart and check out while signed out. The sign-in widget appears.
  2. With no bot domain set for localhost, the widget won't authenticate. Sign in through a test-only shortcut instead: in a Python shell, issue the cookie by calling `issue_customer_cookie` on a response, or temporarily use `login_as` logic. **Do not commit a bypass.**
  3. Place an order. It redirects to `/my-orders/<id>?new=1` and appears in `/admin/mobile/orders/`.
  4. The real Mini App check happens after deploy (see Rollout).
- [ ] **Step 6: Commit** with `git commit -m "feat(storefront): Telegram sign-in at checkout, server-side orders, My orders, hide cost"`

---

## Rollout (after the branch is reviewed; the owner runs these, not the executor)

1. Merge into `main`. This also triggers the cPanel workflow, so the owner should decide whether that host is still used.
2. On EC2:
   1. `cp instance/app.db instance/app.db.bak-$(date +%F)`
   2. `git fetch origin main && git reset --hard origin/main`
   3. `.venv/bin/flask --app app db upgrade`
   4. `sudo systemctl restart angkorkey`
3. In BotFather, run `/setdomain` → `angkorkey.store`, and check that the Mini App URL is `https://angkorkey.store`.
4. Press Start on the bot. In admin Settings, set your admin chat ID, then press "Send test message".
5. Smoke test in Telegram:
   1. Open the channel link and place an order. There should be no login prompt, and you should be asked once to allow messages.
   2. Check that the admin gets a bot message and the order appears in Orders.
   3. Advance the order and check that the customer gets status messages.

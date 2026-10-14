# Telegram Login & Order Management — Design

Date: 2026-10-14
Status: draft for owner review

## Why

Angkorkey customers check out as guests. The cart lives only in the browser
(`localStorage`), and "Confirm & Order via Telegram" opens a chat with the
shop's Telegram account (`@Angkorkey_Store`) with the order typed out as a
message. Nothing is saved on the server.

The owner runs the shop alone. Every order is copied down by hand, and working
out profit is hard. They want to **manage orders** in one place and have
something that stays **easy to scale** as volume grows.

Most customers reach the shop from the owner's Telegram channel, through the
bot's Mini App link `https://t.me/angkorkeywebsite_bot/angkorkey`. That means
Telegram can tell the site who the customer is, with no login form.

## What success looks like

- Every order is saved on the site and tied to a real Telegram account.
- The owner opens the admin panel and sees at a glance: today's order count,
  sales, profit, cash still with drivers, and anything that needs a look.
- Moving an order through its stages takes one tap, and the customer is told
  automatically on Telegram.
- No notebook. Profit for a day, week or month is one screen.

## Goals

1. **Telegram login for customers.**
   - Inside the Mini App: sign in automatically from Telegram's launch data.
     No button, no form.
   - In a normal browser: a "Log in with Telegram" button (Telegram Login
     Widget) using the same bot.
   - A login is required only at checkout. Browsing and the cart stay open to
     everyone.
2. **Orders saved on the server** with items, prices, costs, delivery, voucher,
   contact details and status.
3. **Admin order management** (owner only), on the existing mobile admin
   panel.
4. **Profit reporting** for today, the last 7 days and this month.
5. **Telegram notifications** sent by the bot: to the owner when a new order
   arrives, and to the customer on each status change.
6. **"My orders" page** for customers: their past orders and the current
   status of each.

## Non-goals

- Online payment. Orders stay cash on delivery.
- Stock tracking or inventory counts. The existing stock status field is
  untouched.
- Staff accounts or roles. The owner is the only admin.
- Changing how admins log in (username/password at the existing URL).
- Phone/email login or any login provider other than Telegram.
- Delivery-company integration (Grab API, etc.).
- Rebuilding the storefront design. New screens follow the existing
  Tailwind/zinc style.

## Anti-goals (would count as failure even if it "works")

- Customers in the Mini App seeing a login screen or being asked for a
  password.
- Order totals or profit computed from prices sent by the browser. The server
  must recompute every price from the database.
- Customers seeing cost prices or profit anywhere.
- Ordering getting slower or more steps than today for a Mini App user.
- A failed Telegram notification blocking or losing an order.
- Breaking existing guest browsing, the cart, vouchers or the admin product
  tools.

## Current state (facts from the code)

- Flask + Blueprints, SQLAlchemy on SQLite (`instance/app.db`), Flask-Migrate
  (Alembic), Flask-Session (Redis in production, filesystem fallback),
  Flask-Limiter, Jinja2 + Tailwind.
- `User` model (`models/User.py`) holds **admins only** (username, password,
  phone, image). Admin auth is `session['user_id']`, and `app.before_request`
  guards `/admin/*`.
- Cart items in `localStorage['angkorkey_cart']`: `{variantId, productId,
  name, price, quantity, optionsText, ...}`. `variantId` is either a
  `ProductVariant.id` or the string `base_<productId>` for products without
  variants.
- Prices: `ProductVariant.price` / `Product.price`. Costs:
  `ProductVariant.cost` / `Product.cost` (nullable). `Promotion` is banners
  only and does not change prices.
- Delivery options at checkout: **Standard $2.00** and **Grab** (fee
  "confirm later").
- Vouchers (`models/Voucher.py`): code, `min_spend`, `usage_limit`,
  `usage_count`. A valid voucher means **free standard delivery**. Redemptions
  are tracked per normalised phone number in `VoucherRedemption`.
  Validation is `/api/validate_voucher` and redemption is `/api/use_voucher`,
  both called from the browser.
- Settings (`models/Setting.py`, key/value): `telegram_username`,
  `telegram_bot_token`, `telegram_chat_id` (the channel the admin posts
  products to), `telegram_caption_template`, phones, social links.
- The bot `@angkorkeywebsite_bot` already exists, with the Mini App short
  name `angkorkey`.
- Checkout collects phone, address and an optional Google Maps link. These
  are saved to `localStorage` for next time.
- **Live server (checked 2026-10-14):** EC2 Ubuntu, `/var/www/angkorkey`.
  - systemd `angkorkey.service` runs
    `gunicorn --workers 3 --threads 4 --bind 127.0.0.1:5000 app:app`
    behind nginx.
  - `redis-server` is running, so production uses Redis sessions, Redis rate
    limits and the Redis cache.
  - Outbound HTTPS to `api.telegram.org` works.
  - The EC2 copy is updated by hand: `git fetch origin main && git reset
    --hard origin/main`, then restart the service.
  - Public domain: `https://angkorkey.store` (from `blueprint/seo.py`).
- **Separate deploy target:** `.github/workflows/deploy.yml` ("Deploy to
  cPanel") runs on every push to `main` and `git pull`s into a cPanel host
  (`passenger_wsgi.py` exists for it). It does not deploy the EC2 server.
- **Live DB migration state:**
  - `alembic_version` is `d0f67f256061` (head).
  - The DB also has `voucher`, `voucher_redemption` and `telegram_schedule`
    tables that no migration created; they came from `db.create_all()`, which
    `app.py` runs on every startup.
- **Tests:** `test_crud.py` runs against the real `instance/app.db`.

## How it works

### Customer identity

A new `Customer` model, separate from the admin `User` table:

| field | notes |
|---|---|
| id | PK |
| telegram_id | BigInteger, unique, indexed |
| first_name, last_name, username | from Telegram, refreshed on each login |
| photo_url | optional |
| phone | last phone used at checkout (prefill) |
| address, maps_link | last used (prefill) |
| can_message | bool. True once the user has allowed the bot to message them |
| created_at, last_login_at | |

**Customer login lasts 30 days. It does not use the admin session.**

The admin session stays as it is (30-minute lifetime). Customer identity
lives in its own signed cookie:
- name `ak_customer`
- value signed with `itsdangerous` using `SECRET_KEY`, carrying
  `customer_id` and the issue time
- `HttpOnly`, `Secure` in production, `SameSite=Lax`
- valid for 30 days, refreshed on use

So a customer login never grants `/admin` access, logging out of one doesn't
affect the other, and a browser customer isn't logged out after 30 idle
minutes. Mini App users are also signed in again silently on each launch.
*(Designer's call.)*

**Mini App sign-in.** The page loads `telegram-web-app.js`. If
`Telegram.WebApp.initData` is present, the page POSTs it to
`/auth/telegram/webapp`. The server validates it using Telegram's documented
Mini App scheme:
- `secret = HMAC_SHA256(key="WebAppData", msg=bot_token)`
- compare against `hash`
- reject if `auth_date` is older than 24h

On success the server upserts the `Customer` and sets the session. This
happens silently on page load, so by checkout the customer is already signed
in. If `initData.user.allows_write_to_pm` is false, checkout calls
`Telegram.WebApp.requestWriteAccess()` once, so the bot can send status
updates. The result is stored in `can_message`.

**Browser sign-in.** At checkout, a guest who isn't in the Mini App sees the
Telegram Login Widget with `data-request-access="write"`. The widget posts
its signed payload to `/auth/telegram/widget`. The server validates it with
Telegram's Login Widget scheme:
- `secret = SHA256(bot_token)`
- check the HMAC of the sorted `key=value` lines
- reject if `auth_date` is older than 24h

On success it upserts the customer and returns to checkout with the cart
intact (the cart is in `localStorage`, so it survives the redirect).

One-time setup the owner must do in BotFather: `/setdomain` for the bot to
the site's domain, needed for the Login Widget. The Mini App URL must point
at the same HTTPS domain.

The bot token stays where it is today (`Setting.telegram_bot_token`). An
environment variable `TELEGRAM_BOT_TOKEN`, if set, takes precedence.

### Placing an order

A new endpoint `POST /api/orders` requires a signed-in customer and accepts:
- items: `variantId` + quantity only
- delivery type
- phone, address, maps link
- voucher code

The server, in one transaction:
1. Loads each product/variant. Rejects inactive or missing ones. Takes the
   **price and cost from the database**, never from the request.
2. Validates the voucher with the same rules as today. On success it records
   the redemption and increments usage in the same transaction. This replaces
   the browser calling `/api/use_voucher` separately.
   - **Vouchers apply to Standard delivery only.** A voucher sent with Grab is
     rejected with a clear message, and nothing is redeemed. The checkout UI
     shows this when Grab is selected.
   - Today a voucher used with Grab is still redeemed and gives no benefit.
     That is a bug, and this fixes it. *(Designer's call.)*
3. Computes:
   - subtotal
   - delivery fee: Standard $2 (from a setting, default 2.00), $0 for the
     customer if a voucher applies
   - Grab fee: left empty, filled in later by the admin
4. Saves the `Order` and `OrderItem`s. Status is `new`.
5. Updates the customer's saved phone/address.
6. After commit, sends Telegram notifications (see below). Failures are
   logged and never undo the order.

The response is the order number. The cart is cleared, and the customer sees
a confirmation screen with the order number and a link to "My orders".

**The chat with the shop no longer opens automatically.** The bot confirms
the order to the customer in Telegram, and the confirmation includes a
"Chat with us" link (`t.me/<telegram_username>`), plus the same link on the
confirmation screen. *(Owner's call delegated to the designer: the order no
longer depends on the customer pressing send in a chat.)*

Rate limit: `POST /api/orders` at 10/minute per customer.

### Data model

**Order**

| field | notes |
|---|---|
| id | PK. Shown as `#<id>` |
| customer_id | FK → Customer |
| status | `new`, `confirmed`, `packed`, `delivering`, `delivered`, `cancelled` |
| delivery_type | `standard` / `grab` |
| delivery_fee_charged | what the customer pays for delivery (0 with voucher, null for Grab until set) |
| delivery_fee_driver | what the driver keeps (Standard: 2.00 snapshot; Grab: set by admin) |
| voucher_code | nullable, snapshot of the code |
| subtotal | sum of item price × qty |
| total_cost | sum of item cost × qty (null-cost items counted as 0 and flagged) |
| total_to_collect | subtotal + delivery_fee_charged (what the driver collects from the customer). While a Grab fee is unset, it equals the subtotal, and the order shows "+ Grab fee" |
| phone, address, maps_link | contact snapshot from checkout |
| admin_note | admin-only free text. Customers don't add notes at checkout; they use the chat |
| cash_received | bool, default false |
| cash_received_at | datetime |
| created_at, updated_at, status_changed_at | |

**OrderItem**

| field | notes |
|---|---|
| order_id, product_id, variant_id (nullable) | |
| name, options_text | snapshot at order time |
| unit_price, unit_cost (nullable), quantity | snapshot at order time |

**OrderStatusLog**: order_id, from_status, to_status, created_at. Used as an
audit trail, and to compute "stuck" orders.

Snapshots mean later edits to a product's price, cost or name never change
past orders or past profit.

### Profit

Per order:

```
profit = subtotal − total_cost − delivery_subsidy
delivery_subsidy = delivery_fee_driver − delivery_fee_charged   (≥ 0)
```

**Grab:** the customer pays the Grab fee in full. When the admin enters the
Grab driver fee, the system sets `delivery_fee_charged` to the same value, so
Grab never affects profit. *(Designer's call, following the owner's "delivery
fee is what the customer pays".)*

Without a voucher, the customer pays the driver's fee, so the subsidy is 0 and
delivery doesn't touch profit (as the owner said). With a voucher, the shop
covers the driver's $2, so profit drops by $2. *(Designer's call: the owner
said "delivery fee is what the customer pays", and a free-delivery voucher
shifts that cost to the shop.)*

Reports count orders by `created_at`, in the **Asia/Phnom_Penh** timezone,
excluding `cancelled`. Sales = sum of subtotal. Profit = sum of profit.

### Order lifecycle

```
new → confirmed → packed → delivering → delivered
  └──────────────┴─────────┴────────────→ cancelled (from any stage before delivered)
```

- Each card has one primary "next stage" button.
- "Mark cash received" is available on `delivered` orders.
- Admin can edit on an order:
  - the Grab driver fee
  - the admin note
  - cash received
- A delivered order can be cancelled only with a confirmation (for returns).
  No other backward moves. *(Designer's call.)*
- Cancelling an order does **not** give the voucher back. *(Designer's call:
  keeps it simple. Admin can reset a voucher in the existing voucher screen.)*

### Admin: Orders screen (mobile admin panel)

Approved mockup direction: "numbers on top, orders by stage below"
(`.superpowers/brainstorm/.../admin-today-v2.html`).

- **Top:** order count, sales and profit for the selected range (Today /
  7 days / This month), and "Cash still with drivers": the sum of
  `total_to_collect − delivery_fee_driver` (what the driver will hand back)
  for orders that are delivered but not cash received.
- **Tabs:** Needs a look · New · Confirmed · Packed · Delivering · Done ·
  Cancelled, each with a count.
- **Order card:**
  - number, customer name, total
  - item count, delivery type, time, profit
  - buttons: Next stage, 💬 Telegram (opens `t.me/<username>` or
    `tg://user?id=<telegram_id>`), Cancel
- **Order detail page:** items, contact details, maps link, status history,
  editable Grab fee, note and cash received.
- **"Needs a look"** flags:
  1. any item with no cost (profit would be wrong)
  2. delivered and cash not received after 24h
  3. `new` for more than 3 hours
  4. still `delivering` after 21:00 the same day (deliveries arrive 1–4 pm)
  5. Grab order delivered with no driver fee set
- A new-order count badge on the admin nav.

The desktop admin gets the same pages through the existing layout, if it is
cheap to do so. The mobile panel is the priority.

### Telegram notifications

Sent with the bot token via `sendMessage` (the same HTTP approach as the
existing channel posting in `blueprint/admin/mobile.py`).

- **To the owner, on a new order:** order number, items, total, delivery,
  phone, address, maps link, plus a link to the admin order page. Sent to a
  new setting `telegram_admin_chat_id`, configured in Settings. The owner
  must `/start` the bot once. Settings shows a "Send test message" button.
- **To the customer, on each status change** (confirmed, packed, delivering,
  delivered, cancelled): a short message in their site language (en/km) with
  the order number and new status, and a link to "My orders". Sent only if
  `can_message` is true.

Sending runs after the database commit. A failure is logged and shown as a
small warning on the order in admin, and never raises to the user. Sending is
synchronous with a short timeout (5s). *(Designer's call: volume is low, so no
job queue is needed. Moving to a background queue later is a contained
change.)*

### Customer: "My orders"

- `/my-orders` lists the signed-in customer's orders (newest first): number,
  date, total, status badge.
- `/my-orders/<id>` shows the order detail. It must belong to the signed-in
  customer, otherwise 404.
- It's reachable from the header (account icon) and from the confirmation
  screen and bot messages.
- In en and km, using `translations.py`.

### Checkout changes (`templates/frontend/pages/cart.html`)

- Keep the existing steps and look.
- On confirm: if not signed in, show sign-in (auto in the Mini App, the widget
  in a browser), then submit to `/api/orders` instead of opening `t.me`.
- Prefill phone and address from the customer record when signed in, falling
  back to `localStorage`.
- The voucher check still uses `/api/validate_voucher` for instant feedback.
  The real redemption happens server-side in `/api/orders`.

### Security & privacy fixes bundled in

- **Stop sending variant `cost` to the storefront.** It is currently included
  in `variants_data` on the product page (`blueprint/home.py`), so customers
  can see cost prices.
- CSRF: the JSON endpoints use the existing Flask-WTF setup (send the token
  in a header), or are explicitly exempted with a same-origin check. The
  builder chooses, but the endpoints must not be open to cross-site posts.

## Constraints

- Python/Flask stack as-is. No new frameworks. Only small new dependencies if
  necessary (the HMAC checks need only the standard library).
- Schema changes go in an Alembic migration in `migrations/versions/`,
  revising from `d0f67f256061`.
- **The migration must be idempotent:** create each new table and index only
  if it doesn't exist. `app.py`'s `db.create_all()` runs at startup and may
  create the new tables before `flask db upgrade` runs.
- Don't remove `create_all()` in this project. *(Designer's call: changing
  how the live DB is bootstrapped is out of scope.)*
- Rollout order on EC2:
  1. back up `instance/app.db`
  2. pull
  3. `flask db upgrade`
  4. restart `angkorkey.service`
- Rate limits and sessions must work across gunicorn's 3 workers. Production
  has Redis, so the existing Limiter storage is fine. Customer auth uses a
  signed cookie, so it needs no shared store.
- Bilingual (en/km) for every customer-facing string.
- **Do not push to `main` during development.** That triggers the cPanel
  deploy. Work on a feature branch.
- **Tests must never touch `instance/app.db`.** New tests use a temporary
  SQLite database. `test_crud.py` should not be run as-is against the real
  database.
- Works inside the Telegram Mini App webview on iOS and Android, and in normal
  browsers.

## Left to the builder

- File layout: e.g. `models/Customer.py`, `models/Order.py`,
  `blueprint/orders.py`, `blueprint/admin/orders...`, `services/telegram.py`.
- Test framework (pytest is fine to add as a dev dependency) and test
  structure.
- Exact Tailwind markup, icons and wording within the existing style.
- How the Login Widget is presented (modal vs inline) at checkout.
- Pagination of the order list and My orders.
- Whether "Needs a look" thresholds live in code constants or settings.
- Moving the existing product-to-channel posting into the shared Telegram
  service, which is optional.

## Rollout notes for the owner

1. In BotFather: `/setdomain` → `angkorkey.store` (for browser login). Make
   sure the Mini App URL also points at `https://angkorkey.store`.
2. Open the bot and press Start (so it can message you).
3. In admin Settings: confirm the bot token, set your admin chat ID, and press
   "Send test message".
4. Fill in cost prices on any products that lack them. "Needs a look" will
   list orders affected.

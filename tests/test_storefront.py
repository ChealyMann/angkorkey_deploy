"""Storefront: sign-in partial, My orders, checkout wiring and the cost leak fix."""
import re

from extensions import db
from models import Setting

# Every page embeds all translations as JSON; strip it so assertions only see rendered content.
_TRANSLATIONS_JSON = re.compile(r'<script id="translations-json".*?</script>', re.S)


def html_of(response):
    return _TRANSLATIONS_JSON.sub("", response.get_data(as_text=True))


# ---- Brief tests -----------------------------------------------------------

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
    # Deviation from the brief: anchored as ">#id<" because the footer's "#229ED9" contains "#2".
    assert f">#{mine.id}<" in html and f">#{theirs.id}<" not in html
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


# ---- Sign-in partial ---------------------------------------------------------

def test_sign_in_widget_uses_default_bot_username(client):
    html = html_of(client.get("/my-orders"))
    assert 'src="https://telegram.org/js/telegram-widget.js?22"' in html
    assert 'data-telegram-login="angkorkeywebsite_bot"' in html
    assert 'data-size="large"' in html
    assert 'data-onauth="AngkorAuth.onWidgetAuth(user)"' in html


def test_sign_in_widget_uses_bot_username_setting(client):
    db.session.add(Setting(key="bot_username", value="other_bot"))
    db.session.commit()
    assert 'data-telegram-login="other_bot"' in html_of(client.get("/my-orders"))


def test_bot_username_env_overrides_setting(client, monkeypatch):
    db.session.add(Setting(key="bot_username", value="other_bot"))
    db.session.commit()
    monkeypatch.setenv("BOT_USERNAME", "@angkorkey_dev_bot")
    assert 'data-telegram-login="angkorkey_dev_bot"' in html_of(client.get("/my-orders"))


def test_cart_page_includes_sign_in_partial(client):
    html = html_of(client.get("/cart"))
    assert "telegram-widget.js" in html and 'data-request-access="write"' in html


def test_layout_loads_telegram_scripts_before_content(client):
    html = html_of(client.get("/cart"))
    web_app = html.index('src="https://telegram.org/js/telegram-web-app.js"')
    auth = html.index("js/telegram-auth.js")
    content = html.index('id="content-main"')
    assert web_app < auth < content


def test_header_links_to_my_orders(client):
    html = html_of(client.get("/"))
    assert 'href="/my-orders"' in html and 'aria-label="My orders"' in html


# ---- My orders ----------------------------------------------------------------

def test_my_orders_signed_in_has_no_widget(client, login_as, make_customer, order_factory):
    me = make_customer()
    order_factory(customer=me)
    login_as(me)
    html = html_of(client.get("/my-orders"))
    assert "telegram-widget.js" not in html


def test_my_orders_newest_first_with_status_and_total(client, login_as, make_customer, order_factory):
    from datetime import datetime

    me = make_customer()
    older = order_factory(customer=me, status="delivered", created_at=datetime(2026, 10, 1, 3, 0))
    newer = order_factory(customer=me, status="packed", subtotal=25.0, fee_charged=2.0,
                          created_at=datetime(2026, 10, 13, 18, 30))
    login_as(me)
    html = html_of(client.get("/my-orders"))
    assert html.index(f">#{newer.id}<") < html.index(f">#{older.id}<")
    assert "Packed" in html and "Delivered" in html
    assert "$27.00" in html  # total to collect = subtotal + delivery fee
    assert "14/10/2026" in html  # 18:30 UTC on the 13th is the 14th in Cambodia


def test_my_orders_paginates_20_per_page(client, login_as, make_customer, order_factory):
    me = make_customer()
    orders = [order_factory(customer=me) for _ in range(21)]
    login_as(me)
    first = html_of(client.get("/my-orders"))
    assert f'href="/my-orders/{orders[-1].id}"' in first
    assert f'href="/my-orders/{orders[0].id}"' not in first
    second = html_of(client.get("/my-orders?page=2"))
    assert f'href="/my-orders/{orders[0].id}"' in second
    assert client.get("/my-orders?page=abc").status_code == 200


def test_my_orders_status_in_khmer(client, login_as, make_customer, order_factory):
    from translations import TRANSLATIONS

    me = make_customer()
    order_factory(customer=me, status="delivering")
    login_as(me)
    client.get("/set_lang/km")
    assert TRANSLATIONS["km"]["status_delivering"] in html_of(client.get("/my-orders"))


def test_grab_without_fee_shows_pending_total(client, login_as, make_customer, order_factory, admin_client):
    me = make_customer()
    order_factory(customer=me, delivery_type="grab", subtotal=15.0, fee_charged=None, fee_driver=None)
    login_as(me)
    html = html_of(client.get("/my-orders"))
    assert "$15.00" in html and "+ Grab fee" in html
    admin_html = html_of(admin_client.get("/admin/mobile/orders/?tab=new"))
    assert "+ Grab fee" in admin_html


def test_grab_with_fee_shows_full_total(client, login_as, make_customer, order_factory):
    me = make_customer()
    order_factory(customer=me, delivery_type="grab", subtotal=15.0, fee_charged=3.5, fee_driver=3.5)
    login_as(me)
    html = html_of(client.get("/my-orders"))
    assert "$18.50" in html and "+ Grab fee" not in html


def test_order_detail_shows_items_and_banner(client, login_as, make_customer, order_factory):
    db.session.add(Setting(key="telegram_username", value="Shop_Chat"))
    db.session.commit()
    me = make_customer()
    o = order_factory(customer=me)
    login_as(me)
    html = html_of(client.get(f"/my-orders/{o.id}?new=1"))
    assert "Order placed" in html and f">#{o.id}<" in html
    assert 'href="https://t.me/Shop_Chat"' in html
    assert "Test Item" in html and "$12.00" in html
    plain = html_of(client.get(f"/my-orders/{o.id}"))
    assert "Order placed" not in plain


def test_order_detail_signed_out_is_404(client, order_factory):
    o = order_factory()
    assert client.get(f"/my-orders/{o.id}").status_code == 404


def test_order_detail_missing_is_404(client, login_as, make_customer):
    login_as(make_customer())
    assert client.get("/my-orders/999").status_code == 404


# ---- Checkout wiring ---------------------------------------------------------

def test_cart_posts_to_orders_api_not_telegram_chat(client):
    html = html_of(client.get("/cart"))
    assert "/api/orders" in html
    assert "/api/use_voucher" not in html
    assert "window.open(" not in html and "tgUrl" not in html
    assert "buildTelegramMessage" not in html
    assert "&delivery=" in html


def test_order_error_codes_have_translations(app):
    from translations import TRANSLATIONS

    codes = [
        "sign_in_required", "invalid_items", "invalid_delivery", "phone_required", "address_required",
        "voucher_required", "voucher_invalid", "voucher_inactive", "voucher_limit_reached",
        "voucher_min_spend", "voucher_already_used", "voucher_standard_only", "order_failed",
        "auth_unavailable",
    ]
    for lang in ("en", "km"):
        for code in codes:
            assert TRANSLATIONS[lang].get("err_" + code), (lang, code)
        for status in ("new", "confirmed", "packed", "delivering", "delivered", "cancelled"):
            assert TRANSLATIONS[lang].get("status_" + status), (lang, status)


def test_telegram_auth_script_is_served(client):
    response = client.get("/static/js/telegram-auth.js")
    body = html_of(response)
    response.close()
    assert response.status_code == 200
    for name in ("isMiniApp", "ensureSignedIn", "requestWriteAccessIfNeeded", "onWidgetAuth"):
        assert name in body
    # A failed Mini App sign-in is not memoised: it falls back to /api/me and clears the memo.
    assert "webAppSignIn = null" in body and "/api/me" in body


def test_layout_busts_service_worker_cache_for_auth_script(client):
    # sw.js serves /static/ cache-first forever, so the script URL must change with the file.
    assert "js/telegram-auth.js?v=5" in html_of(client.get("/cart"))


def test_closing_checkout_clears_resume_flag(client):
    html = html_of(client.get("/cart"))
    hide = html[html.index("function hideCheckoutModal()"):]
    assert "sessionStorage.removeItem(RESUME_KEY)" in hide[:400]


def test_cart_shows_standard_delivery_fee_setting(client):
    from models import Setting

    Setting.set_val("standard_delivery_fee", "3.5")
    html = html_of(client.get("/cart"))
    assert "$3.50" in html and "$2.00" not in html
    assert "const STANDARD_FEE = 3.5;" in html


def test_cart_default_standard_delivery_fee(client):
    html = html_of(client.get("/cart"))
    assert "$2.00" in html and "const STANDARD_FEE = 2.0;" in html


def test_mini_app_checks_me_before_posting_init_data(client):
    response = client.get("/static/js/telegram-auth.js")
    body = html_of(response)
    response.close()
    ensure = body[body.index("function ensureSignedIn()"):]
    ensure = ensure[:ensure.index("return webAppSignIn;")]
    # /api/me first; initData is posted only when that says signed out.
    assert ensure.index("webAppSignIn = me()") < ensure.index("postInitData()")
    assert "data.signed_in === true ? true : postInitData()" in ensure


def test_declined_write_access_is_remembered(client):
    response = client.get("/static/js/telegram-auth.js")
    body = html_of(response)
    response.close()
    assert "'angkorkey_write_access_declined'" in body
    request_fn = body[body.index("function requestWriteAccessIfNeeded(data)"):]
    assert "writeAccessDeclined()" in request_fn[:400]
    assert "rememberWriteAccessAnswer(!!allowed)" in request_fn


# ---- No phone at checkout; Telegram login popup ---------------------------------

def test_cart_has_no_phone_field(client):
    html = html_of(client.get("/cart"))
    assert 'id="checkout-phone"' not in html
    assert "phone: phoneVal" not in html


def test_bot_id_meta_comes_from_token(client, bot_token):
    assert '<meta name="telegram-bot-id" content="123">' in client.get("/cart").get_data(as_text=True)


def test_bot_id_meta_empty_without_token(client):
    assert '<meta name="telegram-bot-id" content="">' in client.get("/cart").get_data(as_text=True)


def test_checkout_opens_telegram_login_popup(client):
    js = client.get("/static/js/telegram-auth.js").get_data(as_text=True)
    assert "Telegram.Login.auth" in js and "popupSignIn" in js and "knownSignedOut" in js
    cart = client.get("/cart").get_data(as_text=True)
    assert "auth.popupSignIn()" in cart


# ---- Customer log out -------------------------------------------------------------

def test_my_orders_shows_logout_when_signed_in(client, login_as, make_customer):
    login_as(make_customer(first_name="Sokha"))
    html = html_of(client.get("/my-orders"))
    assert 'id="customer-logout-btn"' in html and "Sokha" in html


def test_my_orders_has_no_logout_when_signed_out(client):
    assert 'id="customer-logout-btn"' not in html_of(client.get("/my-orders"))


def test_logout_signs_customer_out(client, login_as, make_customer):
    login_as(make_customer())
    assert client.get("/api/me").json["signed_in"] is True
    r = client.post("/auth/logout", headers={"Origin": "http://localhost"})
    assert r.status_code == 200
    assert client.get("/api/me").json == {"signed_in": False}
    assert 'id="customer-logout-btn"' not in html_of(client.get("/my-orders"))

import os
import json
from datetime import timedelta

import click
import redis
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, make_response
from werkzeug.security import generate_password_hash
from flask_migrate import Migrate
from flask_session import Session

from extensions import db, limiter, cache

# ------------------------------------------------------------
# Redis connection check
# ------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

def _redis_available():
    """Check if Redis server is reachable."""
    try:
        r = redis.from_url(REDIS_URL, socket_connect_timeout=1)
        r.ping()
        return True
    except (redis.ConnectionError, redis.TimeoutError, ConnectionRefusedError):
        return False

USE_REDIS = _redis_available()

from blueprint.admin.admin import admin_bp
from blueprint.admin.brand.brand import brand_bp
from blueprint.home import home_bp
from blueprint.auth import auth_bp
from blueprint.admin.product.product import product_bp
from blueprint.admin.category.category import category_bp
from blueprint.admin.user.user import user_bp
from blueprint.admin.promotion.promotion import promotion_bp
from blueprint.admin.voucher.voucher import voucher_bp
from blueprint.admin.mobile import mobile_bp
from blueprint.seo import seo_bp

from models import User, Category, Brand, Setting
from translations import TRANSLATIONS


app = Flask(__name__)


app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# ------------------------------------------------------------
# Base folders
# ------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(STATIC_DIR, "images")

os.makedirs(INSTANCE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-key-now"
)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(INSTANCE_DIR, "app.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)

# Upload folder
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR

# ------------------------------------------------------------
# Redis-backed services (with graceful fallback)
# ------------------------------------------------------------
if USE_REDIS:
    # Rate limiter → Redis DB 0
    app.config["RATELIMIT_STORAGE_URI"] = f"{REDIS_URL}/0"
    # Server-side sessions → Redis DB 1
    app.config["SESSION_TYPE"] = "redis"
    app.config["SESSION_PERMANENT"] = True
    app.config["SESSION_REDIS"] = redis.from_url(f"{REDIS_URL}/1")
    app.config["SESSION_KEY_PREFIX"] = "angkorkey:"
    # Caching → Redis DB 2
    app.config["CACHE_TYPE"] = "RedisCache"
    app.config["CACHE_REDIS_URL"] = f"{REDIS_URL}/2"
    app.config["CACHE_DEFAULT_TIMEOUT"] = 300  # 5 minutes
    app.config["CACHE_KEY_PREFIX"] = "angkorkey_cache:"
else:
    # Fallback for local development without Redis
    app.config["RATELIMIT_STORAGE_URI"] = "memory://"
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["CACHE_TYPE"] = "SimpleCache"
    app.config["CACHE_DEFAULT_TIMEOUT"] = 300

# ------------------------------------------------------------
# Initialize extensions
# ------------------------------------------------------------
db.init_app(app)
migrate = Migrate(app, db)
limiter.init_app(app)
Session(app)
cache.init_app(app)

# ------------------------------------------------------------
# Register blueprints
# ------------------------------------------------------------
app.register_blueprint(home_bp)
app.register_blueprint(product_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(category_bp)
app.register_blueprint(user_bp)
app.register_blueprint(promotion_bp)
app.register_blueprint(brand_bp)
app.register_blueprint(voucher_bp)
app.register_blueprint(mobile_bp)
app.register_blueprint(seo_bp)

# ------------------------------------------------------------
# Database Auto-Initialization for deploys (e.g. Render)
# ------------------------------------------------------------
with app.app_context():
    db.create_all()
    # Auto-create default admin if user table is empty
    if not User.query.first():
        default_admin = User(
            username="chealy",
            password=generate_password_hash("zxnmtt123789")
        )
        db.session.add(default_admin)
        db.session.commit()

# ------------------------------------------------------------
# App branding
# ------------------------------------------------------------
app.config["logo"] = "sql_logo.jpg"
app.config["title"] = "Angkorkey"
app.config["icon"] = "static/admin/assets/images/icon_logo.jpg"


@app.before_request
def before_request():
    url = request.path

    if url.startswith("/admin/"):
        if not session.get("user_id"):
            flash("Please Login", "danger")
            return redirect(url_for("auth.login"))

    return None


@app.after_request
def add_cache_control_headers(response):
    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type or "application/json" in content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    # ---------------------------------------------------------
    # Auto-invalidate page cache after admin write operations
    # Any successful POST/PUT/DELETE to /admin/* clears cache
    # so visitors always see the latest data immediately.
    # ---------------------------------------------------------
    if (
        request.path.startswith("/admin/")
        and request.method in ("POST", "PUT", "DELETE")
        and response.status_code in (200, 301, 302)
    ):
        cache.clear()

    return response


@app.route("/set_lang/<lang>")
def set_lang(lang):
    if lang in ["en", "km"]:
        session["lang"] = lang
        session.permanent = True
    return redirect(request.referrer or url_for("home.home"))


@app.context_processor
def inject_translations():
    lang = session.get("lang", "en")
    def get_text(key):
        return TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, key)
    return {
        "_": get_text, 
        "current_lang": lang,
        "translations_json": json.dumps(TRANSLATIONS)
    }


@app.route("/sw.js")
def service_worker():
    response = make_response(send_from_directory(app.static_folder, "js/sw.js"))
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/upload")
def upload_page():
    return render_template("upload.html")


@app.route("/loop")
def loop_marketplace():
    return render_template("loop_marketplace.html")


def _get_nav_data():
    """Fetch navigation data (categories, brands, settings). Cached for 5 min."""
    try:
        categories = Category.query.all()
        brands = Brand.query.filter_by(status="true").order_by(Brand.name.asc()).all()
        telegram_username = Setting.get_val('telegram_username', 'Angkorkey_Store')
        facebook_url = Setting.get_val('facebook_url', '')
        tiktok_url = Setting.get_val('tiktok_url', '')
        phone1 = Setting.get_val('phone1', '')
        phone2 = Setting.get_val('phone2', '')
    except Exception:
        categories = []
        brands = []
        telegram_username = 'Angkorkey_Store'
        facebook_url = ''
        tiktok_url = ''
        phone1 = ''
        phone2 = ''

    return {
        "categories": categories,
        "brands": brands,
        "telegram_username": telegram_username,
        "facebook_url": facebook_url,
        "tiktok_url": tiktok_url,
        "phone1": phone1,
        "phone2": phone2,
    }


@app.context_processor
def inject_nav_data():
    return _get_nav_data()


@app.cli.command("create-admin")
@click.argument("name")
@click.argument("password")
def create_user(name, password):
    """Creates a new user. Usage: flask create-admin <name> <password>"""
    hashed_pw = generate_password_hash(password)

    user = User(username=name, password=hashed_pw)

    db.session.add(user)
    db.session.commit()

    print(f"Successfully created user: {name}")


@app.errorhandler(404)
def page_not_found(error):
    return render_template("frontend/error/404.html"), 404


@app.errorhandler(429)
def too_many_requests(error):
    return render_template("frontend/error/429.html"), 429


if __name__ == "__main__":
    app.run(debug=True)
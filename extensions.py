from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache

db = SQLAlchemy()
limiter = Limiter(key_func=get_remote_address, default_limits=["60 per minute"])
cache = Cache()

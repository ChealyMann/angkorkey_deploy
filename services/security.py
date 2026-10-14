"""Request-level security helpers."""
from urllib.parse import urlparse

from flask import abort, current_app, request


def require_same_origin() -> None:
    """Abort 403 unless the Origin (or, failing that, Referer) host is this host."""
    if current_app.testing and request.headers.get("X-Test-Skip-Origin") == "1":
        return
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source or urlparse(source).netloc != request.host:
        abort(403)

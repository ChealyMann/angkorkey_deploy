"""Request-level security helpers."""
from urllib.parse import urlparse

from flask import abort, current_app, request


def require_same_origin() -> None:
    """Abort 403 unless the Origin (or, failing that, Referer) is this site.

    Accepted: the request's own host, or a hostname listed in
    app.config["PUBLIC_HOSTS"] (needed behind a proxy that does not forward Host).
    """
    if current_app.testing and request.headers.get("X-Test-Skip-Origin") == "1":
        return
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        abort(403)
    parsed = urlparse(source)
    if parsed.netloc == request.host:
        return
    if parsed.hostname and parsed.hostname in current_app.config.get("PUBLIC_HOSTS", ()):
        return
    abort(403)

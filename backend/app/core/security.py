"""Minimal signed session tokens for demo logins.

Token = "<user_id>.<expires_unix>.<hmac_sha256>". Stateless and tamper-proof; good enough for a
hackathon demo with seeded users. A production deployment would use a real identity provider.
"""

import hashlib
import hmac
import time

TOKEN_TTL_SECONDS = 12 * 3600


class InvalidTokenError(Exception):
    pass


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(user_id: str, secret: str, *, ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    payload = f"{user_id}.{int(time.time()) + ttl_seconds}"
    return f"{payload}.{_sign(payload, secret)}"


def verify_token(token: str, secret: str) -> str:
    """Return the user id, or raise InvalidTokenError."""
    try:
        user_id, expires, signature = token.rsplit(".", 2)
        payload = f"{user_id}.{expires}"
        expired = int(expires) < time.time()
    except ValueError as exc:
        raise InvalidTokenError("Malformed token") from exc
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        raise InvalidTokenError("Bad signature")
    if expired:
        raise InvalidTokenError("Token expired")
    return user_id

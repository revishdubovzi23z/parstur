import base64
import hashlib
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from settings import settings

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

AUTH_USER = settings.auth_user
AUTH_PASS = settings.auth_pass
AUTH_PASS_HASH = settings.auth_pass_hash
_auth_enabled = bool(AUTH_USER) and bool(AUTH_PASS or AUTH_PASS_HASH)

# Map token -> Unix-epoch expiry timestamp. We use a sliding 7-day TTL so a
# user who keeps using the app stays logged in indefinitely, but a token that
# was issued and never used past 7 days becomes invalid.
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_session_tokens: dict[str, float] = {}
_ws_tickets: dict[str, float] = {}  # {ticket: expiry_timestamp}


def _verify_password(plaintext: str) -> bool:
    """Return True if plaintext matches the configured password.

    Verification is constant-time. AUTH_PASS_HASH (pbkdf2_sha256) takes
    precedence; AUTH_PASS plaintext is used as a fallback for backward
    compatibility.
    """
    if AUTH_PASS_HASH:
        try:
            algo, iter_str, salt_b64, hash_b64 = AUTH_PASS_HASH.split("$", 3)
        except ValueError:
            return False
        if algo != "pbkdf2_sha256":
            return False
        try:
            iterations = int(iter_str)
        except ValueError:
            return False
        try:
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(hash_b64)
        except Exception:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(actual, expected)
    if AUTH_PASS:
        return secrets.compare_digest(plaintext.encode("utf-8"), AUTH_PASS.encode("utf-8"))
    return False


def _check_token(token: str) -> bool:
    """Return True if token is known and not expired; refresh sliding TTL."""
    expiry = _session_tokens.get(token)
    if expiry is None:
        return False
    now = time.time()
    if now > expiry:
        _session_tokens.pop(token, None)
        return False
    # Sliding refresh — every successful check pushes the expiry forward.
    _session_tokens[token] = now + SESSION_TTL_SECONDS
    return True


def _check_auth(request: Request) -> bool:
    if not _auth_enabled:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _check_token(auth[7:])
    return False


@router.post("/api/login")
@limiter.limit("5/minute")
async def login(request: Request):
    if not _auth_enabled:
        return {"token": "none", "auth_enabled": False}
    body = await request.json()
    user = body.get("username", "") or ""
    password = body.get("password", "") or ""
    # Constant-time on both username and password to avoid leaking whether
    # the username is correct via timing.
    user_ok = secrets.compare_digest(user.encode("utf-8"), AUTH_USER.encode("utf-8"))
    pass_ok = _verify_password(password)
    if user_ok and pass_ok:
        token = secrets.token_hex(32)
        _session_tokens[token] = time.time() + SESSION_TTL_SECONDS
        return {"token": token, "auth_enabled": True}
    raise HTTPException(status_code=401, detail="Invalid credentials")


@router.post("/api/logout")
async def logout(request: Request):
    """Invalidate the bearer token from the request, if any.

    Always returns 200 — even if the token was unknown, expired or missing —
    so callers can use this as a fire-and-forget on the way out.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        _session_tokens.pop(auth[7:], None)
    return {"ok": True}


@router.get("/api/auth_status")
async def auth_status():
    return {"auth_enabled": _auth_enabled}


@router.post("/api/ws/ticket")
async def get_ws_ticket(request: Request):
    """Generate a one-time short-lived ticket for WebSocket authentication.

    Used to avoid passing the long-lived session token in a query parameter
    which might be logged by reverse proxies.
    """
    ticket = secrets.token_hex(16)
    _ws_tickets[ticket] = time.time() + 30  # 30 seconds TTL
    return {"ticket": ticket}

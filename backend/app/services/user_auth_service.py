"""
user_auth_service.py
-----------------------
Real password hashing (bcrypt) and session-token issuance for the human
login system, kept separate from app.crypto.cryptocore which is the
ship-identity ECDSA/ECDH code used by app.routers.auth for the
ship-to-control-center handshake. Different problem, different module.
"""

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

SESSION_LIFETIME = timedelta(hours=12)
VALID_ROLES = ("control_station", "ship_captain")
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/legacy hash - fail closed, not open.
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + SESSION_LIFETIME


def is_expired(expires_at: datetime) -> bool:
    exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= exp


def validate_password_strength(password: str) -> str | None:
    """Returns an error message if the password is too weak, else None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if password.isdigit() or password.isalpha():
        return "Password must contain both letters and numbers"
    return None

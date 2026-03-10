import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import logging
from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from jose import jwt
from jose import JWTError

from cache_service.redis_client import exists as cache_exists, incr as cache_incr, expire as cache_expire, set_key, delete as cache_delete
from data_service.db import PostgresDB

import bcrypt

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-prod")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXP_MINUTES = int(os.getenv("JWT_EXP_MINUTES", "60"))

# lock config
LOCK_THRESHOLD = int(os.getenv("LOGIN_LOCK_THRESHOLD", "5"))
LOCK_WINDOW_SECONDS = int(os.getenv("LOGIN_LOCK_WINDOW_SECONDS", "900"))
LOCK_DURATION_SECONDS = int(os.getenv("LOGIN_LOCK_DURATION_SECONDS", "900"))

# cache operations are handled by `cache_service.redis_client`


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception as exc:
        logging.warning("Password verify failed: %s", exc)
        return False


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + (expires_delta or timedelta(minutes=JWT_EXP_MINUTES))
    payload = {"sub": str(subject), "iat": now, "exp": exp}
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token


def decode_access_token(token: str) -> Optional[str]:
    """Decode JWT and return subject (as string) or None on failure."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        sub = payload.get("sub")
        return str(sub) if sub is not None else None
    except JWTError as exc:
        logging.debug("JWT decode error: %s", exc)
        return None


def get_user_by_id(user_id: int):
    with PostgresDB.from_env() as db:
        row = db.fetchone("SELECT id, email, display_name, password_hash FROM sttt.users WHERE id = %s", (user_id,))
        return row


def get_user_by_email(email: str):
    with PostgresDB.from_env() as db:
        row = db.fetchone("SELECT id, email, display_name, password_hash FROM sttt.users WHERE email = %s", (email,))
        return row


def is_locked(email: str) -> bool:
    try:
        locked_key = f"login_locked:{email}"
        return cache_exists(locked_key)
    except Exception as exc:
        logging.warning("Cache error checking lock for %s: %s", email, exc)
        # fail open: if cache is unavailable, treat as not locked
        return False


def register_failed_attempt(email: str) -> int:
    try:
        fail_key = f"login_fail:{email}"
        # increment fail counter and set window expiry
        cur = cache_incr(fail_key)
        if cur == 1:
            cache_expire(fail_key, LOCK_WINDOW_SECONDS)
        # if threshold reached, set locked key
        if cur >= LOCK_THRESHOLD:
            locked_key = f"login_locked:{email}"
            set_key(locked_key, "1", ex=LOCK_DURATION_SECONDS)
        return cur
    except Exception as exc:
        logging.warning("Cache error registering failed attempt for %s: %s", email, exc)
        # If cache is down, we can't track attempts — return 0 as a safe default
        return 0


def reset_failed_attempts(email: str) -> None:
    try:
        cache_delete(f"login_fail:{email}")
        cache_delete(f"login_locked:{email}")
    except Exception as exc:
        logging.warning("Cache error resetting failed attempts for %s: %s", email, exc)
        # best-effort; nothing else to do

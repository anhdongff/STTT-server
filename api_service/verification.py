import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import smtplib
from email.message import EmailMessage

from data_service.db import PostgresDB
from data_service.sqlite_db import SqliteDB
from api_service.enum import SqliteTableName

# environment
USE_SQLITE = os.getenv("USE_SQLITE", "false") == "true"
CODE_EXPIRE_MINUTES = int(os.getenv("CODE_EXPIRE_MINUTES", "3"))
RE_SEND_VERIFICATION_CODE_MINUTES = int(os.getenv("RE_SEND_VERIFICATION_CODE_MINUTES", "1"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_APP_PASS = os.getenv("EMAIL_APP_PASS")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_db_datetime(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    # SQLite uses 'YYYY-MM-DD HH:MM:SS' (no timezone). Postgres may return ISO format.
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        logging.warning("Could not parse datetime from DB: %r", s)
        return None


def _get_db():
    return SqliteDB.from_env() if USE_SQLITE else PostgresDB.from_env()


def send_verification_code(email: str, code: str, code_type: str) -> Optional[datetime]:
    """Send a verification code to user email.

    Returns timestamp when email was sent, or None if a recent code was already sent or user not found.
    """
    if not EMAIL_USER or not EMAIL_APP_PASS:
        logging.error("Email credentials not configured")
        return None

    with _get_db() as db:
        # lookup user id
        user = db.fetchone(f"SELECT id FROM {SqliteTableName.USERS.value} WHERE email = ?", (email,))
        if not user:
            logging.info("No user found for email %s", email)
            return None
        user_id = user.get("id")

        # check latest code
        row = db.fetchone(
            f"SELECT id, created_at FROM {SqliteTableName.VERIFICATION_CODES.value} WHERE email = ? AND type = ? ORDER BY created_at DESC LIMIT 1",
            (user_id, code_type),
        )
        if row and row.get("created_at"):
            created_at = _parse_db_datetime(row.get("created_at"))
            if created_at:
                delta = _now() - created_at
                if delta < timedelta(minutes=RE_SEND_VERIFICATION_CODE_MINUTES):
                    logging.info("Recent verification code already sent to %s", email)
                    return None

        # create code hash and expiry
        code_hash = bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        expires_at = (_now() + timedelta(minutes=CODE_EXPIRE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")

        db.execute(
            f"INSERT INTO {SqliteTableName.VERIFICATION_CODES.value} (email, code_hash, type, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, code_hash, code_type, expires_at),
        )

    # send email
    try:
        msg = EmailMessage()
        msg["Subject"] = "Mã xác nhận của bạn"
        msg["From"] = EMAIL_USER
        msg["To"] = email
        msg.set_content(f"Mã xác nhận của bạn là: {code}")

        # use SSL SMTP for Gmail
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_USER, EMAIL_APP_PASS)
            smtp.send_message(msg)
        sent_at = _now()
        logging.info("Sent verification code to %s", email)
        return sent_at
    except Exception as exc:
        logging.exception("Failed to send verification email to %s: %s", email, exc)
        return None


def verify_verification_code(email: str, code: str, code_type: str) -> bool:
    """Verify a code for given email and type. Marks code as used when successful."""
    with _get_db() as db:
        user = db.fetchone(f"SELECT id FROM {SqliteTableName.USERS.value} WHERE email = ?", (email,))
        if not user:
            return False
        user_id = user.get("id")

        row = db.fetchone(
            f"SELECT id, code_hash, expires_at, used FROM {SqliteTableName.VERIFICATION_CODES.value} WHERE email = ? AND type = ? ORDER BY created_at DESC LIMIT 1",
            (user_id, code_type),
        )
        if not row:
            return False

        if row.get("used"):
            return False

        expires_at = _parse_db_datetime(row.get("expires_at"))
        if expires_at and _now() > expires_at:
            return False

        try:
            code_hash = row.get("code_hash")
            if not code_hash:
                return False
            ok = bcrypt.checkpw(code.encode("utf-8"), code_hash.encode("utf-8"))
            if not ok:
                return False
            # mark used
            db.execute(f"UPDATE {SqliteTableName.VERIFICATION_CODES.value} SET used = 1 WHERE id = ?", (row.get("id"),))
            return True
        except Exception:
            logging.exception("Error checking verification code for %s", email)
            return False

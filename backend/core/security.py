# backend/core/security.py

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from jose import JWTError, jwt
from bcrypt import checkpw, hashpw, gensalt

from backend.core.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

# ── Constants ───────────────────────────────────────────────────────────────
SECRET_KEY  = settings.jwt_secret_key
ALGORITHM   = "HS256"
TOKEN_EXPIRE_HOURS = 24


# ── Password ────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return hashpw(plain.encode(), gensalt(12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── JWT ─────────────────────────────────────────────────────────────────────

def create_access_token(data: dict[str, Any]) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload["iat"] = datetime.now(timezone.utc)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        logger.warning(f"JWT decode failed: {e}")
        return None
"""Password hashing, JWT and secret-encryption helpers."""
from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def hash_password(password: str) -> str:
    # bcrypt only looks at the first 72 bytes; truncate explicitly (bcrypt>=5 raises).
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_access_token(subject: str | uuid.UUID, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


class SecretDecryptionError(Exception):
    """A stored secret can't be decrypted (usually SECRET_KEY was rotated)."""


def _fernet() -> Fernet:
    # Derive a dedicated Fernet key from SECRET_KEY so there is no second secret
    # to manage. Rotating SECRET_KEY invalidates stored platform credentials.
    digest = hashlib.sha256(f"tabula-secret:{settings.secret_key}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError("Stored secret could not be decrypted") from exc

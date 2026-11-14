"""Authenticated encryption for small secrets stored at rest (OAuth refresh tokens, publish credentials).

    seal(plaintext, purpose="gsc-refresh-token")  -> "v1.<base64url>"
    unseal(token, purpose="gsc-refresh-token")     -> plaintext

The key comes from RANKSIM_SECRET through HKDF-SHA256, with the purpose as the HKDF info, so every purpose has its own
key. The cipher is AES-256-GCM with a random 96-bit nonce; the purpose is also bound as associated data. A token sealed
for one purpose, sealed under another RANKSIM_SECRET, or changed by a single bit fails to unseal with SecretBoxError.
Changing RANKSIM_SECRET makes every stored token unreadable (the owner then reconnects).
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SECRET_ENV = "RANKSIM_SECRET"
VERSION = "v1"
NONCE_BYTES = 12
SALT = b"ranksim-secretbox-v1"
MIN_SECRET_CHARS = 32  # HKDF is fast, so the secret itself must be long and random, not a password


class SecretBoxError(Exception):
    """A token could not be sealed or opened. The message never contains key material or plaintext."""


def configured() -> bool:
    return len(os.environ.get(SECRET_ENV, "").strip()) >= MIN_SECRET_CHARS


def seal(plaintext: str, *, purpose: str) -> str:
    if not isinstance(plaintext, str):
        raise SecretBoxError("Only text can be sealed.")
    key = _key(_secret(), _purpose(purpose))
    nonce = secrets.token_bytes(NONCE_BYTES)
    body = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), _aad(purpose))
    return f"{VERSION}." + base64.urlsafe_b64encode(nonce + body).decode("ascii").rstrip("=")


def unseal(token: str, *, purpose: str) -> str:
    if not isinstance(token, str) or not token.startswith(VERSION + "."):
        raise SecretBoxError("That sealed value is not in a known format.")
    raw = token[len(VERSION) + 1 :]
    try:
        blob = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        raise SecretBoxError("That sealed value is damaged.") from None
    if len(blob) < NONCE_BYTES + 16:
        raise SecretBoxError("That sealed value is damaged.")
    key = _key(_secret(), _purpose(purpose))
    try:
        plain = AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], _aad(purpose))
    except InvalidTag:
        raise SecretBoxError("That sealed value could not be opened (wrong key, wrong purpose, or changed).") from None
    return plain.decode("utf-8")


def _secret() -> str:
    value = os.environ.get(SECRET_ENV, "").strip()
    if len(value) < MIN_SECRET_CHARS:
        raise SecretBoxError(f"{SECRET_ENV} is not set (or shorter than {MIN_SECRET_CHARS} characters), so secrets cannot be stored.")
    return value


def _purpose(purpose: str) -> str:
    text = str(purpose or "").strip()
    if not text:
        raise SecretBoxError("A purpose is required.")
    return text


def _aad(purpose: str) -> bytes:
    return f"ranksim|{VERSION}|{_purpose(purpose)}".encode("utf-8")


@lru_cache(maxsize=32)
def _key(secret: str, purpose: str) -> bytes:
    info = f"ranksim-secretbox|{purpose}".encode("utf-8")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=SALT, info=info).derive(secret.encode("utf-8"))

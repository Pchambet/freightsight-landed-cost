"""One way to keep a secret at rest, used by everything that has to.

AES-256-GCM from `cryptography`, which the application already carries for Clerk's JWTs. The
container is deliberately dull and self-describing: a magic string, a nonce, and the ciphertext,
with the magic as authenticated data so a blob from somewhere else fails loudly instead of
half-decrypting.

Each user of this module brings its own key (backups, ERP credentials) so that one leaked key is one
kind of secret, not all of them.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
KEY_BYTES = 32


class CryptoError(RuntimeError):
    pass


def decode_key(raw: str | None, name: str) -> bytes:
    """A 32-byte key from base64, or an error naming the variable that is wrong."""
    if not raw:
        raise CryptoError(f"{name} is not set")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise CryptoError(f"{name} is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise CryptoError(f"{name} must decode to {KEY_BYTES} bytes, got {len(key)}")
    return key


def seal(plaintext: bytes, key: bytes, *, magic: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return magic + nonce + AESGCM(key).encrypt(nonce, plaintext, magic)


def unseal(blob: bytes, key: bytes, *, magic: bytes) -> bytes:
    if not blob.startswith(magic):
        raise CryptoError("This is not a value sealed by this application")
    nonce = blob[len(magic) : len(magic) + NONCE_BYTES]
    try:
        return AESGCM(key).decrypt(nonce, blob[len(magic) + NONCE_BYTES :], magic)
    except InvalidTag as exc:
        raise CryptoError("Wrong key, or the value has been tampered with") from exc

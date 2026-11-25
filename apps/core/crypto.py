"""Encryption-key helpers shared by every app that stores secrets at rest.

The project has several key regimes (``FERNET_KEY`` for ad-network credentials and
firmware archive passwords, ``SOCIAL_CREDENTIAL_ENCRYPTION_KEY`` for third-party
credentials, ``MFA_ENCRYPTION_KEY`` for TOTP secrets). They are independent on purpose:
a leak of one must not expose the others. This module gives them one validation and
one rotation mechanism:

* A Fernet setting may hold a comma separated list of keys. The first key encrypts;
  every key can decrypt (``MultiFernet``). Rotation can happen without downtime.
* Configuration validators return human-readable problems without exposing keys.

Nothing here reads Django settings or logs key material.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Sequence

from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.core.exceptions import ImproperlyConfigured

KEY_SEPARATOR = ","
MIN_PASSPHRASE_LENGTH = 32
MIN_PASSPHRASE_DISTINCT_CHARACTERS = 10


def split_keys(raw: str | None) -> list[str]:
    """The non-empty entries of a comma separated key list (order preserved)."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(KEY_SEPARATOR) if part.strip()]


def fernet_key_problem(key: str) -> str | None:
    """Why ``key`` is not a Fernet key (32 url-safe base64 bytes), or ``None``."""
    try:
        Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        return "it is not a Fernet key (32 url-safe base64-encoded bytes)"
    return None


def passphrase_problems(value: str, *, forbidden: Iterable[str] = ()) -> list[str]:
    """Why ``value`` is too weak to derive an encryption key from (empty = fine)."""
    problems: list[str] = []
    if len(value) < MIN_PASSPHRASE_LENGTH:
        problems.append(f"it is shorter than {MIN_PASSPHRASE_LENGTH} characters")
    if len(set(value)) < MIN_PASSPHRASE_DISTINCT_CHARACTERS:
        problems.append(
            f"it has fewer than {MIN_PASSPHRASE_DISTINCT_CHARACTERS} distinct "
            "characters"
        )
    if any(value == other for other in forbidden if other):
        problems.append("it is identical to a secret that must stay independent")
    return problems


def multi_fernet(raw: str | None, *, setting_name: str) -> MultiFernet:
    """A ``MultiFernet`` over the comma separated Fernet keys in ``raw``.

    Raises ``ImproperlyConfigured`` naming ``setting_name`` when the list is empty or
    an entry is malformed (the position, never the key, is reported).
    """
    keys = split_keys(raw)
    if not keys:
        raise ImproperlyConfigured(f"{setting_name} must be set (a Fernet key).")
    fernets: list[Fernet] = []
    for position, key in enumerate(keys, start=1):
        problem = fernet_key_problem(key)
        if problem:
            raise ImproperlyConfigured(
                f"{setting_name} entry {position} is invalid: {problem}."
            )
        fernets.append(Fernet(key.encode("ascii")))
    return MultiFernet(fernets)


def derive_fernet(passphrase: str, *, info: bytes) -> Fernet:
    """A Fernet derived from ``passphrase`` (HKDF-SHA256, domain label ``info``)."""
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(
        passphrase.encode("utf-8")
    )
    return Fernet(base64.urlsafe_b64encode(derived))


def multi_fernet_from_passphrases(
    passphrases: Sequence[str], *, info: bytes, setting_name: str
) -> MultiFernet:
    """A ``MultiFernet`` over passphrase-derived keys (first encrypts, all decrypt)."""
    usable = [value for value in passphrases if value]
    if not usable:
        raise ImproperlyConfigured(f"{setting_name} must be set.")
    return MultiFernet([derive_fernet(value, info=info) for value in usable])


def rotate_token(cipher: MultiFernet, token: str) -> str:
    """Re-encrypt ``token`` under the primary key; raises ``InvalidToken``."""
    return cipher.rotate(token.encode("ascii")).decode("ascii")


__all__ = [
    "KEY_SEPARATOR",
    "MIN_PASSPHRASE_DISTINCT_CHARACTERS",
    "MIN_PASSPHRASE_LENGTH",
    "derive_fernet",
    "fernet_key_problem",
    "multi_fernet",
    "multi_fernet_from_passphrases",
    "passphrase_problems",
    "rotate_token",
    "split_keys",
]

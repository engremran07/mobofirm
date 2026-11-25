"""Compatibility imports for the shared key helpers in ``apps.core.crypto``."""

from apps.core.crypto import (
    KEY_SEPARATOR,
    MIN_PASSPHRASE_DISTINCT_CHARACTERS,
    MIN_PASSPHRASE_LENGTH,
    derive_fernet,
    fernet_key_problem,
    multi_fernet,
    multi_fernet_from_passphrases,
    passphrase_problems,
    rotate_token,
    split_keys,
)

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

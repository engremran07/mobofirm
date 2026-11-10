"""Minimal WebAuthn (level 2) verification for platform-authenticator OS identity.

Only what the OS-registration flow needs, built on the already-installed
``cryptography`` package: strict base64url decoding, ``clientDataJSON`` and
``authenticatorData`` checks, and ES256 / RS256 signature verification against a
stored SubjectPublicKeyInfo key. Attestation is not used (``attestation: "none"``), so
no CBOR parsing is required: the browser hands over the public key in SPKI form.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Collection
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

ALG_ES256 = -7
ALG_RS256 = -257
SUPPORTED_ALGORITHMS = (ALG_ES256, ALG_RS256)
MIN_RSA_KEY_BITS = 2048

FLAG_USER_PRESENT = 0x01
FLAG_ATTESTED_CREDENTIAL = 0x40

CLIENT_DATA_CREATE = "webauthn.create"
CLIENT_DATA_GET = "webauthn.get"

_AUTH_DATA_HEADER = 37  # rpIdHash (32) + flags (1) + signCount (4)
_AAGUID_LENGTH = 16


class WebAuthnError(Exception):
    """A WebAuthn payload failed validation; ``reason`` is a stable machine code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AuthenticatorData:
    rp_id_hash: bytes
    flags: int
    sign_count: int
    credential_id: bytes | None

    @property
    def user_present(self) -> bool:
        return bool(self.flags & FLAG_USER_PRESENT)


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(value: object, *, max_bytes: int = 4096) -> bytes:
    """Decode unpadded base64url strictly; anything else is a malformed payload."""
    if not isinstance(value, str) or not value or len(value) > max_bytes * 2:
        raise WebAuthnError("malformed")
    try:
        raw = base64.b64decode(
            value.replace("-", "+").replace("_", "/") + "=" * (-len(value) % 4),
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise WebAuthnError("malformed") from exc
    if not raw or len(raw) > max_bytes:
        raise WebAuthnError("malformed")
    return raw


def rp_id_hash(rp_id: str) -> bytes:
    return hashlib.sha256(rp_id.encode("utf-8")).digest()


def verify_client_data(
    raw: bytes,
    *,
    expected_type: str,
    expected_challenge: bytes,
    allowed_origins: Collection[str],
) -> None:
    """Check type, the server-issued challenge and the calling origin."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebAuthnError("malformed") from exc
    if not isinstance(data, dict):
        raise WebAuthnError("malformed")
    if data.get("type") != expected_type:
        raise WebAuthnError("wrong_type")
    challenge = data.get("challenge")
    try:
        presented = b64url_decode(challenge, max_bytes=256)
    except WebAuthnError as exc:
        raise WebAuthnError("challenge_mismatch") from exc
    if not hmac.compare_digest(presented, expected_challenge):
        raise WebAuthnError("challenge_mismatch")
    origin = data.get("origin")
    if not isinstance(origin, str) or origin not in set(allowed_origins):
        raise WebAuthnError("origin_not_allowed")
    if data.get("crossOrigin") is True:
        raise WebAuthnError("origin_not_allowed")


def parse_authenticator_data(raw: bytes) -> AuthenticatorData:
    if len(raw) < _AUTH_DATA_HEADER:
        raise WebAuthnError("malformed")
    flags = raw[32]
    sign_count = int.from_bytes(raw[33:37], "big")
    credential_id: bytes | None = None
    if flags & FLAG_ATTESTED_CREDENTIAL:
        start = _AUTH_DATA_HEADER + _AAGUID_LENGTH
        if len(raw) < start + 2:
            raise WebAuthnError("malformed")
        length = int.from_bytes(raw[start : start + 2], "big")
        credential_id = raw[start + 2 : start + 2 + length]
        if length == 0 or len(credential_id) != length:
            raise WebAuthnError("malformed")
    return AuthenticatorData(
        rp_id_hash=raw[:32],
        flags=flags,
        sign_count=sign_count,
        credential_id=credential_id,
    )


def check_authenticator_data(data: AuthenticatorData, *, rp_id: str) -> None:
    if not hmac.compare_digest(data.rp_id_hash, rp_id_hash(rp_id)):
        raise WebAuthnError("rp_id_mismatch")
    if not data.user_present:
        raise WebAuthnError("user_not_present")


def load_public_key(spki_der: bytes, algorithm: int):
    """Load an SPKI key and require it to match the declared COSE algorithm."""
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise WebAuthnError("unsupported_algorithm")
    try:
        key = serialization.load_der_public_key(spki_der)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise WebAuthnError("malformed") from exc
    if algorithm == ALG_ES256:
        if not (
            isinstance(key, ec.EllipticCurvePublicKey)
            and isinstance(key.curve, ec.SECP256R1)
        ):
            raise WebAuthnError("unsupported_algorithm")
    elif not (isinstance(key, rsa.RSAPublicKey) and key.key_size >= MIN_RSA_KEY_BITS):
        raise WebAuthnError("unsupported_algorithm")
    return key


def verify_assertion_signature(
    public_key,
    algorithm: int,
    *,
    authenticator_data: bytes,
    client_data_json: bytes,
    signature: bytes,
) -> None:
    """Verify ``signature`` over ``authData || SHA-256(clientDataJSON)``."""
    signed = authenticator_data + hashlib.sha256(client_data_json).digest()
    try:
        if algorithm == ALG_ES256:
            public_key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif algorithm == ALG_RS256:
            public_key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            raise WebAuthnError("unsupported_algorithm")
    except InvalidSignature as exc:
        raise WebAuthnError("bad_signature") from exc

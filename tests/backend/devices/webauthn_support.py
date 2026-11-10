"""A software platform authenticator for the OS-identity tests.

It builds the same payloads the browser module sends, so the real verification code
runs end to end. ``RP_ID``/``ORIGIN`` match Django's test client (``testserver``).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from django.test import Client
from django.urls import reverse

from apps.devices.webauthn import ALG_ES256, ALG_RS256, b64url_decode, b64url_encode
from apps.users.models import Notification

RP_ID = "testserver"
ORIGIN = "http://testserver"


class PlatformAuthenticator:
    """One credential on one operating system; every browser can ask it to sign."""

    def __init__(self, *, algorithm: int = ALG_ES256) -> None:
        self.algorithm = algorithm
        if algorithm == ALG_RS256:
            self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        else:
            self._key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0

    @property
    def credential_id_b64(self) -> str:
        return b64url_encode(self.credential_id)

    def public_key_der(self) -> bytes:
        return self._key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin,
                "crossOrigin": False,
            }
        ).encode()

    def _sign(self, data: bytes) -> bytes:
        if self.algorithm == ALG_RS256:
            return self._key.sign(data, padding.PKCS1v15(), hashes.SHA256())
        return self._key.sign(data, ec.ECDSA(hashes.SHA256()))

    def create(
        self,
        options: dict[str, Any],
        *,
        origin: str = ORIGIN,
        rp_id: str = RP_ID,
        kind: str = "webauthn.create",
        challenge: str | None = None,
    ) -> dict[str, Any]:
        """The registration payload for ``options['publicKey']`` from the server."""
        public_key = options["publicKey"]
        client_data = self._client_data(
            kind, challenge or public_key["challenge"], origin
        )
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([0x41])
            + self.sign_count.to_bytes(4, "big")
            + bytes(16)
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + b"\xa0"
        )
        return {
            "credentialId": self.credential_id_b64,
            "clientDataJSON": b64url_encode(client_data),
            "authenticatorData": b64url_encode(auth_data),
            "publicKey": b64url_encode(self.public_key_der()),
            "publicKeyAlgorithm": self.algorithm,
        }

    def get(
        self,
        options: dict[str, Any],
        *,
        origin: str = ORIGIN,
        rp_id: str = RP_ID,
        kind: str = "webauthn.get",
        flags: int = 0x01,
        bump: bool = True,
        sign_count: int | None = None,
    ) -> dict[str, Any]:
        """The assertion payload answering ``options['publicKey']``."""
        public_key = options["publicKey"]
        if bump:
            self.sign_count += 1
        count = self.sign_count if sign_count is None else sign_count
        client_data = self._client_data(kind, public_key["challenge"], origin)
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + count.to_bytes(4, "big")
        )
        signature = self._sign(auth_data + hashlib.sha256(client_data).digest())
        return {
            "credentialId": self.credential_id_b64,
            "clientDataJSON": b64url_encode(client_data),
            "authenticatorData": b64url_encode(auth_data),
            "signature": b64url_encode(signature),
        }


def tamper(payload: dict[str, Any], field: str) -> dict[str, Any]:
    """Flip one byte of a base64url field (for bad-signature style rejections)."""
    raw = bytearray(b64url_decode(payload[field]))
    raw[-1] ^= 0x01
    return {**payload, field: b64url_encode(bytes(raw))}


WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/121.0.0.0 Safari/537.36"
)


def post(client: Client, name: str, body: Any, **extra: Any):
    """POST JSON (or a raw string body) to a ``devices:`` route."""
    return client.post(
        reverse(f"devices:{name}"),
        data=json.dumps(body) if not isinstance(body, str) else body,
        content_type="application/json",
        **extra,
    )


def get_options(client: Client, purpose: str, **extra: Any) -> dict[str, Any]:
    response = post(client, "os_options", {"purpose": purpose}, **extra)
    assert response.status_code == 200, response.content
    return response.json()


def titles(user: Any) -> list[str]:
    """Titles of the user's notifications, oldest first."""
    return list(
        Notification.objects.filter(recipient=user)
        .order_by("created_at", "pk")
        .values_list("title", flat=True)
    )

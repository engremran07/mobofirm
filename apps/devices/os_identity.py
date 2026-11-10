"""Operating-system device identity through a platform WebAuthn credential.

A website cannot read an OS install id. A *platform* authenticator credential
(Windows Hello/TPM, macOS or Android keystore) is the closest web-only equivalent:
it is shared by every browser on that OS user profile and is destroyed when the OS
is reinstalled. A ``Device`` row that carries such a credential therefore stands for
one operating system, whichever browser is used; the random browser cookie remains
only a fallback for browsers/contexts without a platform authenticator.

Transaction boundary: each public function validates the payload and the
single-use challenge first, then does all writes inside one ``transaction.atomic``
holding the account row lock, so a failure leaves no partial registration. Failures
are audited after the rollback. Registration is idempotent per (account, credential).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.core.utils.ip import get_client_ip
from apps.devices import webauthn
from apps.devices.models import Device, DeviceBrowserLink, DeviceEvent
from apps.devices.services import (
    DevicePolicyError,
    describe_platform,
    enforce_registration_limits,
    ensure_browser_key,
    get_effective_policy,
    hash_browser_key,
    log_device_event,
    notify_device_registered,
    trust_pending_device,
)

logger = logging.getLogger(__name__)

PURPOSE_REGISTER = "register"
PURPOSE_VERIFY = "verify"
_SESSION_KEY = "devices_webauthn_challenge"
_MAX_CREDENTIAL_ID_BYTES = 512
_MAX_PUBLIC_KEY_BYTES = 1024


class OsIdentityError(Exception):
    """A registration/verification was refused; ``reason`` is a stable machine code."""

    def __init__(self, reason: str, *, device: Device | None = None):
        super().__init__(reason)
        self.reason = reason
        self.device = device


@dataclass(frozen=True)
class RegistrationResult:
    device: Device
    created: bool
    approved: bool
    limit_reached: bool


def os_identity_enabled() -> bool:
    return bool(getattr(settings, "DEVICE_OS_IDENTITY_ENABLED", True))


def rp_id_for(request: HttpRequest) -> str:
    """The WebAuthn relying-party id: configured, else the (validated) request host."""
    configured = str(getattr(settings, "DEVICE_WEBAUTHN_RP_ID", "") or "").strip()
    if configured:
        return configured.lower()
    return (urlsplit(f"//{request.get_host()}").hostname or "").lower()


def allowed_origins(request: HttpRequest) -> set[str]:
    """Origins a ceremony may come from: this request's, ``SITE_URL`` and the setting.

    ``request.get_host()`` is already validated against ``ALLOWED_HOSTS``, so local
    development (``http://localhost:8000``) works without configuration.
    """
    origins = {f"{request.scheme}://{request.get_host()}"}
    site_url = urlsplit(str(getattr(settings, "SITE_URL", "") or ""))
    if site_url.scheme and site_url.netloc:
        origins.add(f"{site_url.scheme}://{site_url.netloc}")
    origins.update(
        str(origin).rstrip("/")
        for origin in getattr(settings, "DEVICE_WEBAUTHN_ALLOWED_ORIGINS", ())
        if origin
    )
    return origins


def user_handle(user) -> bytes:
    """Opaque, stable per-account WebAuthn user handle (no personal data)."""
    return salted_hmac(
        "apps.devices.webauthn.user-handle.v1", str(user.pk), algorithm="sha256"
    ).digest()


def credential_id_hash(credential_id: bytes) -> str:
    return hashlib.sha256(credential_id).hexdigest()


# --- challenges --------------------------------------------------------------


def _now_ts() -> float:
    return timezone.now().timestamp()


def issue_challenge(request: HttpRequest, user, purpose: str) -> bytes:
    """A fresh server challenge bound to this session, account and purpose."""
    raw = secrets.token_bytes(32)
    request.session[_SESSION_KEY] = {
        "c": webauthn.b64url_encode(raw),
        "p": purpose,
        "u": str(user.pk),
        "t": _now_ts(),
    }
    return raw


def consume_challenge(request: HttpRequest, user, purpose: str) -> bytes:
    """Take the pending challenge. It is single use: a failed attempt burns it too."""
    state = request.session.pop(_SESSION_KEY, None)
    if not isinstance(state, dict):
        raise OsIdentityError("challenge_missing")
    ttl = int(getattr(settings, "DEVICE_WEBAUTHN_CHALLENGE_TTL", 300))
    issued = state.get("t")
    if not isinstance(issued, (int, float)) or _now_ts() - issued > ttl:
        raise OsIdentityError("challenge_expired")
    if state.get("p") != purpose or state.get("u") != str(user.pk):
        raise OsIdentityError("challenge_mismatch")
    try:
        return webauthn.b64url_decode(state.get("c"), max_bytes=256)
    except webauthn.WebAuthnError as exc:
        raise OsIdentityError("challenge_mismatch") from exc


# --- options -------------------------------------------------------------------


def _descriptor(device: Device) -> dict[str, Any]:
    return {
        "type": "public-key",
        "id": device.os_credential_id,
        "transports": ["internal"],
    }


def _credential_devices(user, rp_id: str):
    return Device.objects.filter(
        user=user,
        os_credential_id_hash__gt="",
        os_credential_rp_id=rp_id,
        revoked_at__isnull=True,
    )


def build_options(request: HttpRequest, user, purpose: str) -> dict[str, Any]:
    """The ``publicKey`` options for ``credentials.create`` / ``credentials.get``."""
    if not os_identity_enabled():
        raise OsIdentityError("disabled")
    rp_id = rp_id_for(request)
    timeout = int(getattr(settings, "DEVICE_WEBAUTHN_TIMEOUT_MS", 60000))
    if purpose == PURPOSE_VERIFY:
        allowed = [
            _descriptor(device)
            for device in _credential_devices(user, rp_id).filter(is_blocked=False)
        ]
        if not allowed:
            raise OsIdentityError("no_credentials")
        return {
            "challenge": webauthn.b64url_encode(
                issue_challenge(request, user, purpose)
            ),
            "rpId": rp_id,
            "allowCredentials": allowed,
            "userVerification": "discouraged",
            "timeout": timeout,
        }
    if purpose != PURPOSE_REGISTER:
        raise OsIdentityError("malformed")
    name = user.get_username()
    return {
        "rp": {
            "id": rp_id,
            "name": str(getattr(settings, "SITE_NAME", "") or rp_id),
        },
        "user": {
            "id": webauthn.b64url_encode(user_handle(user)),
            "name": name,
            "displayName": getattr(user, "full_name", "") or name,
        },
        "challenge": webauthn.b64url_encode(issue_challenge(request, user, purpose)),
        "pubKeyCredParams": [
            {"type": "public-key", "alg": webauthn.ALG_ES256},
            {"type": "public-key", "alg": webauthn.ALG_RS256},
        ],
        "authenticatorSelection": {
            "authenticatorAttachment": "platform",
            "residentKey": "discouraged",
            "userVerification": "discouraged",
        },
        "attestation": "none",
        "excludeCredentials": [
            _descriptor(device) for device in _credential_devices(user, rp_id)
        ],
        "timeout": timeout,
    }


# --- shared helpers --------------------------------------------------------------


def _event_context(request: HttpRequest) -> dict[str, Any]:
    return {
        "ip": get_client_ip(request) or "",
        "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:512],
        "country": "",
    }


def _field(payload: Mapping[str, Any], name: str, *, max_bytes: int = 4096) -> bytes:
    return webauthn.b64url_decode(payload.get(name), max_bytes=max_bytes)


def _link_browser(user, device: Device, key_hash: str, now) -> None:
    """Recognise this browser on ``device``'s OS and fold its fallback registration.

    Caller holds the account lock inside an atomic block. A pending fallback row was
    only a placeholder and is deleted; an approved one hands its slot to the OS
    (one OS = one slot).
    """
    fallback = (
        Device.objects.select_for_update()
        .filter(user=user, browser_key_hash=key_hash, os_credential_id_hash="")
        .exclude(pk=device.pk)
        .first()
    )
    transfer_slot = False
    if fallback is not None and fallback.is_blocked:
        raise OsIdentityError("blocked_device", device=fallback)
    if fallback is not None and fallback.revoked_at is None:
        transfer_slot = fallback.is_trusted
        if transfer_slot:
            fallback.is_trusted = False
            fallback.browser_key_hash = ""
            fallback.metadata = {
                **(fallback.metadata or {}),
                "folded_into_device": str(device.pk),
            }
            fallback.save(update_fields=["is_trusted", "browser_key_hash", "metadata"])
        else:
            fallback.delete()
    link, created = DeviceBrowserLink.objects.get_or_create(
        user=user,
        browser_key_hash=key_hash,
        defaults={"device": device, "first_linked_at": now, "verified_at": now},
    )
    if not created:
        link.device = device
        link.verified_at = now
        link.save(update_fields=["device", "verified_at"])
    if transfer_slot and not device.is_trusted:
        trust_pending_device(user, device, reason="os_replaced_browser_registration")


def _reject_unusable(device: Device) -> None:
    if device.is_blocked:
        raise OsIdentityError("blocked_device", device=device)
    if device.revoked_at is not None:
        raise OsIdentityError("revoked_device", device=device)


# --- registration --------------------------------------------------------------


def register_os_credential(
    request: HttpRequest, user, payload: Mapping[str, Any]
) -> RegistrationResult:
    """Register this operating system (its platform credential) for ``user``.

    Idempotent per (account, credential). The OS is approved immediately when the
    allowance has room; otherwise it stays pending and ``limit_reached`` is set.
    """
    try:
        result = _register(request, user, payload)
    except (webauthn.WebAuthnError, DevicePolicyError) as exc:
        error = OsIdentityError(exc.reason)
    except OsIdentityError as exc:
        error = exc
    else:
        if result.created and result.approved:
            notify_device_registered(user, result.device, get_client_ip(request) or "")
        return result
    log_device_event(
        error.device,
        user,
        "registration",
        success=False,
        reason=f"os_register_{error.reason}",
        ctx=_event_context(request),
    )
    raise error


def _register(
    request: HttpRequest, user, payload: Mapping[str, Any]
) -> RegistrationResult:
    if not os_identity_enabled():
        raise OsIdentityError("disabled")
    rp_id = rp_id_for(request)
    challenge = consume_challenge(request, user, PURPOSE_REGISTER)
    credential_id = _field(payload, "credentialId", max_bytes=_MAX_CREDENTIAL_ID_BYTES)
    client_data = _field(payload, "clientDataJSON")
    public_key_der = _field(payload, "publicKey", max_bytes=_MAX_PUBLIC_KEY_BYTES)
    algorithm = payload.get("publicKeyAlgorithm")
    if not isinstance(algorithm, int) or isinstance(algorithm, bool):
        raise OsIdentityError("malformed")
    webauthn.verify_client_data(
        client_data,
        expected_type=webauthn.CLIENT_DATA_CREATE,
        expected_challenge=challenge,
        allowed_origins=allowed_origins(request),
    )
    auth = webauthn.parse_authenticator_data(_field(payload, "authenticatorData"))
    webauthn.check_authenticator_data(auth, rp_id=rp_id)
    if auth.credential_id != credential_id:
        raise OsIdentityError("credential_mismatch")
    webauthn.load_public_key(public_key_der, algorithm)

    public_key = base64.b64encode(public_key_der).decode("ascii")
    cred_hash = credential_id_hash(credential_id)
    key_hash = hash_browser_key(ensure_browser_key(request))
    ua = (request.META.get("HTTP_USER_AGENT") or "").strip()
    os_family, device_type = describe_platform(ua)
    now = timezone.now()
    created = False
    limit_reached = False
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        device = (
            Device.objects.select_for_update()
            .filter(user=user, os_credential_id_hash=cred_hash)
            .first()
        )
        if device is not None:
            _reject_unusable(device)
            if (
                device.os_credential_public_key != public_key
                or device.os_credential_alg != algorithm
                or device.os_credential_rp_id != rp_id
            ):
                raise OsIdentityError("credential_conflict", device=device)
        else:
            created = True
            # A pending/approved fallback registration of this very browser is
            # upgraded in place so it keeps its slot and history.
            device = (
                Device.objects.select_for_update()
                .filter(
                    user=user,
                    browser_key_hash=key_hash,
                    os_credential_id_hash="",
                    revoked_at__isnull=True,
                )
                .first()
            )
            if device is not None:
                _reject_unusable(device)
            else:
                enforce_registration_limits(
                    user, get_effective_policy(request, user=user), now
                )
                device = Device(user=user, first_seen_at=now)
            device.browser_key_hash = ""
            device.os_credential_id = webauthn.b64url_encode(credential_id)
            device.os_credential_id_hash = cred_hash
            device.os_credential_public_key = public_key
            device.os_credential_alg = algorithm
            device.os_credential_sign_count = auth.sign_count
            device.os_credential_rp_id = rp_id
            device.display_name = f"{os_family} {device_type}"
            device.os_name = os_family
            device.os_family = os_family
            device.browser_family = ""
            device.metadata = {"device_type": device_type, "identity": "os_credential"}
            device.last_seen_at = now
            device.save()
            DeviceEvent.objects.create(
                device=device,
                user=user,
                event_type="registration",
                success=True,
                reason="os_credential_registered",
                ip=get_client_ip(request) or "",
                user_agent=ua[:512],
            )
        _link_browser(user, device, key_hash, now)
        if not device.is_trusted:
            try:
                trust_pending_device(user, device, reason="os_registered")
            except DevicePolicyError as exc:
                if exc.reason != "limit_reached":
                    raise
                limit_reached = True
    return RegistrationResult(
        device=device,
        created=created,
        approved=device.is_trusted,
        limit_reached=limit_reached,
    )


# --- verification ----------------------------------------------------------------


def verify_os_credential(
    request: HttpRequest, user, payload: Mapping[str, Any]
) -> Device:
    """Verify a WebAuthn assertion and recognise this browser on the registered OS."""
    try:
        device = _verify(request, user, payload)
    except (webauthn.WebAuthnError, DevicePolicyError) as exc:
        error = OsIdentityError(exc.reason)
    except OsIdentityError as exc:
        error = exc
    else:
        return device
    log_device_event(
        error.device,
        user,
        "login",
        success=False,
        reason=f"os_verify_{error.reason}",
        ctx=_event_context(request),
    )
    raise error


def _verify(request: HttpRequest, user, payload: Mapping[str, Any]) -> Device:
    if not os_identity_enabled():
        raise OsIdentityError("disabled")
    challenge = consume_challenge(request, user, PURPOSE_VERIFY)
    credential_id = _field(payload, "credentialId", max_bytes=_MAX_CREDENTIAL_ID_BYTES)
    client_data = _field(payload, "clientDataJSON")
    authenticator_data = _field(payload, "authenticatorData")
    signature = _field(payload, "signature")
    rp_id = rp_id_for(request)
    key_hash = hash_browser_key(ensure_browser_key(request))
    now = timezone.now()
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        # Only this account's credentials are searched: another account's credential
        # id is simply unknown here.
        device = (
            Device.objects.select_for_update()
            .filter(user=user, os_credential_id_hash=credential_id_hash(credential_id))
            .first()
        )
        if device is None:
            raise OsIdentityError("unknown_credential")
        _reject_unusable(device)
        if device.os_credential_rp_id != rp_id:
            raise OsIdentityError("rp_id_mismatch", device=device)
        webauthn.verify_client_data(
            client_data,
            expected_type=webauthn.CLIENT_DATA_GET,
            expected_challenge=challenge,
            allowed_origins=allowed_origins(request),
        )
        auth = webauthn.parse_authenticator_data(authenticator_data)
        webauthn.check_authenticator_data(auth, rp_id=rp_id)
        public_key = webauthn.load_public_key(
            base64.b64decode(device.os_credential_public_key),
            int(device.os_credential_alg or 0),
        )
        webauthn.verify_assertion_signature(
            public_key,
            int(device.os_credential_alg or 0),
            authenticator_data=authenticator_data,
            client_data_json=client_data,
            signature=signature,
        )
        stored_count = device.os_credential_sign_count
        if (auth.sign_count or stored_count) and auth.sign_count <= stored_count:
            raise OsIdentityError("sign_count_regressed", device=device)
        device.os_credential_sign_count = auth.sign_count
        device.last_seen_at = now
        device.save(update_fields=["os_credential_sign_count", "last_seen_at"])
        _link_browser(user, device, key_hash, now)
        DeviceEvent.objects.create(
            device=device,
            user=user,
            event_type="login",
            success=True,
            reason="os_identity_verified",
            ip=get_client_ip(request) or "",
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:512],
        )
    return device

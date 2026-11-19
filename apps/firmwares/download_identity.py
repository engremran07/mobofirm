"""Who owns a download token: a signed-in user or a signed browser cookie.

An anonymous visitor used to be identified by IP address, so everyone behind one NAT
(or every request without a derivable IP) shared one token and its countdown. The
identity is now a random id in a signed, ``HttpOnly`` cookie; only a keyed hash of it
is stored (``DownloadToken.anon_hash``). The IP is kept for the anonymous daily quota
and for rate limiting, never as an identity.

The cookie is strictly necessary for the download gate (it carries no tracking id and
is never read by another app).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils.crypto import constant_time_compare, salted_hmac

IDENTITY_COOKIE = "mf_dl"
IDENTITY_SALT = "firmwares.download-identity"
IDENTITY_HASH_SALT = "firmwares.download-identity-hash"
IDENTITY_MAX_AGE = 30 * 24 * 3600
ANON_SUBJECT = "a"
USER_SUBJECT = "u"


@dataclass(frozen=True)
class DownloadIdentity:
    """The caller of a download request.

    ``user`` is the signed-in user, otherwise ``anon_id`` is the browser's cookie id
    (``is_new`` when the cookie still has to be sent to the browser).
    """

    user: Any | None
    anon_id: str = ""
    is_new: bool = False

    @property
    def anon_hash(self) -> str:
        """The stored form of an anonymous id (empty for a signed-in user)."""
        return hash_anon_id(self.anon_id) if self.user is None else ""

    @property
    def subject(self) -> str:
        """The principal a signed link is bound to (``u<pk>`` or ``a<hash>``)."""
        if self.user is not None:
            return f"{USER_SUBJECT}{self.user.pk}"
        return f"{ANON_SUBJECT}{self.anon_hash}"


def hash_anon_id(anon_id: str) -> str:
    """Keyed SHA-256 of a cookie id; the raw id is never stored."""
    if not anon_id:
        return ""
    return salted_hmac(IDENTITY_HASH_SALT, anon_id, algorithm="sha256").hexdigest()


def resolve_identity(request: HttpRequest) -> DownloadIdentity:
    """The signed-in user, else the browser's cookie id (a fresh one when absent)."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return DownloadIdentity(user=user)
    existing = request.get_signed_cookie(
        IDENTITY_COOKIE, default="", salt=IDENTITY_SALT, max_age=IDENTITY_MAX_AGE
    )
    if isinstance(existing, str) and existing:
        return DownloadIdentity(user=None, anon_id=existing)
    return DownloadIdentity(user=None, anon_id=secrets.token_urlsafe(24), is_new=True)


def attach_identity_cookie(
    response: HttpResponse, request: HttpRequest, identity: DownloadIdentity
) -> None:
    """Send a freshly minted anonymous id to the browser (no-op otherwise)."""
    if not identity.is_new or identity.user is not None:
        return
    response.set_signed_cookie(
        IDENTITY_COOKIE,
        identity.anon_id,
        salt=IDENTITY_SALT,
        max_age=IDENTITY_MAX_AGE,
        secure=request.is_secure(),
        httponly=True,
        samesite="Lax",
    )


def subject_of(token: Any) -> str:
    """The principal a token (and every link made for it) belongs to."""
    if token.user_id is not None:
        return f"{USER_SUBJECT}{token.user_id}"
    return f"{ANON_SUBJECT}{token.anon_hash}"


def owns_token(identity: DownloadIdentity, token: Any) -> bool:
    """Whether ``identity`` is the one the token was issued to."""
    if token.user_id is not None:
        return identity.user is not None and identity.user.pk == token.user_id
    return bool(token.anon_hash) and constant_time_compare(
        identity.anon_hash, token.anon_hash
    )

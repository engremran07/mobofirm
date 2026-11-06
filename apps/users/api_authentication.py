"""Token authentication for the explicit API-client endpoints of the users app."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from allauth.account.models import EmailAddress
from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed


def token_ttl() -> timedelta:
    return timedelta(seconds=int(getattr(settings, "API_TOKEN_TTL_SECONDS", 604800)))


def issue_api_token(user: Any) -> Token:
    """Replace any existing token so each sign-in rotates the credential."""
    Token.objects.filter(user=user).delete()
    return Token.objects.create(user=user)


def revoke_api_tokens(user: Any) -> int:
    deleted, _ = Token.objects.filter(user=user).delete()
    return deleted


class ExpiringTokenAuthentication(TokenAuthentication):
    """
    Token authentication that enforces expiry and the account gates that the
    browser flow applies: the user must be active and have a verified email.
    """

    def authenticate_credentials(self, key: str):
        user, token = super().authenticate_credentials(key)
        if token.created + token_ttl() < timezone.now():
            token.delete()
            raise AuthenticationFailed("Token has expired.")
        if not EmailAddress.objects.filter(
            user=user, email__iexact=user.email, verified=True
        ).exists():
            raise AuthenticationFailed("Email address is not verified.")
        return user, token

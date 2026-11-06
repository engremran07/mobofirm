"""Typed access to the authenticated user of a request."""

from __future__ import annotations

from typing import Protocol

from django.core.exceptions import PermissionDenied

from apps.users.models import CustomUser


class _HasUser(Protocol):
    @property
    def user(self) -> object: ...


def current_user(request: _HasUser) -> CustomUser:
    """Return the request's authenticated ``CustomUser`` or deny the request.

    Views behind ``login_required`` / ``IsAuthenticated`` always satisfy this; the
    check narrows ``request.user`` (``CustomUser | AnonymousUser``) for typing.
    """
    user = request.user
    if not isinstance(user, CustomUser):
        raise PermissionDenied("Authentication required.")
    return user

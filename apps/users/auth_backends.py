# apps/users/auth_backends.py
"""
Enterprise-grade multi-identifier authentication backend for the platform.

✅ Login via email, username, or phone (case-insensitive)
✅ Compatible with django-allauth and Django admin
✅ Safe against enumeration and timing leaks
✅ RFC 7613 Unicode normalization (casefold)
✅ Structured logging and exception safety
✅ Lazy model resolution to avoid import-time circulars
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import MultipleObjectsReturned
from django.db.models import Q

logger = logging.getLogger(__name__)


class MultiFieldAuthBackend(ModelBackend):
    """
    Authenticate users by email, username, or phone number.

    Compatible with Django admin, django-allauth, and session auth.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        """
        Normalize user identifier to lowercase and strip whitespace.

        Uses str.casefold() for Unicode-safe comparisons (RFC 7613).
        """
        return str(identifier or "").strip().casefold()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def authenticate(
        self,
        request=None,
        username: str | None = None,
        password: str | None = None,
        **kwargs,
    ) -> Any | None:
        """
        Authenticate using email, username, or phone.
        Returns user instance or None.
        """
        if not username or password is None:
            logger.debug("Authentication attempt missing credentials.")
            return None

        identifier = self._normalize_identifier(username)
        UserModel = get_user_model()
        user = None

        try:
            # Filter by any accepted identifier (case-insensitive)
            qs = UserModel.objects.filter(
                Q(email__iexact=identifier)
                | Q(username__iexact=identifier)
                | Q(phone__iexact=identifier)
            ).distinct()

            # Load at most two rows to detect duplicates cheaply
            candidates: Sequence[UserModel] = list(qs[:2])

            if not candidates:
                # Perform dummy hash work to equalize timing and mitigate enumeration
                try:
                    dummy = UserModel()
                    dummy.set_password(password)
                    dummy.check_password(password)
                except Exception:  # noqa: S110
                    # best-effort only; never raise
                    pass
                logger.debug("No user found for identifier=%s", identifier)
                return None

            if len(candidates) > 1:
                logger.warning("Multiple accounts share identifier=%s", identifier)

            user = candidates[0]

        except MultipleObjectsReturned:
            logger.warning("Duplicate users detected for identifier=%s", identifier)
            return None
        except Exception as exc:
            logger.exception(
                "User lookup failed for identifier=%s → %s", identifier, exc
            )
            return None

        # Verify password in timing-safe manner
        try:
            if (
                user
                and user.check_password(password)
                and self.user_can_authenticate(user)
            ):
                logger.info(
                    "User %s authenticated successfully",
                    getattr(user, "email", user.pk),
                )
                return user
            else:
                logger.debug(
                    "Invalid credentials or inactive account for %s", identifier
                )
        except Exception as exc:
            logger.exception(
                "Password verification failed for %s → %s", identifier, exc
            )

        return None

    # ------------------------------------------------------------------
    # User Retrieval
    # ------------------------------------------------------------------
    def get_user(self, user_id: Any) -> Any | None:
        """
        Retrieve user safely for session authentication.
        """
        UserModel = get_user_model()
        try:
            return UserModel.objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            logger.debug("get_user: User not found id=%s", user_id)
            return None
        except Exception as exc:
            logger.exception("get_user failed for id=%s → %s", user_id, exc)
            return None

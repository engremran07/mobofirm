"""Typed runtime configuration: one accessor and one validator.

Who answers "is X on?" is documented in ``apps.core.app_service`` (AppRegistry switch >
app settings row > environment force). This module owns the *environment* side:

* ``env_bool`` / ``env_int`` / ``env_csv`` parse environment variables with the same
  accepted spellings everywhere and raise ``ImproperlyConfigured`` with the variable
  name for a bad value (``TRUSTED_PROXY_COUNT=abc`` used to surface as a bare
  ``ValueError`` from deep inside ``settings``). Settings modules should use them.
* ``runtime_config()`` returns a frozen, typed snapshot of the flags other code
  branches on, read from Django settings (never from ``os.environ`` at request time).
* ``validate_runtime_config()`` returns every configuration problem at once; the
  system checks in ``apps.core.checks`` report them at startup (``manage.py check``,
  ``runserver``, ``migrate``) and under ``check --deploy``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.core.crypto import (
    fernet_key_problem,
    passphrase_problems,
    split_keys,
)

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})
VALID_CROSS_ORIGIN_RESOURCE_POLICIES = frozenset(
    {"same-origin", "same-site", "cross-origin"}
)


def env_bool(
    name: str, default: bool = False, *, environ: Mapping[str, str] | None = None
) -> bool:
    """A boolean environment flag (``1/true/yes/on`` or ``0/false/no/off``)."""
    raw = (environ if environ is not None else os.environ).get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ImproperlyConfigured(
        f"{name} must be one of 1/true/yes/on or 0/false/no/off, not {raw!r}."
    )


def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """An integer environment value, optionally bounded."""
    raw = (environ if environ is not None else os.environ).get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be an integer, not {raw!r}.") from exc
    if minimum is not None and value < minimum:
        raise ImproperlyConfigured(f"{name} must be at least {minimum}, not {value}.")
    if maximum is not None and value > maximum:
        raise ImproperlyConfigured(f"{name} must be at most {maximum}, not {value}.")
    return value


def env_csv(
    name: str,
    default: tuple[str, ...] = (),
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """A comma separated environment list (blank entries dropped)."""
    raw = (environ if environ is not None else os.environ).get(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Typed snapshot of the environment-level flags other code branches on."""

    debug: bool
    maintenance_mode: bool
    require_mfa: bool
    staff_mfa_required: bool
    queue_backend: str
    celery_time_limit: int | None
    celery_soft_time_limit: int | None


def runtime_config() -> RuntimeConfig:
    """The effective environment-level configuration (from Django settings)."""
    return RuntimeConfig(
        debug=bool(settings.DEBUG),
        maintenance_mode=bool(getattr(settings, "MAINTENANCE_MODE", False)),
        require_mfa=bool(getattr(settings, "REQUIRE_MFA", False)),
        staff_mfa_required=bool(getattr(settings, "STAFF_MFA_REQUIRED", True)),
        queue_backend=str(getattr(settings, "QUEUE_BACKEND", "sync")),
        celery_time_limit=getattr(settings, "CELERY_TASK_TIME_LIMIT", None),
        celery_soft_time_limit=getattr(settings, "CELERY_TASK_SOFT_TIME_LIMIT", None),
    )


@dataclass(frozen=True, slots=True)
class ConfigProblem:
    """One configuration problem; ``deploy_only`` ones gate releases, not startup."""

    code: str
    message: str
    hint: str
    fatal: bool = True
    deploy_only: bool = False


def _fernet_list_problems(setting_name: str, raw: str) -> list[ConfigProblem]:
    problems: list[ConfigProblem] = []
    for position, key in enumerate(split_keys(raw), start=1):
        reason = fernet_key_problem(key)
        if reason:
            problems.append(
                ConfigProblem(
                    code="core.E003",
                    message=f"{setting_name} entry {position} is invalid: {reason}.",
                    hint=(
                        "Generate one with cryptography.fernet.Fernet.generate_key(); "
                        "a comma separated list rotates keys (first encrypts, all "
                        "decrypt)."
                    ),
                )
            )
    return problems


def validate_runtime_config(source: Any = settings) -> list[ConfigProblem]:
    """Every runtime-configuration problem in ``source`` (a settings object)."""
    problems: list[ConfigProblem] = []

    hard = getattr(source, "CELERY_TASK_TIME_LIMIT", None)
    soft = getattr(source, "CELERY_TASK_SOFT_TIME_LIMIT", None)
    if hard is None:
        problems.append(
            ConfigProblem(
                code="core.W001",
                message=(
                    "CELERY_TASK_TIME_LIMIT is not set: a stuck task without its own "
                    "time_limit occupies a worker slot forever."
                ),
                hint="Set CELERY_TASK_TIME_LIMIT and CELERY_TASK_SOFT_TIME_LIMIT.",
                fatal=False,
            )
        )
    elif not isinstance(hard, int) or hard < 1:
        problems.append(
            ConfigProblem(
                code="core.E002",
                message=(
                    f"CELERY_TASK_TIME_LIMIT must be a positive integer, not {hard!r}."
                ),
                hint="Use seconds, for example 600.",
            )
        )
    elif soft is not None and (not isinstance(soft, int) or not 0 < soft < hard):
        problems.append(
            ConfigProblem(
                code="core.E002",
                message=(
                    "CELERY_TASK_SOFT_TIME_LIMIT must be a positive integer smaller "
                    f"than CELERY_TASK_TIME_LIMIT ({hard}), not {soft!r}."
                ),
                hint="The soft limit lets a task clean up before the hard kill.",
            )
        )

    corp = getattr(source, "CROSS_ORIGIN_RESOURCE_POLICY", "")
    if corp and corp not in VALID_CROSS_ORIGIN_RESOURCE_POLICIES:
        problems.append(
            ConfigProblem(
                code="core.E006",
                message=(
                    f"CROSS_ORIGIN_RESOURCE_POLICY {corp!r} is not a valid value, so "
                    "the header would not be sent."
                ),
                hint="Use same-site, same-origin or cross-origin (empty turns it off).",
            )
        )

    fernet_key = getattr(source, "FERNET_KEY", "") or ""
    social_key = getattr(source, "SOCIAL_CREDENTIAL_ENCRYPTION_KEY", "") or ""
    mfa_key = getattr(source, "MFA_ENCRYPTION_KEY", "") or ""
    secret_key = getattr(source, "SECRET_KEY", "") or ""

    problems += _fernet_list_problems("FERNET_KEY", fernet_key)
    problems += _fernet_list_problems("SOCIAL_CREDENTIAL_ENCRYPTION_KEY", social_key)

    if mfa_key:
        reasons = passphrase_problems(mfa_key, forbidden=[secret_key])
        problems += [
            ConfigProblem(
                code="core.E004",
                message=f"MFA_ENCRYPTION_KEY is too weak: {reason}.",
                hint="Use at least 32 random characters, separate from SECRET_KEY.",
                deploy_only=True,
            )
            for reason in reasons
        ]

    regimes = {
        "FERNET_KEY": set(split_keys(fernet_key)),
        "SOCIAL_CREDENTIAL_ENCRYPTION_KEY": set(split_keys(social_key)),
    }
    if regimes["FERNET_KEY"] & regimes["SOCIAL_CREDENTIAL_ENCRYPTION_KEY"]:
        problems.append(
            ConfigProblem(
                code="core.E005",
                message=(
                    "FERNET_KEY and SOCIAL_CREDENTIAL_ENCRYPTION_KEY share a key: a "
                    "leak of one would expose both secret stores."
                ),
                hint="Give each purpose its own key.",
                deploy_only=True,
            )
        )
    if (
        mfa_key
        and mfa_key
        in regimes["FERNET_KEY"] | regimes["SOCIAL_CREDENTIAL_ENCRYPTION_KEY"]
    ):
        problems.append(
            ConfigProblem(
                code="core.E005",
                message="MFA_ENCRYPTION_KEY reuses another encryption key.",
                hint="Give each purpose its own key.",
                deploy_only=True,
            )
        )
    return problems


__all__ = [
    "FALSE_VALUES",
    "TRUE_VALUES",
    "VALID_CROSS_ORIGIN_RESOURCE_POLICIES",
    "ConfigProblem",
    "RuntimeConfig",
    "env_bool",
    "env_csv",
    "env_int",
    "runtime_config",
    "validate_runtime_config",
]

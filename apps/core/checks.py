"""Startup system checks for the runtime configuration owned by core.

Registered from ``CoreConfig.ready``. Problems found by
``apps.core.runtime_config.validate_runtime_config`` surface in ``manage.py check``,
``runserver`` and ``migrate`` (always) and as release-gate failures under
``manage.py check --deploy`` (``deploy_only`` problems such as weak or shared keys).
Messages name the setting and never contain key material.
"""

from __future__ import annotations

from typing import Any

from django.core import checks

from apps.core.runtime_config import ConfigProblem, validate_runtime_config


def _as_message(problem: ConfigProblem) -> checks.CheckMessage:
    factory = checks.Error if problem.fatal else checks.Warning
    return factory(problem.message, hint=problem.hint, id=problem.code)


@checks.register(checks.Tags.security)
def check_runtime_config(**_kwargs: Any) -> list[checks.CheckMessage]:
    """Startup: malformed keys and invalid Celery limits are reported immediately."""
    return [
        _as_message(problem)
        for problem in validate_runtime_config()
        if not problem.deploy_only
    ]


@checks.register(checks.Tags.security, deploy=True)
def check_runtime_config_for_deploy(**_kwargs: Any) -> list[checks.CheckMessage]:
    """``check --deploy``: additionally reports weak, shared or reused keys."""
    return [
        _as_message(problem)
        for problem in validate_runtime_config()
        if problem.deploy_only
    ]

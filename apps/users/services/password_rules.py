"""Per-rule password feedback computed from the project's own validators.

``AUTH_PASSWORD_VALIDATORS`` stays the only source of truth: every active
validator becomes one rule, in settings order, and its pass/fail comes from the
validator's own ``validate()`` call. Nothing here re-implements a policy, so the
live checklist on the sign-up, reset, change-password and profile pages cannot
drift from what the server enforces when the form is posted.

The password is only held in memory for the length of one call: it is never
logged, stored or echoed back.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import (
    CommonPasswordValidator,
    MinimumLengthValidator,
    NumericPasswordValidator,
    UserAttributeSimilarityValidator,
    get_default_password_validators,
)
from django.core.exceptions import ValidationError

# Passwords this long are refused before any validator runs (similarity checks are
# quadratic); well above any sensible password.
MAX_PASSWORD_LENGTH: Final[int] = 1024
# Live form values that may be compared with the password, mapped to the user
# attributes the similarity validator reads.
SIMILARITY_FIELDS: Final[tuple[str, ...]] = ("username", "email", "full_name")


@dataclass(frozen=True)
class PasswordRule:
    """One rule of the checklist, derived from one configured validator."""

    id: str
    label: str


@dataclass(frozen=True)
class RuleResult:
    """Outcome of one rule for a typed password.

    ``checked`` is false when the rule had nothing to compare against (for example
    the similarity rule on a form without an email or username); the server still
    applies it when the form is posted.
    """

    id: str
    label: str
    passed: bool
    checked: bool = True
    message: str = ""


def _slug(validator: Any) -> str:
    name = type(validator).__name__.removesuffix("Validator")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def rule_for(validator: Any) -> PasswordRule:
    """The checklist rule describing ``validator``."""
    if isinstance(validator, MinimumLengthValidator):
        return PasswordRule(
            "min_length", f"At least {validator.min_length} characters long"
        )
    if isinstance(validator, NumericPasswordValidator):
        return PasswordRule("numeric", "Not only numbers")
    if isinstance(validator, UserAttributeSimilarityValidator):
        return PasswordRule("similarity", "Not too similar to your email or username")
    if isinstance(validator, CommonPasswordValidator):
        return PasswordRule("common", "Not a commonly used password")
    return PasswordRule(_slug(validator), str(validator.get_help_text()))


def password_rules() -> list[PasswordRule]:
    """The rules for the configured validators, in settings order."""
    return [rule_for(validator) for validator in get_default_password_validators()]


def _similarity_user(
    values: Mapping[str, str], account: Any | None
) -> tuple[Any, bool]:
    """An unsaved user for the similarity validator and whether it has anything to compare.

    The validator reads the model's field metadata, so a real (never saved) instance of
    the user model is used. Live form values win over the signed-in account's saved ones.
    """
    model = get_user_model()
    attributes: dict[str, str] = {}
    for name in ("username", "email", "first_name", "last_name"):
        saved = getattr(account, name, "") if account is not None else ""
        attributes[name] = str(saved or "")
    full_name = values.get("full_name", "").strip()
    if full_name:
        attributes["first_name"], attributes["last_name"] = full_name, ""
    for name in ("username", "email"):
        typed = values.get(name, "").strip()
        if typed:
            attributes[name] = typed
    candidate = model(
        **{key: value for key, value in attributes.items() if hasattr(model, key)}
    )
    return candidate, any(attributes.values())


def evaluate_password(
    password: str, values: Mapping[str, str] | None = None, account: Any | None = None
) -> list[RuleResult]:
    """Run every configured validator separately and report each rule.

    ``values`` are the live form values (``username``, ``email``, ``full_name``);
    ``account`` is the signed-in user whose saved attributes also count.
    """
    similarity_user, has_attributes = _similarity_user(values or {}, account)
    results: list[RuleResult] = []
    for validator in get_default_password_validators():
        rule = rule_for(validator)
        similarity = isinstance(validator, UserAttributeSimilarityValidator)
        try:
            validator.validate(password, similarity_user if similarity else None)
        except ValidationError as error:
            results.append(
                RuleResult(rule.id, rule.label, False, message=" ".join(error.messages))
            )
        else:
            checked = has_attributes or not similarity
            results.append(RuleResult(rule.id, rule.label, True, checked=checked))
    return results

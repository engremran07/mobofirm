"""Typed environment parsing, runtime-configuration validation and key rotation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet, InvalidToken
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.core.checks import check_runtime_config, check_runtime_config_for_deploy
from apps.core.runtime_config import (
    env_bool,
    env_csv,
    env_int,
    runtime_config,
    validate_runtime_config,
)
from apps.core.utils.crypto_keys import (
    derive_fernet,
    fernet_key_problem,
    multi_fernet,
    multi_fernet_from_passphrases,
    passphrase_problems,
    rotate_token,
    split_keys,
)

STRONG_PASSPHRASE = "q7Zk-Lm3vN8xR2tYb5Wc9HdPf4GjS6aE"


def settings_like(**values):
    base = {
        "CELERY_TASK_TIME_LIMIT": 600,
        "CELERY_TASK_SOFT_TIME_LIMIT": 540,
        "FERNET_KEY": Fernet.generate_key().decode(),
        "SOCIAL_CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "MFA_ENCRYPTION_KEY": STRONG_PASSPHRASE,
        "SECRET_KEY": "s" * 60,
    }
    return SimpleNamespace(**{**base, **values})


def codes(problems):
    return sorted(problem.code for problem in problems)


# --- environment parsing -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("TRUE", True),
        (" yes ", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
        ("", False),
    ],
)
def test_env_bool_accepts_the_documented_spellings(raw, expected):
    assert env_bool("FLAG", environ={"FLAG": raw}) is expected


def test_env_bool_uses_the_default_when_unset_and_rejects_garbage():
    assert env_bool("FLAG", True, environ={}) is True
    assert env_bool("FLAG", environ={}) is False
    with pytest.raises(ImproperlyConfigured, match="FLAG"):
        env_bool("FLAG", environ={"FLAG": "maybe"})


def test_env_int_is_bounded_and_names_the_variable():
    assert env_int("N", 3, environ={}) == 3
    assert env_int("N", 3, environ={"N": " 7 "}) == 7
    assert env_int("N", 3, environ={"N": ""}) == 3
    with pytest.raises(ImproperlyConfigured, match="N must be an integer"):
        env_int("N", 3, environ={"N": "abc"})
    with pytest.raises(ImproperlyConfigured, match="at least 1"):
        env_int("N", 3, minimum=1, environ={"N": "0"})
    with pytest.raises(ImproperlyConfigured, match="at most 5"):
        env_int("N", 3, maximum=5, environ={"N": "6"})


def test_env_csv_drops_blank_entries():
    assert env_csv("L", environ={"L": " a, b ,,c "}) == ("a", "b", "c")
    assert env_csv("L", ("x",), environ={}) == ("x",)


# --- validation ----------------------------------------------------------------


def test_a_complete_configuration_has_no_problems():
    assert validate_runtime_config(settings_like()) == []


def test_a_missing_global_time_limit_is_a_warning_not_a_startup_error():
    problems = validate_runtime_config(settings_like(CELERY_TASK_TIME_LIMIT=None))

    assert codes(problems) == ["core.W001"]
    assert problems[0].fatal is False


@pytest.mark.parametrize(
    "values",
    [
        {"CELERY_TASK_TIME_LIMIT": 0},
        {"CELERY_TASK_TIME_LIMIT": "600"},
        {"CELERY_TASK_SOFT_TIME_LIMIT": 600},
        {"CELERY_TASK_SOFT_TIME_LIMIT": 900},
        {"CELERY_TASK_SOFT_TIME_LIMIT": -1},
    ],
)
def test_invalid_celery_limits_are_fatal(values):
    problems = validate_runtime_config(settings_like(**values))

    assert codes(problems) == ["core.E002"]
    assert problems[0].fatal is True


def test_malformed_fernet_keys_are_fatal_and_never_echo_the_key():
    secret_looking = "not-a-key-supersecret"
    problems = validate_runtime_config(
        settings_like(
            FERNET_KEY=f"{Fernet.generate_key().decode()},{secret_looking}",
            SOCIAL_CREDENTIAL_ENCRYPTION_KEY=secret_looking + "-2",
        )
    )

    assert codes(problems) == ["core.E003", "core.E003"]
    messages = " ".join(problem.message + problem.hint for problem in problems)
    assert "FERNET_KEY entry 2" in messages
    assert "SOCIAL_CREDENTIAL_ENCRYPTION_KEY entry 1" in messages
    assert secret_looking not in messages


def test_weak_mfa_keys_gate_deploys_not_startup():
    problems = validate_runtime_config(settings_like(MFA_ENCRYPTION_KEY="short"))

    assert {problem.code for problem in problems} == {"core.E004"}
    assert all(problem.deploy_only for problem in problems)


def test_an_mfa_key_equal_to_the_secret_key_is_rejected():
    problems = validate_runtime_config(settings_like(SECRET_KEY=STRONG_PASSPHRASE))

    assert any("identical" in problem.message for problem in problems)


def test_keys_must_not_be_shared_between_purposes():
    shared = Fernet.generate_key().decode()

    problems = validate_runtime_config(
        settings_like(FERNET_KEY=shared, SOCIAL_CREDENTIAL_ENCRYPTION_KEY=shared)
    )
    reused_mfa = validate_runtime_config(
        settings_like(FERNET_KEY=shared, MFA_ENCRYPTION_KEY=shared)
    )

    assert codes(problems) == ["core.E005"]
    assert codes(reused_mfa) == ["core.E005"]


def test_unset_keys_are_not_a_startup_problem_production_settings_require_them():
    assert not validate_runtime_config(
        settings_like(FERNET_KEY="", MFA_ENCRYPTION_KEY="")
    )


def test_an_invalid_cross_origin_resource_policy_is_reported_not_ignored():
    problems = validate_runtime_config(
        settings_like(CROSS_ORIGIN_RESOURCE_POLICY="typo")
    )

    assert codes(problems) == ["core.E006"]
    assert not validate_runtime_config(settings_like(CROSS_ORIGIN_RESOURCE_POLICY=""))
    assert not validate_runtime_config(
        settings_like(CROSS_ORIGIN_RESOURCE_POLICY="same-origin")
    )


# --- system checks -------------------------------------------------------------


@override_settings(CELERY_TASK_TIME_LIMIT=None, FERNET_KEY="garbage")
def test_the_startup_check_reports_errors_and_warnings():
    messages = check_runtime_config()

    by_id = {message.id: message for message in messages}
    assert isinstance(by_id["core.E003"], checks.Error)
    assert isinstance(by_id["core.W001"], checks.Warning)
    assert "garbage" not in by_id["core.E003"].msg


@override_settings(MFA_ENCRYPTION_KEY="weak")
def test_the_deploy_check_adds_the_strength_rules_only():
    startup_ids = {message.id for message in check_runtime_config()}
    deploy_ids = {message.id for message in check_runtime_config_for_deploy()}

    assert "core.E004" in deploy_ids
    assert "core.E004" not in startup_ids


def test_the_checks_are_registered_with_django():
    registered = checks.registry.registry.get_checks(include_deployment_checks=True)

    assert check_runtime_config in registered
    assert check_runtime_config_for_deploy in registered


@override_settings(
    DEBUG=False,
    MAINTENANCE_MODE=True,
    REQUIRE_MFA=True,
    CELERY_TASK_TIME_LIMIT=300,
    CELERY_TASK_SOFT_TIME_LIMIT=240,
)
def test_the_typed_snapshot_reads_django_settings():
    config = runtime_config()

    assert config.maintenance_mode is True
    assert config.require_mfa is True
    assert (config.celery_time_limit, config.celery_soft_time_limit) == (300, 240)
    assert config.debug is False


# --- key handling and rotation -------------------------------------------------


def test_key_lists_are_split_and_trimmed():
    assert split_keys(" a , b ,, c ") == ["a", "b", "c"]
    assert split_keys("") == []
    assert split_keys(None) == []


def test_fernet_key_problems_are_described_without_the_key():
    assert fernet_key_problem(Fernet.generate_key().decode()) is None
    assert "Fernet key" in (fernet_key_problem("nope") or "")
    assert "Fernet key" in (fernet_key_problem("é" * 44) or "")


def test_passphrase_strength_rules():
    assert not passphrase_problems(STRONG_PASSPHRASE)
    assert len(passphrase_problems("short")) == 2
    assert passphrase_problems(STRONG_PASSPHRASE, forbidden=[STRONG_PASSPHRASE])


def test_rotation_keeps_old_ciphertext_readable_and_reencrypts_under_the_new_key():
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    token = multi_fernet(old_key, setting_name="K").encrypt(b"secret").decode()

    rotated_reader = multi_fernet(f"{new_key},{old_key}", setting_name="K")
    assert rotated_reader.decrypt(token.encode()) == b"secret"

    migrated = rotate_token(rotated_reader, token)
    assert (
        multi_fernet(new_key, setting_name="K").decrypt(migrated.encode()) == b"secret"
    )
    with pytest.raises(InvalidToken):
        multi_fernet(old_key, setting_name="K").decrypt(migrated.encode())

    # Idempotent: a restarted re-encrypt job may rotate an already-current token.
    again = rotate_token(rotated_reader, migrated)
    assert multi_fernet(new_key, setting_name="K").decrypt(again.encode()) == b"secret"


def test_new_ciphertext_uses_the_first_key():
    first = Fernet.generate_key().decode()
    second = Fernet.generate_key().decode()
    token = multi_fernet(f"{first},{second}", setting_name="K").encrypt(b"x")

    assert Fernet(first.encode()).decrypt(token) == b"x"


def test_unset_or_malformed_key_lists_raise_a_clear_error():
    with pytest.raises(ImproperlyConfigured, match="FERNET_KEY must be set"):
        multi_fernet("", setting_name="FERNET_KEY")
    with pytest.raises(ImproperlyConfigured, match="FERNET_KEY entry 2 is invalid"):
        multi_fernet(f"{Fernet.generate_key().decode()},bad", setting_name="FERNET_KEY")


def test_passphrase_derivation_matches_the_mfa_regime_and_rotates():
    info = b"gsmfws-mfa-secret-v1"
    old = derive_fernet("old-" + STRONG_PASSPHRASE, info=info)
    token = old.encrypt(b"totp-secret").decode()

    cipher = multi_fernet_from_passphrases(
        ["new-" + STRONG_PASSPHRASE, "old-" + STRONG_PASSPHRASE],
        info=info,
        setting_name="MFA_ENCRYPTION_KEY",
    )

    assert cipher.decrypt(token.encode()) == b"totp-secret"
    with pytest.raises(ImproperlyConfigured):
        multi_fernet_from_passphrases([], info=info, setting_name="MFA_ENCRYPTION_KEY")

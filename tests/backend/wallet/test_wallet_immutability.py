"""Ledger immutability (F-020): model guards, counter guard, CHECKs, trigger migration."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.test import override_settings

from apps.wallet import services
from apps.wallet.models import (
    LedgerEntry,
    LedgerReason,
    PayoutRequest,
    TopUpRequest,
    Wallet,
    ledger_posting,
)

pytestmark = pytest.mark.django_db


def new_entry(wallet: Wallet, **fields: Any) -> LedgerEntry:
    values: dict[str, Any] = {
        "wallet": wallet,
        "direction": "credit",
        "reason": LedgerReason.TOPUP,
        "amount": 5,
        "balance_after": 5,
        "key": "entry:default",
    }
    values.update(fields)
    return LedgerEntry(**values)


def test_saving_a_new_object_with_an_existing_primary_key_cannot_overwrite_a_row(
    user: Any,
) -> None:
    original = services.credit(user, 5, reason=LedgerReason.TOPUP, key="pk:1")
    forged = new_entry(original.wallet, key="pk:forged", amount=999, id=original.pk)

    with pytest.raises(IntegrityError), transaction.atomic():
        forged.save()

    assert LedgerEntry.objects.get(pk=original.pk).amount == 5
    assert not LedgerEntry.objects.filter(key="pk:forged").exists()


def test_bulk_writes_to_the_ledger_are_refused(user: Any) -> None:
    entry = services.credit(user, 5, reason=LedgerReason.TOPUP, key="bulk:1")

    with pytest.raises(TypeError):
        LedgerEntry.objects.bulk_create([new_entry(entry.wallet, key="bulk:2")])
    with pytest.raises(TypeError):
        LedgerEntry.objects.bulk_create(
            [new_entry(entry.wallet, key="bulk:1")], update_conflicts=True
        )
    with pytest.raises(TypeError):
        LedgerEntry.objects.bulk_update([entry], ["amount"])
    with pytest.raises(TypeError):
        LedgerEntry.objects.update_or_create(key="bulk:1", defaults={"amount": 1})
    assert list(LedgerEntry.objects.values_list("key", flat=True)) == ["bulk:1"]
    assert LedgerEntry.objects.get(key="bulk:1").amount == 5


def test_only_personal_columns_can_be_redacted(user: Any, staff: Any) -> None:
    entry = services.adjust(
        user, 5, actor=staff, reason_text="Paid to a@b.example", key="redact:1"
    )

    with pytest.raises(TypeError):
        LedgerEntry.objects.filter(pk=entry.pk).redact(amount=1)
    with pytest.raises(TypeError):
        LedgerEntry.objects.filter(pk=entry.pk).redact(balance_after=0)
    changed = LedgerEntry.objects.filter(pk=entry.pk).redact(note="", actor=None)

    entry.refresh_from_db()
    assert (changed, entry.note, entry.actor_id, entry.amount) == (1, "", None, 5)


def test_wallet_counters_cannot_be_written_outside_a_posting(user: Any) -> None:
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="guard:1")
    wallet = Wallet.objects.get(user=user)

    with pytest.raises(TypeError):
        Wallet.objects.filter(pk=wallet.pk).update(balance=999)
    with pytest.raises(TypeError):
        Wallet.objects.filter(pk=wallet.pk).update(held=1)
    with pytest.raises(TypeError):
        Wallet.objects.bulk_update([wallet], ["balance"])
    wallet.balance = 999
    with pytest.raises(TypeError):
        wallet.save()
    with pytest.raises(TypeError):
        wallet.save(update_fields=["balance", "updated_at"])

    assert Wallet.objects.get(pk=wallet.pk).balance == 50


def test_a_stale_full_save_cannot_write_old_counters_back(user: Any) -> None:
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="stale:1")
    stale = Wallet.objects.get(user=user)
    services.credit(user, 25, reason=LedgerReason.TOPUP, key="stale:2")

    with pytest.raises(TypeError):
        stale.save()

    assert services.balance(user) == 75


def test_other_wallet_columns_stay_writable(user: Any, other: Any) -> None:
    wallet = services.get_wallet(user)

    Wallet.objects.filter(pk=wallet.pk).update(is_frozen=True)
    wallet.refresh_from_db()
    wallet.frozen_reason = "Review"
    wallet.save(update_fields=["frozen_reason", "updated_at"])
    Wallet.objects.bulk_update([wallet], ["is_frozen"])

    assert services.get_wallet(other).balance == 0
    assert Wallet.objects.get(pk=wallet.pk).frozen_reason == "Review"


def test_a_wallet_cannot_be_created_with_credits_that_have_no_ledger_row(
    user: Any,
) -> None:
    with pytest.raises(TypeError):
        Wallet.objects.create(user=user, balance=100)
    with pytest.raises(TypeError):
        Wallet(user=user, held=1).save()

    assert not Wallet.objects.filter(user=user).exists()
    assert Wallet.objects.create(user=user).balance == 0


def test_the_posting_window_closes_again(user: Any) -> None:
    wallet = services.get_wallet(user)

    with ledger_posting():
        Wallet.objects.filter(pk=wallet.pk).update(balance=0)
    with pytest.raises(TypeError):
        Wallet.objects.filter(pk=wallet.pk).update(balance=0)


def test_the_database_refuses_negative_or_over_held_balances(user: Any) -> None:
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="neg:1")
    wallet = Wallet.objects.get(user=user)

    with pytest.raises(IntegrityError), transaction.atomic(), ledger_posting():
        Wallet.objects.filter(pk=wallet.pk).update(balance=-1)
    with pytest.raises(IntegrityError), transaction.atomic(), ledger_posting():
        Wallet.objects.filter(pk=wallet.pk).update(held=51)

    assert Wallet.objects.get(pk=wallet.pk).balance == 50


@pytest.mark.parametrize(
    "fields",
    [
        {"direction": "refund"},
        {"amount": 0},
        {"key": ""},
    ],
)
def test_the_database_refuses_malformed_ledger_rows(
    user: Any, fields: dict[str, Any]
) -> None:
    wallet = services.get_wallet(user)

    with pytest.raises(IntegrityError), transaction.atomic():
        new_entry(wallet, **{"key": "check:1", **fields}).save()

    assert not LedgerEntry.objects.exists()


def test_the_database_refuses_negative_money_on_requests(user: Any) -> None:
    wallet = services.get_wallet(user)

    with pytest.raises(IntegrityError), transaction.atomic():
        TopUpRequest.objects.create(
            user=user, credits=10, price=-1, currency_label="USD"
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        PayoutRequest.objects.create(
            wallet=wallet,
            credits=10,
            payout_amount=-1,
            currency_label="USD",
            method="bank",
            destination="x",
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        PayoutRequest.objects.create(
            wallet=wallet,
            credits=10,
            payout_amount=1,
            fee_percent=101,
            currency_label="USD",
            method="bank",
            destination="x",
        )


def test_the_migrations_match_the_models() -> None:
    # Read the real migration files even when the suite runs with --no-migrations.
    with override_settings(MIGRATION_MODULES={}):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        detector = MigrationAutodetector(
            loader.project_state(), ProjectState.from_apps(django_apps)
        )
        changes = detector.changes(graph=loader.graph)

    assert "wallet" not in changes
    assert loader.graph.leaf_nodes("wallet") == [("wallet", "0003_ledger_integrity")]


class RecordingEditor:
    """Stands in for a schema editor and records the SQL it is asked to run."""

    def __init__(self, vendor: str) -> None:
        self.connection = SimpleNamespace(vendor=vendor)
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(" ".join(sql.split()))


def test_the_ledger_trigger_is_installed_and_removed_on_postgresql_only() -> None:
    migration = import_module("apps.wallet.migrations.0003_ledger_integrity")
    sqlite = RecordingEditor("sqlite")
    postgres = RecordingEditor("postgresql")

    migration.install_ledger_guard(None, sqlite)
    migration.remove_ledger_guard(None, sqlite)
    migration.install_ledger_guard(None, postgres)
    installed = list(postgres.statements)
    postgres.statements.clear()
    migration.remove_ledger_guard(None, postgres)

    assert sqlite.statements == []
    assert "CREATE OR REPLACE FUNCTION wallet_ledger_guard()" in installed[0]
    assert "append-only" in installed[0]
    assert any(
        "CREATE TRIGGER wallet_ledger_no_update BEFORE UPDATE ON wallet_ledgerentry"
        in sql
        for sql in installed
    )
    assert any(
        "CREATE TRIGGER wallet_ledger_no_delete BEFORE DELETE ON wallet_ledgerentry"
        in sql
        for sql in installed
    )
    assert postgres.statements[-1] == "DROP FUNCTION IF EXISTS wallet_ledger_guard()"
    assert sum("DROP TRIGGER IF EXISTS" in sql for sql in postgres.statements) == 2

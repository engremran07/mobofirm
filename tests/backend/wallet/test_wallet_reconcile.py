"""Wallet reconciliation (F-020): drift reports, the audited repair, command and task."""

from __future__ import annotations

import logging
from dataclasses import replace
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.wallet import reconcile, services, tasks
from apps.wallet.models import (
    EscrowHold,
    LedgerEntry,
    LedgerReason,
    Wallet,
    WalletRepair,
    ledger_posting,
)
from tests.backend.wallet.conftest import tamper_with_ledger

pytestmark = pytest.mark.django_db


def corrupt(wallet: Wallet, **counters: int) -> None:
    """Simulate a manual SQL fix that changed a counter without a ledger row."""
    with ledger_posting():
        Wallet.objects.filter(pk=wallet.pk).update(**counters)


def raw_sql(sql: str, params: list[Any]) -> None:
    """Simulate tampering below the ORM (a database console)."""
    tamper_with_ledger(sql, params)


def funded(user: Any, amount: int = 100, key: str = "fund") -> Wallet:
    services.credit(user, amount, reason=LedgerReason.TOPUP, key=key)
    return Wallet.objects.get(user=user)


def test_consistent_wallets_report_clean(user: Any, other: Any) -> None:
    funded(user)
    funded(other, 40, key="fund:other")
    services.spend(user, "test.package", key="spend:1")

    report = reconcile.reconcile()

    assert report.consistent
    assert (report.wallets_checked, report.drifts, report.complete) == (2, (), True)


def test_a_balance_drift_is_reported_with_both_numbers_and_never_fixed(
    user: Any, other: Any
) -> None:
    wallet = funded(user)
    funded(other, 10, key="fund:other")
    corrupt(wallet, balance=150)

    report = reconcile.reconcile()

    assert not report.consistent
    assert [(d.wallet_id, d.field, d.expected, d.actual) for d in report.drifts] == [
        (wallet.pk, "balance", 100, 150),
        (wallet.pk, "last_balance_after", 150, 100),
    ]
    assert Wallet.objects.get(pk=wallet.pk).balance == 150
    assert report.summary() == {
        "wallets_checked": 2,
        "drift_count": 2,
        "drifted_wallets": 1,
        "complete": True,
    }


def test_held_credits_are_checked_against_the_open_escrow_holds(user: Any) -> None:
    wallet = funded(user)
    services.lock_escrow(user, 30, key="bounty:1")
    assert reconcile.reconcile().consistent

    corrupt(wallet, held=10)
    drifts = reconcile.reconcile().drifts

    assert [(d.field, d.expected, d.actual) for d in drifts] == [("held", 30, 10)]
    corrupt(wallet, held=30)
    services.cancel_escrow(EscrowHold.objects.get(key="bounty:1"))
    assert reconcile.reconcile(wallet_ids=[wallet.pk]).consistent


def test_a_tampered_last_balance_after_is_a_drift(user: Any) -> None:
    wallet = funded(user)
    raw_sql("UPDATE wallet_ledgerentry SET balance_after = %s", [7])

    drifts = reconcile.reconcile().drifts

    assert [(d.field, d.expected, d.actual) for d in drifts] == [
        ("last_balance_after", 100, 7)
    ]
    assert drifts[0].wallet_id == wallet.pk


def test_the_deep_check_finds_a_broken_chain_the_totals_cannot_see(user: Any) -> None:
    funded(user, 100, key="chain:1")
    services.credit(user, 20, reason=LedgerReason.TOPUP, key="chain:2")
    services.credit(user, 5, reason=LedgerReason.TOPUP, key="chain:3")
    first = LedgerEntry.objects.get(key="chain:1")
    raw_sql(
        "UPDATE wallet_ledgerentry SET balance_after = %s WHERE id = %s", [90, first.pk]
    )

    assert reconcile.reconcile().consistent
    deep = reconcile.reconcile(deep=True).drifts

    assert [(d.field, d.expected, d.actual) for d in deep] == [
        ("ledger_chain", 100, 90)
    ]


def test_a_posting_that_commits_between_the_batch_queries_is_not_reported(
    user: Any,
) -> None:
    funded(user)
    real = reconcile.batch_ledger_truth

    def stale_batch(ids: list[int]) -> dict[int, reconcile.LedgerTruth]:
        # The ledger totals were read before a posting that the wallet row now includes.
        return {
            wallet_id: replace(truth, balance=truth.balance - 40)
            for wallet_id, truth in real(ids).items()
        }

    with patch.object(reconcile, "batch_ledger_truth", stale_batch):
        report = reconcile.reconcile()

    assert report.consistent
    assert report.wallets_checked == 1


def test_a_run_out_of_time_is_reported_incomplete_not_clean(user: Any) -> None:
    funded(user)

    report = reconcile.reconcile(max_seconds=-1)

    assert (report.complete, report.consistent, report.wallets_checked) == (
        False,
        False,
        0,
    )


def test_batches_cover_every_wallet_and_filters_narrow_the_run(
    make_user: Any,
) -> None:
    wallets = [funded(make_user(f"m{n}"), 10, key=f"b:{n}") for n in range(5)]
    corrupt(wallets[3], balance=11)

    everything = reconcile.reconcile(batch_size=2)
    only_one = reconcile.reconcile(wallet_ids=[wallets[0].pk], batch_size=2)

    assert everything.wallets_checked == 5
    assert {d.wallet_id for d in everything.drifts} == {wallets[3].pk}
    assert only_one.wallets_checked == 1 and only_one.consistent


def test_repair_rewrites_the_counters_from_the_ledger_with_an_audit_row(
    user: Any, staff: Any
) -> None:
    wallet = funded(user)
    services.lock_escrow(user, 30, key="bounty:r")
    corrupt(wallet, balance=140, held=0)
    entries_before = LedgerEntry.objects.count()

    plan = reconcile.plan_repair(wallet.pk)
    record = reconcile.repair_wallet(
        wallet.pk, actor=staff, reason_text="Manual SQL on 11 Nov", key="repair:1"
    )

    assert (plan.previous_balance, plan.repaired_balance) == (140, 100)
    assert (plan.previous_held, plan.repaired_held) == (0, 30)
    fixed = Wallet.objects.get(pk=wallet.pk)
    assert (fixed.balance, fixed.held) == (100, 30)
    assert (record.actor_id, record.reason, record.wallet_id) == (
        staff.pk,
        "Manual SQL on 11 Nov",
        wallet.pk,
    )
    assert (record.previous_balance, record.repaired_balance) == (140, 100)
    assert LedgerEntry.objects.count() == entries_before
    assert reconcile.reconcile().consistent


def test_repair_is_idempotent_on_its_key_and_refuses_a_clean_wallet(
    user: Any, other: Any, staff: Any
) -> None:
    wallet = funded(user)
    corrupt(wallet, balance=5)

    first = reconcile.repair_wallet(
        wallet.pk, actor=staff, reason_text="Fix", key="repair:dup"
    )
    again = reconcile.repair_wallet(
        wallet.pk, actor=staff, reason_text="Fix", key="repair:dup"
    )

    assert again.pk == first.pk
    assert WalletRepair.objects.count() == 1
    with pytest.raises(reconcile.RepairRefused):
        reconcile.repair_wallet(
            wallet.pk, actor=staff, reason_text="Fix", key="repair:again"
        )
    other_wallet = funded(other, 10, key="fund:o")
    with pytest.raises(services.WalletError):
        reconcile.repair_wallet(
            other_wallet.pk, actor=staff, reason_text="Fix", key="repair:dup"
        )


def test_repair_needs_the_permission_a_reason_and_a_key(
    user: Any, other: Any, staff: Any
) -> None:
    wallet = funded(user)
    corrupt(wallet, balance=5)

    with pytest.raises(PermissionDenied):
        reconcile.repair_wallet(
            wallet.pk, actor=other, reason_text="Fix", key="repair:p"
        )
    with pytest.raises(services.WalletError):
        reconcile.repair_wallet(wallet.pk, actor=staff, reason_text=" ", key="repair:r")
    with pytest.raises(services.WalletError):
        reconcile.repair_wallet(wallet.pk, actor=staff, reason_text="Fix", key="")
    with pytest.raises(reconcile.RepairRefused):
        reconcile.repair_wallet(999999, actor=staff, reason_text="Fix", key="repair:n")

    assert Wallet.objects.get(pk=wallet.pk).balance == 5
    assert not WalletRepair.objects.exists()


def test_a_ledger_that_is_itself_wrong_is_never_papered_over(
    user: Any, staff: Any
) -> None:
    wallet = funded(user, 50)
    raw_sql("UPDATE wallet_ledgerentry SET direction = %s", ["debit"])

    assert reconcile.reconcile().drifts
    with pytest.raises(reconcile.RepairRefused, match="inconsistent"):
        reconcile.repair_wallet(
            wallet.pk, actor=staff, reason_text="Try", key="repair:neg"
        )
    assert Wallet.objects.get(pk=wallet.pk).balance == 50


def test_the_repair_audit_record_is_append_only(user: Any, staff: Any) -> None:
    wallet = funded(user)
    corrupt(wallet, balance=5)
    record = reconcile.repair_wallet(
        wallet.pk, actor=staff, reason_text="Fix", key="repair:ao"
    )

    record.reason = "edited"
    with pytest.raises(TypeError):
        record.save()
    with pytest.raises(TypeError):
        record.delete()
    with pytest.raises(TypeError):
        WalletRepair.objects.all().update(reason="x")
    with pytest.raises(TypeError):
        WalletRepair.objects.all().delete()


def test_the_reconcile_command_reports_and_never_changes_money(
    user: Any,
) -> None:
    wallet = funded(user)
    corrupt(wallet, balance=130)
    out = StringIO()

    call_command("reconcile_wallets", stdout=out)

    text = out.getvalue()
    assert "Checked 1 wallet(s): 2 drift(s)." in text
    assert f"wallet {wallet.pk}" in text and "balance should be 100 but is 130" in text
    assert Wallet.objects.get(pk=wallet.pk).balance == 130
    with pytest.raises(CommandError, match="nothing was changed"):
        call_command("reconcile_wallets", "--fail-on-drift", stdout=StringIO())


def test_the_reconcile_command_is_quiet_about_a_clean_ledger(user: Any) -> None:
    funded(user)
    out = StringIO()

    call_command("reconcile_wallets", "--fail-on-drift", "--deep", stdout=out)

    assert out.getvalue().strip() == "Checked 1 wallet(s): 0 drift(s)."


def test_the_repair_command_is_a_dry_run_until_applied(user: Any, staff: Any) -> None:
    wallet = funded(user)
    corrupt(wallet, balance=130)
    out = StringIO()

    call_command("repair_wallet", str(wallet.pk), stdout=out)
    assert "Dry run: nothing was changed" in out.getvalue()
    assert Wallet.objects.get(pk=wallet.pk).balance == 130

    with pytest.raises(CommandError, match="reason is required"):
        call_command(
            "repair_wallet",
            str(wallet.pk),
            "--apply",
            f"--actor={staff.email}",
            "--key=cmd:1",
            stdout=StringIO(),
        )
    with pytest.raises(CommandError, match="existing user"):
        call_command(
            "repair_wallet", str(wallet.pk), "--apply", "--actor=nobody", stdout=out
        )
    applied = StringIO()
    call_command(
        "repair_wallet",
        str(wallet.pk),
        "--apply",
        f"--actor={staff.email}",
        "--reason=Counter drift",
        "--key=cmd:1",
        stdout=applied,
    )

    assert "Repaired; audit record #" in applied.getvalue()
    assert Wallet.objects.get(pk=wallet.pk).balance == 100
    with pytest.raises(CommandError, match="already match"):
        call_command("repair_wallet", str(wallet.pk), stdout=StringIO())


def test_the_nightly_task_alerts_with_structured_fields_on_drift(
    user: Any, caplog: pytest.LogCaptureFixture
) -> None:
    wallet = funded(user)
    corrupt(wallet, balance=130)

    with caplog.at_level(logging.INFO, logger="apps.wallet.reconcile"):
        result = tasks.reconcile_wallets.apply().get()

    assert result["drift_count"] == 2 and result["complete"] is True
    alerts = [
        vars(r)
        for r in caplog.records
        if getattr(r, "alert", "") == "wallet_reconcile_drift"
    ]
    assert alerts and all(a["levelno"] == logging.ERROR for a in alerts)
    assert (alerts[0]["wallet_id"], alerts[0]["field"]) == (wallet.pk, "balance")
    assert (alerts[0]["expected"], alerts[0]["actual"]) == (100, 130)
    assert any(
        getattr(r, "alert", "") == "wallet_reconcile_drift_summary"
        for r in caplog.records
    )


def test_the_nightly_task_logs_a_clean_run_at_info(
    user: Any, caplog: pytest.LogCaptureFixture
) -> None:
    funded(user)

    with caplog.at_level(logging.INFO, logger="apps.wallet.reconcile"):
        result = tasks.reconcile_wallets.apply().get()

    assert result == {
        "wallets_checked": 1,
        "drift_count": 0,
        "drifted_wallets": 0,
        "complete": True,
    }
    assert not [
        r
        for r in caplog.records
        if r.name.startswith("apps.wallet") and r.levelno >= logging.WARNING
    ]


def test_the_nightly_task_has_time_limits_and_is_registered_with_celery() -> None:
    from app.celery import app

    app.loader.import_default_modules()
    task = tasks.reconcile_wallets

    assert task.name == "apps.wallet.tasks.reconcile_wallets"
    assert task.name in app.tasks
    assert (task.soft_time_limit, task.time_limit) == (
        tasks.SOFT_TIME_LIMIT,
        tasks.HARD_TIME_LIMIT,
    )
    assert 0 < tasks.RUN_BUDGET_SECONDS < tasks.SOFT_TIME_LIMIT < tasks.HARD_TIME_LIMIT
    assert task.acks_late is True


def test_a_soft_time_limit_is_logged_as_an_alert_not_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch.object(tasks, "reconcile", side_effect=SoftTimeLimitExceeded()),
        caplog.at_level(logging.ERROR, logger="apps.wallet.tasks"),
    ):
        result = tasks.reconcile_wallets.apply().get()

    assert result == {"complete": False, "timed_out": True}
    assert [
        getattr(r, "alert", "") for r in caplog.records if r.name == "apps.wallet.tasks"
    ] == ["wallet_reconcile_timeout"]

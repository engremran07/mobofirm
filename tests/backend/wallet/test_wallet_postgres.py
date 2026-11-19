"""Real parallel transactions and the ledger trigger (PostgreSQL only).

Row locks (``SELECT ... FOR UPDATE``) and triggers do not exist on the SQLite the
default suite runs on, so these tests need ``DATABASES`` to be PostgreSQL (run them with
a throwaway database; the deterministic twins live in ``test_wallet_concurrency.py``).
They are skipped on any other backend, and only on that ground.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures.thread import ThreadPoolExecutor
from functools import partial
from importlib import import_module
from threading import Barrier
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction

from apps.wallet import reconcile, services
from apps.wallet.erasure import anonymise_user
from apps.wallet.models import (
    LedgerEntry,
    LedgerReason,
    RewardRule,
    Wallet,
    ledger_posting,
)
from apps.wallet.services import (
    IdempotencyConflict,
    InsufficientCredits,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="needs PostgreSQL row locks and triggers",
    ),
]

EVENT = "test.contribution"
JOIN_SECONDS = 60


def parallel(jobs: list[Callable[[], Any]]) -> list[Any]:
    """Run the jobs at the same moment on their own connections; results or errors."""
    barrier = Barrier(len(jobs))

    def run(job: Callable[[], Any]) -> Any:
        try:
            barrier.wait(timeout=JOIN_SECONDS)
            return job()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(run, job) for job in jobs]
        return [
            future.exception(timeout=JOIN_SECONDS) or future.result()
            for future in futures
        ]


def fund(user: Any, amount: int, key: str = "fund") -> None:
    services.credit(user, amount, reason=LedgerReason.TOPUP, key=key)


def raw_sql(sql: str, params: list[Any] | None = None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


def test_parallel_debits_never_overdraw_the_wallet(user: Any) -> None:
    fund(user, 100)

    outcomes = parallel(
        [
            partial(services.debit, user, 30, reason=LedgerReason.SPEND, key=f"par:{n}")
            for n in range(8)
        ]
    )

    paid = [o for o in outcomes if isinstance(o, LedgerEntry)]
    refused = [o for o in outcomes if isinstance(o, InsufficientCredits)]
    assert len(paid) == 3 and len(refused) == 5
    assert services.balance(user) == 10
    assert reconcile.reconcile(deep=True).consistent


def test_one_delivered_many_times_at_once_books_a_single_entry(user: Any) -> None:
    outcomes = parallel(
        [
            partial(
                services.credit,
                user,
                50,
                reason=LedgerReason.TOPUP,
                key="dup:parallel",
            )
            for _ in range(8)
        ]
    )

    assert all(isinstance(o, LedgerEntry) for o in outcomes)
    assert len({o.pk for o in outcomes}) == 1
    assert LedgerEntry.objects.filter(key="dup:parallel").count() == 1
    assert services.balance(user) == 50


def test_one_key_raced_by_two_wallets_has_exactly_one_winner(
    user: Any, other: Any
) -> None:
    outcomes = parallel(
        [
            partial(
                services.credit, owner, 5, reason=LedgerReason.TOPUP, key="race:shared"
            )
            for owner in (user, other)
        ]
    )

    assert sum(isinstance(o, LedgerEntry) for o in outcomes) == 1
    assert sum(isinstance(o, IdempotencyConflict) for o in outcomes) == 1
    assert LedgerEntry.objects.filter(key="race:shared").count() == 1
    assert reconcile.reconcile().consistent


def test_releases_in_opposite_directions_do_not_deadlock(user: Any, other: Any) -> None:
    fund(user, 1000)
    fund(other, 1000, key="fund:other")
    services.sync_registry()
    rounds = 6
    holds = [
        (
            services.lock_escrow(user, 10, key=f"swap:a:{n}"),
            services.lock_escrow(other, 10, key=f"swap:b:{n}"),
        )
        for n in range(rounds)
    ]

    for number, (forward, backward) in enumerate(holds):
        outcomes = parallel(
            [
                partial(
                    services.release_escrow,
                    forward,
                    to_user=other,
                    key=f"swap:a:{number}:pay",
                    event_key="test.bounty",
                ),
                partial(
                    services.release_escrow,
                    backward,
                    to_user=user,
                    key=f"swap:b:{number}:pay",
                    event_key="test.bounty",
                ),
            ]
        )
        assert all(isinstance(o, LedgerEntry) for o in outcomes), outcomes

    assert services.balance(user) == 1000
    assert services.balance(other) == 1000
    assert reconcile.reconcile(deep=True).consistent


def test_an_object_pays_one_recipient_even_when_everyone_asks_at_once(
    make_user: Any, rule: RewardRule
) -> None:
    assert rule.per_object_once
    members = [make_user(f"racer{n}") for n in range(6)]

    outcomes = parallel(
        [
            partial(
                services.grant_reward,
                member,
                EVENT,
                key=f"obj:{member.pk}",
                reference="firmware:42",
            )
            for member in members
        ]
    )

    assert sum(isinstance(o, LedgerEntry) for o in outcomes) == 1
    assert LedgerEntry.objects.filter(reference="firmware:42").count() == 1


def test_a_daily_cap_holds_when_one_user_earns_from_several_requests(
    user: Any, rule: RewardRule
) -> None:
    rule.daily_cap = 10
    rule.per_object_once = False
    rule.save()

    outcomes = parallel(
        [partial(services.grant_reward, user, EVENT, key=f"cap:{n}") for n in range(6)]
    )

    assert sum(isinstance(o, LedgerEntry) for o in outcomes) == 1
    assert services.balance(user) == 10


def test_the_trigger_refuses_changes_to_money_columns_and_deletes(user: Any) -> None:
    entry = services.credit(user, 5, reason=LedgerReason.TOPUP, key="trg:1")

    for sql in (
        "UPDATE wallet_ledgerentry SET amount = amount + 1",
        "UPDATE wallet_ledgerentry SET balance_after = 0",
        "UPDATE wallet_ledgerentry SET direction = 'debit'",
        "UPDATE wallet_ledgerentry SET key = 'other'",
        "UPDATE wallet_ledgerentry SET wallet_id = wallet_id + 1",
        "DELETE FROM wallet_ledgerentry",
    ):
        with pytest.raises(IntegrityError, match="append-only"), transaction.atomic():
            raw_sql(sql)

    stored = LedgerEntry.objects.get(pk=entry.pk)
    assert (stored.amount, stored.balance_after, stored.key) == (5, 5, "trg:1")


def test_the_note_changes_only_inside_the_erasure_window(user: Any, staff: Any) -> None:
    services.adjust(
        user, 5, actor=staff, reason_text="Paid to a@b.example", key="trg:2"
    )

    with pytest.raises(IntegrityError, match="account erasure"), transaction.atomic():
        raw_sql("UPDATE wallet_ledgerentry SET note = ''")
    with transaction.atomic():
        raw_sql("SELECT set_config('wallet.ledger_scrub', 'on', true)")
        raw_sql("UPDATE wallet_ledgerentry SET note = ''")
    assert LedgerEntry.objects.get(key="trg:2").note == ""
    with pytest.raises(IntegrityError), transaction.atomic():
        raw_sql("UPDATE wallet_ledgerentry SET note = 'again'")


def test_the_actor_can_be_cleared_but_never_reassigned(user: Any, staff: Any) -> None:
    services.adjust(user, 5, actor=staff, reason_text="Goodwill", key="trg:3")

    with pytest.raises(IntegrityError, match="actor"), transaction.atomic():
        raw_sql("UPDATE wallet_ledgerentry SET actor_id = %s", [user.pk])
    with transaction.atomic():
        raw_sql("UPDATE wallet_ledgerentry SET actor_id = NULL")

    assert LedgerEntry.objects.get(key="trg:3").actor_id is None


def test_account_erasure_passes_through_the_trigger_and_keeps_every_amount(
    user: Any, staff: Any
) -> None:
    services.adjust(
        user, 5, actor=staff, reason_text="Paid to a@b.example", key="trg:4"
    )
    before = list(LedgerEntry.objects.values_list("amount", "balance_after", "key"))

    report = anonymise_user(user)

    assert report.ledger_notes == 1 and report.unreconciled_wallets == 0
    assert (
        list(LedgerEntry.objects.values_list("amount", "balance_after", "key"))
        == before
    )
    assert not LedgerEntry.objects.exclude(note="").exists()
    with pytest.raises(IntegrityError), transaction.atomic():
        raw_sql("UPDATE wallet_ledgerentry SET note = 'late edit'")


def test_reconcile_and_repair_work_against_a_real_drift(user: Any, staff: Any) -> None:
    fund(user, 100)
    wallet = Wallet.objects.get(user=user)
    with ledger_posting():
        Wallet.objects.filter(pk=wallet.pk).update(balance=180)

    assert not reconcile.reconcile().consistent
    reconcile.repair_wallet(
        wallet.pk, actor=staff, reason_text="Drift", key="pg:repair"
    )

    assert Wallet.objects.get(pk=wallet.pk).balance == 100
    assert reconcile.reconcile(deep=True).consistent


def test_the_guard_migration_is_reversible_and_reinstallable(user: Any) -> None:
    migration = import_module("apps.wallet.migrations.0003_ledger_integrity")
    services.credit(user, 5, reason=LedgerReason.TOPUP, key="trg:5")

    with connection.schema_editor() as editor:
        migration.remove_ledger_guard(None, editor)
    try:
        raw_sql("UPDATE wallet_ledgerentry SET amount = 6")
        raw_sql("UPDATE wallet_ledgerentry SET amount = 5")
    finally:
        with connection.schema_editor() as editor:
            migration.install_ledger_guard(None, editor)
    with pytest.raises(IntegrityError), transaction.atomic():
        raw_sql("UPDATE wallet_ledgerentry SET amount = 7")

    assert LedgerEntry.objects.get(key="trg:5").amount == 5

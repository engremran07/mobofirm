"""Shared safety guards and reporting for the platform seed commands."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.core.management.base import CommandError
from django.db import connection

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def require_local_database(*, confirm: bool) -> None:
    """Refuse to seed unless the owner confirmed and the database is local.

    SQLite is always local. PostgreSQL must be reached over a loopback address, so
    a mistyped environment can never seed a shared or production database.
    """
    if not confirm:
        raise CommandError(
            "Seeding writes real catalogue, editorial and account records. "
            "Re-run with --confirm to proceed."
        )
    if connection.vendor == "sqlite":
        return
    host = (connection.settings_dict.get("HOST") or "").strip()
    if host not in LOOPBACK_HOSTS:
        raise CommandError(
            "Refusing to seed a non-local database "
            f"(host {host or 'unset'!r}); only localhost, 127.0.0.1 and ::1 "
            "are accepted."
        )


@dataclass
class SeedReport:
    """Counts of records created (or already present) by one seed step."""

    created: dict[str, int] = field(default_factory=dict)
    existing: dict[str, int] = field(default_factory=dict)

    def record(self, kind: str, *, created: bool) -> None:
        bucket = self.created if created else self.existing
        bucket[kind] = bucket.get(kind, 0) + 1

    def merge(self, other: SeedReport) -> None:
        for kind, count in other.created.items():
            self.created[kind] = self.created.get(kind, 0) + count
        for kind, count in other.existing.items():
            self.existing[kind] = self.existing.get(kind, 0) + count

    def lines(self) -> list[str]:
        kinds = sorted(set(self.created) | set(self.existing))
        return [
            f"  {kind}: {self.created.get(kind, 0)} created, "
            f"{self.existing.get(kind, 0)} already present"
            for kind in kinds
        ]


class SeedVerificationError(CommandError):
    """Raised when seeded data does not satisfy its acceptance criteria."""


def require_user(username: str) -> AbstractBaseUser:
    """Fetch a seeded account; steps that need one fail with guidance."""
    user = get_user_model().objects.filter(username=username).first()
    if user is None:
        raise CommandError(
            f"Seed account {username!r} does not exist; run seed_people first."
        )
    return user


def check(condition: bool, message: str) -> None:
    """Assert one acceptance criterion; failures abort the command visibly."""
    if not condition:
        raise SeedVerificationError(f"Verification failed: {message}")


@contextmanager
def eager_background_tasks() -> Iterator[None]:
    """Run Celery tasks inline so post-publish automation completes during a seed.

    Seeding must not depend on a broker; the same task code runs, only inline, so
    tag assignment and other post-commit work is exercised and finished before the
    command reports.
    """
    from app.celery import app as celery_app

    previous = (
        celery_app.conf.task_always_eager,
        celery_app.conf.task_eager_propagates,
    )
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager, celery_app.conf.task_eager_propagates = (
            previous
        )


def run_step(
    stdout_write: Callable[[str], object], title: str, step: Callable[[], SeedReport]
) -> SeedReport:
    """Run one seed step, echoing its title and per-kind counts."""
    stdout_write(f"== {title}")
    report = step()
    for line in report.lines():
        stdout_write(line)
    return report

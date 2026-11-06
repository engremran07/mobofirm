"""Deterministic seeding of the staff, editorial and reader accounts."""

from __future__ import annotations

from collections.abc import Sequence

from allauth.account.models import EmailAddress
from django.contrib.auth.models import Group
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.seed_data.people import ALL_PEOPLE, PersonSeed
from apps.core.seeding import SeedReport
from apps.users.models import CustomUser


def _create(person: PersonSeed) -> CustomUser:
    user = CustomUser.objects.create_user(
        email=person.email,
        username=person.username,
        full_name=person.full_name,
        bio=person.bio,
        is_staff=person.is_staff,
        role=person.role or None,
        can_create_blog_posts=person.can_create_blog_posts,
        profile_completed=True,
        email_verified_at=timezone.now(),
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=person.email,
        defaults={"verified": True, "primary": True},
    )
    return user


def _join_groups(user: CustomUser, person: PersonSeed) -> None:
    for name in person.groups:
        group = Group.objects.filter(name=name).first()
        if group is None:
            raise CommandError(
                f"Group {name!r} does not exist; run bootstrap_site before seeding "
                "accounts."
            )
        group.user_set.add(user)


def ensure_people(
    people: Sequence[PersonSeed] = ALL_PEOPLE,
) -> tuple[SeedReport, dict[str, CustomUser]]:
    """Create the seed accounts (unusable passwords) and their group membership."""
    report = SeedReport()
    users: dict[str, CustomUser] = {}
    for person in people:
        with transaction.atomic():
            user = CustomUser.objects.filter(username=person.username).first()
            created = user is None
            if user is None:
                user = _create(person)
            _join_groups(user, person)
        users[person.username] = user
        report.record("accounts", created=created)
    return report, users

"""Every account holds the free package from sign-up (all creation paths) and by backfill."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.shop import entitlements, services
from apps.shop.baseline import (
    backfill_baseline_packages,
    default_package,
    ensure_baseline_package,
)
from apps.shop.models import PackageTier, UserPackage

pytestmark = pytest.mark.django_db


@pytest.fixture
def free(db) -> PackageTier:
    return PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        limit_files=10,
        window="day",
        device_limit=3,
    )


@pytest.fixture
def pro(db) -> PackageTier:
    return PackageTier.objects.create(
        name="Pro",
        slug="pro",
        price=Decimal(9),
        duration_days=30,
        limit_files=100,
        window="month",
        applies_to_featured=True,
    )


def baselines(user) -> list[UserPackage]:
    return list(UserPackage.objects.filter(user=user, is_baseline=True))


class TestSignUpPaths:
    def test_orm_created_accounts_get_it(self, free, django_user_model):
        user = django_user_model.objects.create_user(
            username="orm", email="orm@example.com", password="Pass-12345!"
        )

        (row,) = baselines(user)
        assert (row.package, row.status, row.source) == (free, "active", "default")
        assert row.expires_at is None

    def test_superusers_get_it_too(self, free, django_user_model):
        admin = django_user_model.objects.create_superuser(
            username="root", email="root@example.com", password="Pass-12345!"
        )

        assert len(baselines(admin)) == 1

    def test_the_web_sign_up_form_assigns_it(self, free):
        response = Client().post(
            reverse("users:account_signup"),
            {
                "email": "web-free@example.com",
                "password1": "Another-valid-password-983!",
                "password2": "Another-valid-password-983!",
            },
            follow=True,
        )

        assert response.status_code == 200
        user = get_user_model().objects.get(email="web-free@example.com")
        assert len(baselines(user)) == 1

    def test_social_sign_up_assigns_it(self, free):
        from allauth.socialaccount.models import SocialAccount, SocialLogin
        from django.contrib.sessions.backends.db import SessionStore

        user = get_user_model()(email="social@example.com", username="social_free")
        user.set_unusable_password()
        login = SocialLogin(
            user=user, account=SocialAccount(provider="google", uid="12345")
        )
        request = RequestFactory().get("/")
        request.session = SessionStore()

        login.save(request)

        assert (
            len(baselines(get_user_model().objects.get(email="social@example.com")))
            == 1
        )

    def test_admin_created_accounts_get_it(self, free, django_user_model):
        root = django_user_model.objects.create_superuser(
            username="boss", email="boss@example.com", password="Pass-12345!"
        )
        client = Client()
        client.force_login(root)

        response = client.post(
            reverse("admin:users_customuser_add"),
            {
                "email": "made@example.com",
                "username": "made_by_admin",
                "password1": "Another-valid-password-983!",
                "password2": "Another-valid-password-983!",
            },
        )

        assert response.status_code == 302
        made = get_user_model().objects.get(email="made@example.com")
        assert len(baselines(made)) == 1

    def test_saving_again_never_duplicates_it(self, free, django_user_model):
        user = django_user_model.objects.create_user(
            username="again", email="again@example.com", password="Pass-12345!"
        )

        user.first_name = "Edited"
        user.save()
        ensure_baseline_package(user)
        ensure_baseline_package(user)

        assert len(baselines(user)) == 1

    def test_without_a_default_package_sign_up_still_works(self, django_user_model):
        user = django_user_model.objects.create_user(
            username="early", email="early@example.com", password="Pass-12345!"
        )

        assert baselines(user) == []
        assert ensure_baseline_package(user) is None

    def test_a_changed_default_package_is_followed(self, free, django_user_model):
        user = django_user_model.objects.create_user(
            username="moved", email="moved@example.com", password="Pass-12345!"
        )
        starter = PackageTier.objects.create(
            name="Starter", slug="starter", duration_days=0, limit_files=5, window="day"
        )
        PackageTier.objects.filter(pk=free.pk).update(is_default=False)
        PackageTier.objects.filter(pk=starter.pk).update(is_default=True)

        ensure_baseline_package(user)

        (row,) = baselines(user)
        assert row.package == starter
        assert default_package() == starter


class TestEntitlementsUnchangedByTheBaseline:
    def test_the_free_allowance_applies_with_the_baseline_row(
        self, free, django_user_model
    ):
        user = django_user_model.objects.create_user(
            username="plain", email="plain@example.com", password="Pass-12345!"
        )

        ent = entitlements.effective_entitlements(user)

        assert ent.package is None
        assert ent.package_name == "Free"
        assert ent.download_quota == 10
        assert entitlements.active_package(user) is None

    def test_a_purchased_package_replaces_it_without_touching_the_baseline(
        self, free, pro, django_user_model
    ):
        user = django_user_model.objects.create_user(
            username="buyer1", email="buyer1@example.com", password="Pass-12345!"
        )

        held = services.activate_package(user, pro)

        ent = entitlements.effective_entitlements(user)
        assert ent.package == held and ent.package_name == "Pro"
        (row,) = baselines(user)
        assert row.status == "active"

    def test_the_free_allowance_returns_when_the_package_ends(
        self, free, pro, django_user_model
    ):
        user = django_user_model.objects.create_user(
            username="buyer2", email="buyer2@example.com", password="Pass-12345!"
        )
        held = services.activate_package(user, pro)
        UserPackage.objects.filter(pk=held.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )

        assert entitlements.effective_entitlements(user).package_name == "Free"

    def test_the_baseline_cannot_be_cancelled(self, free, django_user_model):
        user = django_user_model.objects.create_user(
            username="keeper", email="keeper@example.com", password="Pass-12345!"
        )
        (row,) = baselines(user)

        with pytest.raises(services.OrderError, match="free package"):
            services.cancel_package(row)

    def test_a_second_active_baseline_is_rejected_by_the_database(
        self, free, django_user_model
    ):
        user = django_user_model.objects.create_user(
            username="twice", email="twice@example.com", password="Pass-12345!"
        )

        with pytest.raises(Exception, match="UNIQUE|unique"):
            UserPackage.objects.create(
                user=user, package=free, is_baseline=True, source="default"
            )


class TestBackfill:
    def make_unassigned(self, count: int) -> None:
        user_model = get_user_model()
        user_model.objects.bulk_create(
            [
                user_model(username=f"old{i}", email=f"old{i}@example.com")
                for i in range(count)
            ]
        )

    def test_it_assigns_every_account_without_one_and_is_idempotent(self, free):
        self.make_unassigned(5)

        first = backfill_baseline_packages(batch_size=2)
        second = backfill_baseline_packages(batch_size=2)

        assert (first.created, first.already) == (5, 0)
        assert (second.created, second.already) == (0, 5)
        assert UserPackage.objects.filter(is_baseline=True).count() == 5

    def test_it_never_touches_accounts_that_hold_a_paid_package(
        self, free, pro, django_user_model
    ):
        self.make_unassigned(1)
        buyer = get_user_model().objects.get(username="old0")
        held = services.activate_package(buyer, pro)

        backfill_baseline_packages()

        held.refresh_from_db()
        assert held.status == "active" and not held.is_baseline
        assert entitlements.effective_entitlements(buyer).package == held

    def test_the_command_reports_and_needs_a_default_package(self, db):
        self.make_unassigned(1)
        with pytest.raises(CommandError, match="seed_packages"):
            call_command("backfill_free_package")

        PackageTier.objects.create(
            name="Free", slug="free", is_default=True, duration_days=0, limit_files=1
        )
        out = StringIO()
        call_command("backfill_free_package", stdout=out)
        again = StringIO()
        call_command("backfill_free_package", stdout=again)

        assert "1 assigned" in out.getvalue()
        assert "0 assigned, 1 already" in again.getvalue()

    def test_seeding_the_packages_also_backfills(self, db):
        self.make_unassigned(2)

        call_command("seed_packages", stdout=StringIO())

        assert UserPackage.objects.filter(is_baseline=True).count() == 2

    def test_the_admin_lists_the_free_rows_and_filters_them(
        self, free, django_user_model
    ):
        root = django_user_model.objects.create_superuser(
            username="lister", email="lister@example.com", password="Pass-12345!"
        )
        client = Client()
        client.force_login(root)

        listing = client.get(
            reverse("admin:shop_userpackage_changelist"), {"is_baseline__exact": "1"}
        )

        assert listing.status_code == 200
        assert b"lister@example.com" in listing.content

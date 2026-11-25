"""Package builder ads & unlock fields, their human text and the pricing page rows."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from apps.shop.admin import PackageTierAdmin
from apps.shop.models import PackageTier

pytestmark = pytest.mark.django_db


def tier(**fields) -> PackageTier:
    base = {"name": "T", "slug": "t", "price": Decimal(5), "duration_days": 30}
    base.update(fields)
    return PackageTier(**base)


class TestFieldsAndRules:
    def test_defaults_are_off_and_documented(self):
        package = PackageTier()

        assert package.unlock_by_ads_free is False and package.unlock_ads_free == 3
        assert (
            package.unlock_by_ads_featured is False and package.unlock_ads_featured == 5
        )
        assert (package.unlock_cooldown_seconds, package.unlock_daily_cap) == (30, 5)
        for name in (
            "unlock_by_ads_free",
            "unlock_ads_free",
            "unlock_by_ads_featured",
            "unlock_ads_featured",
            "unlock_cooldown_seconds",
            "unlock_daily_cap",
        ):
            field = PackageTier._meta.get_field(name)
            assert isinstance(field, models.Field)
            assert field.help_text

    @pytest.mark.parametrize("field", ["unlock_ads_free", "unlock_ads_featured"])
    def test_at_least_one_ad_is_required(self, field):
        package = tier(limit_files=5, **{field: 0})

        with pytest.raises(ValidationError) as error:
            package.full_clean(exclude=["slug"])

        assert field in error.value.message_dict

    def test_a_package_with_featured_cannot_unlock_featured_by_ads(self):
        package = tier(
            limit_files=5, applies_to_featured=True, unlock_by_ads_featured=True
        )

        with pytest.raises(ValidationError) as error:
            package.full_clean(exclude=["slug"])

        assert "unlock_by_ads_featured" in error.value.message_dict

    def test_ad_free_packages_never_offer_unlocks(self):
        package = tier(
            limit_files=5,
            ad_free=True,
            unlock_by_ads_free=True,
            unlock_by_ads_featured=True,
        )

        assert not package.unlock_offers_free and not package.unlock_offers_featured
        assert package.unlock_text == "No ads, nothing to watch"
        assert package.ads_text == "No ads"

    def test_the_human_text_reads_plainly(self):
        package = tier(
            limit_files=5,
            unlock_by_ads_free=True,
            unlock_ads_free=3,
            unlock_by_ads_featured=True,
            unlock_ads_featured=1,
            unlock_daily_cap=4,
        )

        assert package.unlock_text == (
            "Over your limit: watch 3 ads to unlock a free file; "
            "watch 1 ad to unlock a featured file (up to 4 files a day)"
        )
        assert package.ads_text == "Download-gate ads, link shortener"
        assert tier(direct_links=True).ads_text == "Download-gate ads, direct links"
        assert tier().unlock_text == "No ad unlocks"


class TestBuilder:
    def test_the_builder_has_an_ads_and_unlocks_tab(self):
        names = [str(name) for name, _ in PackageTierAdmin.fieldsets]
        tab = dict(PackageTierAdmin.fieldsets)[
            next(
                n for n in dict(PackageTierAdmin.fieldsets) if str(n) == "Ads & unlocks"
            )
        ]

        assert names.index("Ads & unlocks") == len(names) - 2
        flat = [
            f
            for row in tab["fields"]
            for f in (row if isinstance(row, tuple) else (row,))
        ]
        assert flat == [
            "unlock_by_ads_free",
            "unlock_ads_free",
            "unlock_by_ads_featured",
            "unlock_ads_featured",
            "unlock_cooldown_seconds",
            "unlock_daily_cap",
        ]

    def test_the_preview_describes_ads_and_unlocks(self, client, django_user_model):
        staff = django_user_model.objects.create_superuser(
            username="builder", email="builder@example.com", password="Pass-12345!"
        )
        package = PackageTier.objects.create(
            name="Free",
            slug="free",
            is_default=True,
            duration_days=0,
            limit_files=3,
            window="day",
            unlock_by_ads_free=True,
        )
        client.force_login(staff)

        page = client.get(reverse("admin:shop_packagetier_change", args=[package.pk]))

        text = page.content.decode()
        assert package.unlock_text in text and package.ads_text in text

    def test_the_ticket_list_is_read_only_and_filterable(
        self, client, django_user_model
    ):
        from django.contrib import admin

        from apps.shop.unlock_models import AdUnlockTicket

        ticket_admin = admin.site.get_model_admin(AdUnlockTicket)
        staff = django_user_model.objects.create_superuser(
            username="auditor", email="auditor@example.com", password="Pass-12345!"
        )
        client.force_login(staff)

        page = client.get(reverse("admin:shop_adunlockticket_changelist"))

        assert page.status_code == 200
        assert "0 started, 0 unlocked (0%)" in page.content.decode()
        assert client.get(reverse("admin:shop_adunlockticket_add")).status_code in {
            403,
            404,
        }
        filters = [
            item[0] if isinstance(item, tuple) else item
            for item in ticket_admin.list_filter
        ]
        assert filters == ["status", "kind", "package", "created_at"]


class TestPricingPage:
    @pytest.fixture
    def ladder(self):
        free = PackageTier.objects.create(
            name="Free",
            slug="free",
            is_default=True,
            duration_days=0,
            limit_files=5,
            window="day",
            unlock_by_ads_free=True,
            unlock_ads_free=3,
            unlock_by_ads_featured=True,
            unlock_ads_featured=5,
            sort_order=1,
        )
        silver = PackageTier.objects.create(
            name="Silver",
            slug="silver",
            price=Decimal(5),
            duration_days=30,
            limit_files=40,
            window="package",
            direct_links=True,
            unlock_by_ads_free=True,
            unlock_ads_free=2,
            sort_order=2,
        )
        platinum = PackageTier.objects.create(
            name="Platinum",
            slug="platinum",
            price=Decimal(99),
            duration_days=365,
            bandwidth_enabled=False,
            limit_devices=False,
            applies_to_featured=True,
            applies_to_paid=True,
            ad_free=True,
            direct_links=True,
            sort_order=3,
        )
        return free, silver, platinum

    def test_the_cards_describe_ads_and_unlocks(self, client, ladder):
        page = client.get(reverse("shop:pricing")).content.decode()

        assert "Over your limit: watch 3 ads to unlock a free file" in page
        assert "Download-gate ads, link shortener" in page
        assert "Download-gate ads, direct links" in page
        assert "No ads, direct links" in page
        assert page.count("data-plan-ads") == 3
        assert page.count("data-plan-unlock") == 2

    def test_the_comparison_table_has_the_ad_rows(self, client, ladder):
        from apps.shop.views import comparison_rows

        rows = dict(comparison_rows(list(ladder)))

        assert rows["Ads"] == [
            "Download-gate ads, link shortener",
            "Download-gate ads, direct links",
            "No ads, direct links",
        ]
        assert rows["Over your limit"] == [
            "Watch 3 ads to unlock a file",
            "Watch 2 ads to unlock a file",
            "No ads to watch",
        ]
        assert rows["Featured files by ads"] == [
            "Watch 5 ads per file",
            "—",
            "Included",
        ]
        page = client.get(reverse("shop:pricing")).content.decode()
        for label in ("Over your limit", "Featured files by ads", "Ads"):
            assert label in page

"""The promotions admin: pages load, forms validate, lifecycle dialogs work and are protected."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.promotions import lifecycle
from apps.promotions.grants import claim
from apps.promotions.models import (
    Promotion,
    PromotionKind,
    PromotionLog,
    PromotionStatus,
)

DIALOG = {"_form_submitted": "on"}


@pytest.fixture
def root(staff: Any) -> Client:
    client = Client()
    client.force_login(staff)
    return client


def promotion_form(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Credits gift",
        "slug": "credits-gift",
        "headline": "Free credits",
        "description": "",
        "terms": "",
        "kind": PromotionKind.CREDITS_BONUS,
        "audience": "everyone",
        "announce": "on",
        "cta_label": "See the offer",
        "discounted_price": "",
        "discounted_credit_price": "",
        "bonus_files": 0,
        "bonus_size_gb": 0,
        "bonus_devices": 0,
        "trial_days": 0,
        "bonus_credits": 25,
        "reward_multiplier": "1.00",
        "event_keys": "[]",
        "earn_rules": "{}",
        "max_claims_total": 0,
        "max_claims_per_user": 1,
        "budget_credits": 0,
        "log-TOTAL_FORMS": 0,
        "log-INITIAL_FORMS": 0,
        "log-MIN_NUM_FORMS": 0,
        "log-MAX_NUM_FORMS": 0,
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


@pytest.mark.parametrize(
    "name", ["promotion", "promotiongrant", "promotionprogress", "promotionlog"]
)
def test_every_promotion_model_changelist_loads(root: Client, name: str) -> None:
    assert root.get(reverse(f"admin:promotions_{name}_changelist")).status_code == 200


def test_the_add_form_loads_for_promotions_and_is_closed_for_workflow_records(
    root: Client,
) -> None:
    assert root.get(reverse("admin:promotions_promotion_add")).status_code == 200
    for name in ("promotiongrant", "promotionprogress", "promotionlog"):
        assert root.get(reverse(f"admin:promotions_{name}_add")).status_code == 403


def test_the_change_page_has_every_tab_and_the_analytics(
    root: Client, make_promotion: Any, user: Any
) -> None:
    promotion = make_promotion()
    claim(user, promotion)

    html = root.get(
        reverse("admin:promotions_promotion_change", args=[promotion.pk])
    ).content.decode()

    for tab in (
        "Offer",
        "Schedule &amp; banner",
        "Package &amp; price",
        "Bonus &amp; rewards",
        "Earn rules",
        "Caps &amp; budget",
        "Analytics",
        "Audit log",
    ):
        assert tab in html
    assert "Claims and rewards granted" in html and "View grants" in html


def test_staff_can_create_a_draft_and_become_its_creator(
    root: Client, staff: Any
) -> None:
    response = root.post(reverse("admin:promotions_promotion_add"), promotion_form())

    assert response.status_code == 302
    promotion = Promotion.objects.get(slug="credits-gift")
    assert promotion.status == PromotionStatus.DRAFT
    assert promotion.created_by_id == staff.pk
    assert promotion.announcement_id is None


def test_an_incomplete_earn_promotion_is_rejected_with_a_clear_error(
    root: Client,
) -> None:
    response = root.post(
        reverse("admin:promotions_promotion_add"),
        promotion_form(
            kind=PromotionKind.EARN_BY_CONTRIBUTING, trial_days=7, bonus_credits=0
        ),
    )

    assert response.status_code == 200
    assert "Earn rules need" in response.content.decode()
    assert not Promotion.objects.exists()


def action_url(name: str, promotion: Promotion) -> str:
    return reverse(f"admin:promotions_promotion_{name}", args=[promotion.pk])


def test_the_lifecycle_dialogs_schedule_start_and_end(
    root: Client, make_promotion: Any
) -> None:
    later = make_promotion(live=False, starts_at=timezone.now() + timedelta(days=3))

    assert root.get(action_url("schedule_promotion", later)).status_code == 200
    root.post(action_url("schedule_promotion", later), DIALOG)
    later.refresh_from_db()
    assert later.status == PromotionStatus.SCHEDULED

    root.post(action_url("unschedule_promotion", later), DIALOG)
    later.refresh_from_db()
    assert later.status == PromotionStatus.DRAFT

    response = root.post(action_url("start_promotion", later), DIALOG)
    later.refresh_from_db()
    assert later.status == PromotionStatus.ACTIVE and "HX-Redirect" in response.headers
    assert later.announcement is not None and later.announcement.is_active

    root.post(action_url("end_promotion", later), DIALOG)
    later.refresh_from_db()
    assert later.status == PromotionStatus.ENDED
    assert later.announcement is not None and not later.announcement.is_active
    actions = set(
        PromotionLog.objects.filter(promotion=later).values_list("action", flat=True)
    )
    assert {"scheduled", "started_now", "ended", "returned_to_draft"} <= actions


def test_an_invalid_promotion_cannot_be_started_and_says_why(
    root: Client, make_promotion: Any
) -> None:
    promotion = make_promotion(
        PromotionKind.DISCOUNTED_PACKAGE,
        live=False,
        discounted_price=Decimal("4.00"),
    )

    response = root.post(action_url("start_promotion", promotion), DIALOG)

    promotion.refresh_from_db()
    assert promotion.status == PromotionStatus.DRAFT
    page = root.get(response.headers["HX-Redirect"]).content.decode()
    assert "Choose the package this offer is about." in page


def test_duplicate_makes_a_draft_copy(root: Client, make_promotion: Any) -> None:
    promotion = make_promotion()

    response = root.post(action_url("duplicate_promotion", promotion), DIALOG)

    copy = Promotion.objects.exclude(pk=promotion.pk).get()
    assert copy.status == PromotionStatus.DRAFT
    assert response.headers["HX-Redirect"].endswith(f"/{copy.pk}/change/")


def test_dialog_actions_need_the_change_permission(
    make_promotion: Any, make_user: Any
) -> None:
    viewer = make_user("viewer", staff=False)
    viewer.is_staff = True
    viewer.save()
    viewer.user_permissions.add(Permission.objects.get(codename="view_promotion"))
    client = Client()
    client.force_login(viewer)
    promotion = make_promotion()

    response = client.post(action_url("end_promotion", promotion), DIALOG)

    assert response.status_code == 403
    promotion.refresh_from_db()
    assert promotion.status == PromotionStatus.ACTIVE


def test_a_running_promotion_cannot_be_deleted_but_an_ended_one_can(
    root: Client, make_promotion: Any
) -> None:
    promotion = make_promotion()
    url = reverse("admin:promotions_promotion_delete", args=[promotion.pk])

    assert root.get(url).status_code == 403
    lifecycle.end_now(promotion)
    assert root.get(url).status_code == 200


def test_the_navigation_group_lists_the_campaign_models(root: Client) -> None:
    html = root.get(reverse("admin:index")).content.decode()

    assert "Campaigns" in html and "Promotions" in html


def test_the_dashboard_shows_the_campaign_kpi(
    root: Client, make_promotion: Any
) -> None:
    make_promotion()

    html = root.get(reverse("admin:index")).content.decode()

    assert "Campaigns running" in html

"""Offer pages, the claim dialog, banners, panels, search, ads link and the seed command."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ads.models import Campaign
from apps.core.models import AppRegistry
from apps.core.search import search
from apps.promotions import lifecycle
from apps.promotions.grants import claim
from apps.promotions.models import Promotion, PromotionGrant, PromotionKind
from apps.users.models import Announcement

HX = {"HX-Request": "true"}


def offer_url(promotion: Promotion) -> str:
    return reverse("promotions:offer", kwargs={"slug": promotion.slug})


def claim_url(promotion: Promotion) -> str:
    return reverse("promotions:claim", kwargs={"slug": promotion.slug})


# ---------------------------------------------------------------- pages


def test_the_index_lists_running_and_recently_ended_offers_but_not_drafts(
    make_promotion: Any, client: Client
) -> None:
    running = make_promotion(name="Running offer")
    ended = make_promotion(name="Finished offer")
    lifecycle.end_now(ended)
    make_promotion(name="Secret draft", live=False)

    html = client.get(reverse("promotions:index")).content.decode()

    assert running.name in html and ended.name in html
    assert "Secret draft" not in html and "Recently ended" in html


def test_the_index_is_friendly_when_nothing_runs(client: Client) -> None:
    response = client.get(reverse("promotions:index"))

    assert response.status_code == 200
    assert "No offers are running right now" in response.content.decode()


def test_an_offer_page_shows_terms_effect_and_audience(
    make_promotion: Any, client: Client
) -> None:
    promotion = make_promotion(
        name="Launch bonus",
        description="Extra files for everyone.",
        terms="Valid on new files only",
        max_claims_total=100,
    )

    html = client.get(offer_url(promotion)).content.decode()

    assert "+10 files" in html and "Extra files for everyone." in html
    assert "Valid on new files only" in html
    assert "Limited to 100 places in total." in html
    assert "Sign in to take this offer" in html


@pytest.mark.parametrize("make_live", [False])
def test_drafts_and_unknown_offers_are_not_public(
    make_promotion: Any, client: Client, user: Any, make_live: bool
) -> None:
    draft = make_promotion(live=make_live)

    assert client.get(offer_url(draft)).status_code == 404
    client.force_login(user)
    assert client.get(offer_url(draft)).status_code == 404
    assert (
        client.get(reverse("promotions:offer", kwargs={"slug": "nope"})).status_code
        == 404
    )


def test_staff_can_preview_a_draft(
    make_promotion: Any, client: Client, staff: Any
) -> None:
    draft = make_promotion(live=False)
    client.force_login(staff)

    response = client.get(offer_url(draft))

    assert response.status_code == 200
    assert "Staff preview" in response.content.decode()


def test_the_offer_page_reflects_the_visitor_state(
    make_promotion: Any, client: Client, user: Any
) -> None:
    promotion = make_promotion()
    client.force_login(user)

    available = client.get(offer_url(promotion)).content.decode()
    assert 'data-offer-state="available"' in available
    assert f'hx-get="{claim_url(promotion)}"' in available

    claim(user, promotion)
    claimed = client.get(offer_url(promotion)).content.decode()
    assert 'data-offer-state="claimed"' in claimed
    assert "Claim this offer" not in claimed

    lifecycle.end_now(promotion)
    ended = client.get(offer_url(promotion)).content.decode()
    assert 'data-offer-state="ended"' in ended


def test_the_earn_offer_shows_progress_like_two_of_three_approved_uploads(
    make_promotion: Any, premium: Any, client: Client, user: Any, staff: Any
) -> None:
    from apps.core.events.bus import event_bus

    promotion = make_promotion(
        PromotionKind.EARN_BY_CONTRIBUTING,
        target_package=premium,
        trial_days=7,
        earn_rules={"events": ["contribution.approved"], "count": 3, "within_days": 30},
    )
    for number in (1, 2):
        event_bus.publish(
            "firmware.approved",
            {"uploader": user, "reviewer": staff, "pending_id": f"p{number}"},
        )
    client.force_login(user)

    html = client.get(offer_url(promotion)).content.decode()

    assert "2 of 3 approved uploads within 30 days" in html
    assert 'data-offer-state="in_progress"' in html


def test_not_eligible_and_unverified_visitors_are_told_why(
    make_promotion: Any, client: Client, make_user: Any
) -> None:
    sellers_only = make_promotion(audience="sellers")
    open_offer = make_promotion()
    client.force_login(make_user("plain"))
    assert (
        'data-offer-state="not_eligible"'
        in client.get(offer_url(sellers_only)).content.decode()
    )

    client.force_login(make_user("unverified", verified=False))
    assert (
        'data-offer-state="unverified"'
        in client.get(offer_url(open_offer)).content.decode()
    )


def test_page_views_count_once_per_visitor_and_banner_clicks_are_tracked(
    make_promotion: Any, client: Client
) -> None:
    from django.core.cache import cache

    cache.clear()
    promotion = make_promotion()

    client.get(offer_url(promotion) + "?src=banner")
    client.get(offer_url(promotion) + "?src=banner")

    promotion.refresh_from_db()
    assert (promotion.page_views, promotion.announcement_clicks) == (1, 1)


# ---------------------------------------------------------------- claim dialog


def test_the_claim_dialog_requires_sign_in(make_promotion: Any, client: Client) -> None:
    promotion = make_promotion()

    page = client.get(claim_url(promotion))
    htmx = client.get(claim_url(promotion), headers=HX)

    assert page.status_code == 302 and "login" in page["Location"]
    assert htmx.status_code == 204 and "login" in htmx["HX-Redirect"]


def test_the_claim_dialog_explains_the_offer_before_confirming(
    make_promotion: Any, client: Client, user: Any
) -> None:
    promotion = make_promotion(terms="One per person", description="Bonus for you.")
    client.force_login(user)

    html = client.get(claim_url(promotion), headers=HX).content.decode()

    assert "claim-offer-dialog" in html and "+10 files" in html
    assert "One per person" in html and "Claim offer" in html
    assert 'name="csrfmiddlewaretoken"' in html


def test_confirming_claims_the_offer_and_redirects_back(
    make_promotion: Any, client: Client, user: Any
) -> None:
    promotion = make_promotion()
    client.force_login(user)

    plain = client.post(claim_url(promotion))
    htmx = client.post(claim_url(promotion), headers=HX)

    assert plain.status_code == 302 and plain["Location"] == offer_url(promotion)
    assert htmx.status_code == 204 and htmx["HX-Redirect"] == offer_url(promotion)
    assert PromotionGrant.objects.filter(user=user, promotion=promotion).count() == 1


def test_a_claim_without_the_csrf_token_is_rejected(
    make_promotion: Any, user: Any
) -> None:
    promotion = make_promotion()
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)

    response = strict.post(claim_url(promotion))

    assert response.status_code == 403
    assert not PromotionGrant.objects.exists()


def test_a_rule_violation_stays_inside_the_dialog(
    make_promotion: Any, client: Client, make_user: Any
) -> None:
    promotion = make_promotion(max_claims_total=1)
    claim(make_user("early"), promotion)
    late = make_user("late")
    client.force_login(late)

    response = client.post(claim_url(promotion), headers=HX)

    assert response.status_code == 200
    assert "Every place in this offer is taken." in response.content.decode()
    assert not PromotionGrant.objects.filter(user=late).exists()


def test_ineligible_visitors_see_a_close_only_dialog(
    make_promotion: Any, client: Client, make_user: Any
) -> None:
    promotion = make_promotion(audience="sellers")
    client.force_login(make_user("plain"))

    html = client.get(claim_url(promotion), headers=HX).content.decode()

    assert "This offer is for another group." in html
    assert "Claim offer" not in html
    assert client.post(claim_url(promotion), headers=HX).status_code == 200
    assert not PromotionGrant.objects.exists()


def test_claims_belong_to_the_signed_in_account_only(
    make_promotion: Any, user: Any, other: Any
) -> None:
    promotion = make_promotion()
    for account in (user, other):
        client = Client()
        client.force_login(account)
        client.post(claim_url(promotion), {"user": user.pk, "user_id": user.pk})

    grants = PromotionGrant.objects.filter(promotion=promotion)
    assert sorted(grants.values_list("user_id", flat=True)) == sorted(
        [user.pk, other.pk]
    )


def test_an_unpublished_offer_has_no_claim_dialog(
    make_promotion: Any, client: Client, user: Any
) -> None:
    draft = make_promotion(live=False)
    client.force_login(user)

    assert client.get(claim_url(draft)).status_code == 404
    assert client.post(claim_url(draft)).status_code == 404


def test_the_earn_reward_can_be_taken_from_the_dialog_when_ready(
    make_promotion: Any, premium: Any, client: Client, user: Any, staff: Any
) -> None:
    from apps.promotions.models import PromotionStatus
    from apps.shop import services as shop_services
    from apps.shop.models import PackageTier

    silver = PackageTier.objects.create(
        name="Silver", slug="silver", price=5, duration_days=30
    )
    held = shop_services.activate_package(user, silver)
    promotion = make_promotion(
        PromotionKind.EARN_BY_CONTRIBUTING,
        target_package=premium,
        trial_days=7,
        earn_rules={"events": ["contribution.approved"], "count": 1},
    )
    from apps.core.events.bus import event_bus

    event_bus.publish(
        "firmware.approved", {"uploader": user, "reviewer": staff, "pending_id": "p1"}
    )
    shop_services.cancel_package(held)
    client.force_login(user)

    assert (
        'data-offer-state="ready"' in client.get(offer_url(promotion)).content.decode()
    )
    response = client.post(claim_url(promotion))

    assert response.status_code == 302
    assert PromotionGrant.objects.filter(promotion=promotion, trigger="earn:1").exists()
    assert promotion.status == PromotionStatus.ACTIVE


# ---------------------------------------------------------------- banners


def banner_html(client: Client) -> str:
    return client.get(reverse("shop:pricing")).content.decode()


def test_a_running_promotion_shows_a_site_banner_with_its_call_to_action(
    make_promotion: Any, client: Client
) -> None:
    promotion = make_promotion(headline="Free downloads this week")

    html = banner_html(client)

    assert 'class="site-banner"' in html and "Free downloads this week" in html
    assert f'href="/offers/{promotion.slug}/?src=banner"' in html


def test_the_banner_respects_the_audience_and_disappears_when_the_offer_ends(
    make_promotion: Any, client: Client, user: Any
) -> None:
    sellers = make_promotion(audience="sellers", headline="Sellers only")
    everyone = make_promotion(headline="For everyone")

    assert "For everyone" in banner_html(client)
    client.force_login(user)
    html = banner_html(client)
    assert "For everyone" in html and "Sellers only" not in html

    lifecycle.end_now(everyone)
    lifecycle.end_now(sellers)
    assert 'class="site-banner"' not in banner_html(client)


def test_a_signed_in_only_banner_is_hidden_from_visitors(
    make_promotion: Any, client: Client, user: Any
) -> None:
    make_promotion(audience="verified_email", headline="Verified members")

    assert "Verified members" not in banner_html(client)
    client.force_login(user)
    assert "Verified members" in banner_html(client)


def test_claiming_hides_the_banner_for_that_user(
    make_promotion: Any, client: Client, user: Any
) -> None:
    promotion = make_promotion(headline="Claim me")
    client.force_login(user)
    assert "Claim me" in banner_html(client)

    claim(user, promotion)

    assert "Claim me" not in banner_html(client)


def test_dismissing_a_banner_hides_it_in_this_browser(
    make_promotion: Any, client: Client
) -> None:
    promotion = make_promotion(headline="Dismiss me")
    banner = Promotion.objects.get().announcement
    assert banner is not None
    url = reverse("users:dismiss_announcement", kwargs={"pk": banner.pk})

    response = client.post(url, headers=HX)

    assert response.status_code == 200 and response.content == b""
    assert "mf_dismissed_announcements" in response.cookies
    assert "Dismiss me" not in banner_html(client)
    assert promotion.slug  # the offer itself stays reachable
    assert client.get(offer_url(promotion)).status_code == 200


def test_dismissing_without_javascript_redirects_safely(
    make_promotion: Any, client: Client
) -> None:
    make_promotion()
    banner = Announcement.objects.get()
    url = reverse("users:dismiss_announcement", kwargs={"pk": banner.pk})

    local = client.post(url, {"next": "/offers/"})
    foreign = client.post(url, {"next": "https://evil.example/"})

    assert local["Location"] == "/offers/" and foreign["Location"] == "/"
    assert client.get(url).status_code == 405
    missing = reverse("users:dismiss_announcement", kwargs={"pk": 99999})
    assert client.post(missing).status_code == 404


def test_banner_impressions_count_once_per_signed_in_session(
    make_promotion: Any, client: Client, user: Any
) -> None:
    promotion = make_promotion()
    client.force_login(user)

    banner_html(client)
    banner_html(client)

    promotion.refresh_from_db()
    assert promotion.banner_views == 1


def test_the_banner_link_must_be_a_local_path_or_https() -> None:
    from django.core.exceptions import ValidationError

    from apps.users.models import validate_announcement_link

    for good in ("/offers/x/", "https://example.com/a"):
        validate_announcement_link(good)
    for bad in ("//evil.example", "http://example.com", "javascript:alert(1)", "/a\\b"):
        with pytest.raises(ValidationError):
            validate_announcement_link(bad)


# ---------------------------------------------------------------- panels


def test_my_files_quota_panel_lists_running_offers_and_the_quota_source(
    make_promotion: Any, client: Client, user: Any
) -> None:
    promotion = make_promotion(name="Launch bonus", headline="x")
    client.force_login(user)

    before = client.get(reverse("library:my_quota")).content.decode()
    assert "Active offers" in before and "Launch bonus" in before

    claim(user, promotion)
    after = client.get(reverse("library:my_quota")).content.decode()
    assert "Campaign &quot;Launch bonus&quot; +10 files until" in after
    assert "Active offers" not in after


def test_ways_to_earn_lists_running_offers(
    make_promotion: Any, client: Client, user: Any
) -> None:
    make_promotion(name="Earn extra")
    client.force_login(user)

    html = client.get(reverse("wallet:earn")).content.decode()

    assert "Active offers" in html and "Earn extra" in html


def test_panels_render_nothing_when_the_app_is_disabled(
    make_promotion: Any, client: Client, user: Any
) -> None:
    make_promotion(name="Hidden when off")
    registry = AppRegistry.get_solo()
    registry.promotions_enabled = False
    registry.save()
    client.force_login(user)

    assert "Active offers" not in client.get(reverse("wallet:earn")).content.decode()
    assert (
        "Hidden when off"
        not in client.get(reverse("library:my_quota")).content.decode()
    )
    assert client.get(reverse("promotions:index")).status_code == 404


# ---------------------------------------------------------------- search, ads, dashboard


def test_the_search_provider_lists_only_running_public_offers(
    make_promotion: Any,
) -> None:
    make_promotion(name="Summer launch bonus")
    make_promotion(name="Winter launch draft", live=False)

    hits = search("launch", kinds=("offer",))

    assert [hit.title for hit in hits] == ["Summer launch bonus"]
    assert hits[0].url.startswith("/offers/")


def test_an_ad_campaign_follows_the_offer_it_promotes(make_promotion: Any) -> None:
    promotion = make_promotion()
    campaign = Campaign.objects.create(name="Promote it", promotion=promotion)

    assert campaign.is_live() and campaign.offer_url == offer_url(promotion)

    lifecycle.end_now(promotion)
    campaign.refresh_from_db()
    assert campaign.is_live() is False

    promotion.delete()
    campaign.refresh_from_db()
    assert campaign.promotion_id is None and campaign.is_live()


def test_the_dashboard_counts_running_campaigns(make_promotion: Any) -> None:
    from apps.core.admin_dashboard import build_kpis, compute_metrics

    make_promotion()
    metrics = compute_metrics()

    kpi = next(item for item in build_kpis(metrics) if item.key == "promotions")
    assert kpi.value == 1 and kpi.permission == "promotions.view_promotion"


# ---------------------------------------------------------------- seed command


def test_the_seed_command_creates_disabled_drafts_and_is_idempotent(db: Any) -> None:
    out = StringIO()

    call_command("seed_launch_offers", stdout=out)
    call_command("seed_launch_offers", stdout=out)

    offers = {p.slug: p for p in Promotion.objects.all()}
    assert set(offers) == {"launch-bonus", "contribute-unlock-premium"}
    assert all(
        p.status == "draft" and p.announcement_id is None for p in offers.values()
    )
    assert Announcement.objects.count() == 0
    assert "kept: launch-bonus" in out.getvalue()
    assert "choose the reward package" in out.getvalue()


def test_the_earn_seed_targets_the_best_public_package_when_one_exists(
    premium: Any,
) -> None:
    call_command("seed_launch_offers", stdout=StringIO())

    earn = Promotion.objects.get(slug="contribute-unlock-premium")
    assert earn.target_package_id == premium.pk
    earn.full_clean()
    lifecycle.schedule(earn)
    earn.refresh_from_db()
    assert earn.status == "active"
    assert earn.ends_at is None or earn.ends_at > timezone.now() - timedelta(days=1)

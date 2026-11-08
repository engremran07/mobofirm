"""Ad zones end to end: a placement on a template fills a ``{% ad_zone %}`` of a real page.

Hops covered: ads switch -> consent -> placement (template/zone) -> device -> assignment
-> rotation/caps -> fallback -> impression event -> click redirect, plus the staff test
profile that must never leak to anybody else or be counted.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ads.models import (
    AdCreative,
    AdEvent,
    AdPlacement,
    AdsSettings,
    Campaign,
    PlacementAssignment,
    TemplateAdExclusion,
)
from apps.ads.services import zones as zone_service

PAGE = "firmwares/library_hub.html"
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"
CONSENT = "apps.ads.services.zones.has_consent"
LANDING = "https://advertiser.example/offer"


def make_user(name: str, *, staff: bool = False, perms: tuple[str, ...] = ()):
    user = get_user_model().objects.create_user(
        email=f"{name}@example.com",
        username=name,
        password="pass-12345-xyz",
        is_staff=staff,
    )
    for codename in perms:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    ROOT_URLCONF="app.urls",
    SECURE_SSL_REDIRECT=False,
)
class ZoneServingBase(TestCase):
    def setUp(self) -> None:
        settings_row = AdsSettings.get_solo()
        settings_row.ads_enabled = True
        settings_row.save()
        self.campaign = Campaign.objects.create(name="House", type="house")
        self.creative = AdCreative.objects.create(
            campaign=self.campaign,
            name="Banner",
            creative_type="banner",
            image_url="https://cdn.example/banner.png",
            click_url=LANDING,
        )
        self.placement = AdPlacement.objects.create(
            name="Home header",
            code="home-header",
            slug="home-header",
            template_reference=PAGE,
            zone="header",
            allowed_sizes="728x90",
        )
        self.assignment = PlacementAssignment.objects.create(
            placement=self.placement, creative=self.creative
        )

    def home(self, **extra) -> str:
        response = self.client.get("/", **extra)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def impressions(self) -> int:
        return AdEvent.objects.filter(event_type="impression").count()


class ZoneRenderingTests(ZoneServingBase):
    @patch(CONSENT, return_value=True)
    def test_placed_zone_renders_labelled_reserved_banner_and_counts_impression(
        self, _consent
    ):
        html = self.home()

        self.assertIn('data-ad-zone="header"', html)
        self.assertIn('aria-label="Advertisement"', html)
        self.assertIn("ad-zone--s728x90", html)
        self.assertIn("https://cdn.example/banner.png", html)
        self.assertIn('rel="noopener sponsored nofollow"', html)
        self.assertEqual(self.impressions(), 1)

    @patch(CONSENT, return_value=True)
    def test_banner_link_goes_through_the_tracking_redirect_not_the_advertiser(
        self, _consent
    ):
        html = self.home()

        go = reverse(
            "ads:click_redirect",
            kwargs={"placement": self.placement.slug, "creative_id": self.creative.pk},
        )
        self.assertIn(f'href="{go}"', html)
        self.assertNotIn(f'href="{LANDING}"', html)

    @patch(CONSENT, return_value=True)
    def test_zone_is_ignored_for_templates_without_a_placement(self, _consent):
        self.placement.template_reference = "blog/post_list.html"
        self.placement.save()

        self.assertNotIn("data-ad-zone", self.home())
        self.assertEqual(self.impressions(), 0)

    @patch(CONSENT, return_value=False)
    def test_no_advertising_consent_renders_nothing_and_records_nothing(self, _consent):
        self.assertNotIn("data-ad-zone", self.home())
        self.assertEqual(self.impressions(), 0)

    @patch(CONSENT, return_value=True)
    def test_ads_switched_off_renders_nothing(self, _consent):
        row = AdsSettings.get_solo()
        row.ads_enabled = False
        row.save()

        self.assertNotIn("data-ad-zone", self.home())
        self.assertEqual(self.impressions(), 0)

    @patch(CONSENT, return_value=True)
    def test_disabled_placement_renders_nothing(self, _consent):
        self.placement.is_enabled = False
        self.placement.save()

        self.assertNotIn("data-ad-zone", self.home())

    @patch(CONSENT, return_value=True)
    def test_excluded_template_renders_nothing(self, _consent):
        TemplateAdExclusion.objects.create(template_path=PAGE, reason="ad-free")

        self.assertNotIn("data-ad-zone", self.home())

    @patch(CONSENT, return_value=True)
    def test_zone_without_an_eligible_creative_collapses(self, _consent):
        self.assignment.is_enabled = False
        self.assignment.save()

        self.assertNotIn("data-ad-zone", self.home())

    @patch(CONSENT, return_value=True)
    def test_inactive_campaign_is_not_served(self, _consent):
        self.campaign.is_active = False
        self.campaign.save()

        self.assertNotIn("data-ad-zone", self.home())

    @patch(CONSENT, return_value=True)
    def test_fallback_creative_fills_a_zone_with_no_assignment(self, _consent):
        self.assignment.delete()
        self.placement.fallback_creative = self.creative
        self.placement.save()

        self.assertIn("ad-zone--s728x90", self.home())

    @patch(CONSENT, return_value=True)
    def test_site_wide_placement_fills_every_page_but_never_account_pages(
        self, _consent
    ):
        self.placement.template_reference = ""
        self.placement.save()

        self.assertIn('data-ad-zone="header"', self.home())
        login = self.client.get("/accounts/login/")
        self.assertNotIn("data-ad-zone", login.content.decode())

    @patch(CONSENT, return_value=True)
    def test_a_creative_with_unsafe_markup_never_renders(self, _consent):
        bad = AdCreative.objects.create(
            campaign=self.campaign,
            name="Bad",
            creative_type="html",
            html='<img src="https://x.example/a.png" onerror="alert(1)">',
        )
        self.assignment.creative = bad
        self.assignment.save()

        self.assertNotIn("onerror", self.home())


class DeviceTargetingTests(ZoneServingBase):
    @patch(CONSENT, return_value=True)
    def test_mobile_only_placement(self, _consent):
        self.placement.device_target = "mobile"
        self.placement.save()

        self.assertIn("data-ad-zone", self.home(HTTP_USER_AGENT=MOBILE_UA))
        self.assertNotIn("data-ad-zone", self.home(HTTP_USER_AGENT=DESKTOP_UA))

    @patch(CONSENT, return_value=True)
    def test_desktop_only_placement(self, _consent):
        self.placement.device_target = "desktop"
        self.placement.save()

        self.assertIn("data-ad-zone", self.home(HTTP_USER_AGENT=DESKTOP_UA))
        self.assertNotIn("data-ad-zone", self.home(HTTP_USER_AGENT=MOBILE_UA))


class RotationAndCapTests(ZoneServingBase):
    @patch(CONSENT, return_value=True)
    def test_daily_cap_stops_serving_once_reached(self, _consent):
        self.campaign.daily_cap = 2
        self.campaign.save()

        self.home()
        self.home()
        third = self.home()

        self.assertEqual(self.impressions(), 2)
        self.assertNotIn("data-ad-zone", third)

    @patch(CONSENT, return_value=True)
    def test_total_cap_counts_all_days(self, _consent):
        self.campaign.total_cap = 1
        self.campaign.save()
        self.home()
        AdEvent.objects.update(created_at=timezone.now() - timedelta(days=3))

        self.assertNotIn("data-ad-zone", self.home())

    @patch(CONSENT, return_value=True)
    def test_rotation_serves_every_assigned_creative_over_time(self, _consent):
        second = AdCreative.objects.create(
            campaign=self.campaign,
            name="Banner two",
            creative_type="banner",
            image_url="https://cdn.example/two.png",
            click_url=LANDING,
        )
        PlacementAssignment.objects.create(placement=self.placement, creative=second)

        pages = "".join(self.home() for _ in range(40))

        self.assertIn("banner.png", pages)
        self.assertIn("two.png", pages)


class ClickRedirectTests(ZoneServingBase):
    def url(self, creative: AdCreative | None = None) -> str:
        return reverse(
            "ads:click_redirect",
            kwargs={
                "placement": self.placement.slug,
                "creative_id": (creative or self.creative).pk,
            },
        )

    @patch("apps.ads.views._has_ads_consent", return_value=True)
    def test_redirects_to_the_stored_advertiser_url_and_counts_the_click(
        self, _consent
    ):
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], LANDING)
        self.assertEqual(AdEvent.objects.filter(event_type="click").count(), 1)

    @patch("apps.ads.views._has_ads_consent", return_value=False)
    def test_without_consent_the_visitor_still_arrives_but_nothing_is_stored(
        self, _consent
    ):
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(AdEvent.objects.count(), 0)

    @patch("apps.ads.views._has_ads_consent", return_value=True)
    def test_creative_not_assigned_to_the_placement_is_not_found(self, _consent):
        other = AdCreative.objects.create(
            campaign=self.campaign,
            name="Other",
            creative_type="banner",
            image_url="https://cdn.example/o.png",
            click_url="https://evil.example/",
        )

        self.assertEqual(self.client.get(self.url(other)).status_code, 404)

    @patch("apps.ads.views._has_ads_consent", return_value=True)
    def test_destination_is_never_taken_from_the_request(self, _consent):
        response = self.client.get(self.url() + "?next=https://evil.example/")

        self.assertEqual(response["Location"], LANDING)

    @patch("apps.ads.views._has_ads_consent", return_value=True)
    def test_unsafe_stored_destination_is_not_found(self, _consent):
        AdCreative.objects.filter(pk=self.creative.pk).update(click_url="")

        self.assertEqual(self.client.get(self.url()).status_code, 404)

    @patch("apps.ads.views._has_ads_consent", return_value=True)
    def test_paused_campaign_is_not_found(self, _consent):
        self.campaign.is_active = False
        self.campaign.save()

        self.assertEqual(self.client.get(self.url()).status_code, 404)


class TestProfileTests(ZoneServingBase):
    def setUp(self) -> None:
        super().setUp()
        self.manager = make_user("manager", staff=True, perms=("change_adplacement",))

    def test_signed_link_shows_marked_test_box_without_consent_or_counting(self):
        self.client.force_login(self.manager)
        row = AdsSettings.get_solo()
        row.ads_enabled = False
        row.save()

        html = self.client.get(
            zone_service.preview_url(self.manager, "/")
        ).content.decode()

        self.assertIn("TEST AD", html)
        self.assertIn("728x90", html)
        self.assertIn("zone header", html)
        self.assertIn(f"placement #{self.placement.pk}", html)
        self.assertEqual(AdEvent.objects.count(), 0)

    def test_every_declared_zone_is_forced_even_when_unplaced(self):
        self.client.force_login(self.manager)

        html = self.client.get(
            zone_service.preview_url(self.manager, "/")
        ).content.decode()

        self.assertIn('data-ad-zone="footer"', html)
        self.assertIn("EMPTY ZONE", html)

    def test_paused_placement_is_labelled_in_the_preview(self):
        self.client.force_login(self.manager)
        self.placement.is_enabled = False
        self.placement.save()

        html = self.client.get(
            zone_service.preview_url(self.manager, "/")
        ).content.decode()

        self.assertIn("PAUSED ZONE", html)

    def test_session_toggle_keeps_the_profile_on_for_later_requests(self):
        self.client.force_login(self.manager)
        session = self.client.session
        session[zone_service.PREVIEW_SESSION_KEY] = timezone.now().timestamp() + 600
        session.save()

        self.assertIn("TEST AD", self.home())

    def test_an_expired_session_toggle_does_nothing(self):
        self.client.force_login(self.manager)
        session = self.client.session
        session[zone_service.PREVIEW_SESSION_KEY] = timezone.now().timestamp() - 5
        session.save()

        self.assertNotIn("TEST AD", self.home())

    def test_anonymous_visitor_never_sees_test_ads_even_with_a_valid_token(self):
        link = zone_service.preview_url(self.manager, "/")

        html = self.client.get(link).content.decode()

        self.assertNotIn("TEST AD", html)
        self.assertNotIn("EMPTY ZONE", html)

    def test_plain_flag_and_forged_token_are_ignored(self):
        self.client.force_login(self.manager)

        for value in ("1", "true", "forged", signing.dumps({"u": self.manager.pk})):
            with self.subTest(value=value):
                html = self.client.get(f"/?ad_preview={value}").content.decode()
                self.assertNotIn("TEST AD", html)

    def test_a_link_issued_to_one_staff_user_is_useless_to_another(self):
        other = make_user("other", staff=True, perms=("change_adplacement",))
        link = zone_service.preview_url(self.manager, "/")
        self.client.force_login(other)

        self.assertNotIn("TEST AD", self.client.get(link).content.decode())

    def test_staff_without_the_placement_permission_cannot_preview(self):
        plain = make_user("plain", staff=True)
        self.client.force_login(plain)

        html = self.client.get(zone_service.preview_url(plain, "/")).content.decode()

        self.assertNotIn("TEST AD", html)

    def test_normal_signed_in_user_cannot_preview(self):
        member = make_user("member")
        self.client.force_login(member)

        html = self.client.get(zone_service.preview_url(member, "/")).content.decode()

        self.assertNotIn("TEST AD", html)

    def test_expired_link_is_rejected(self):
        self.client.force_login(self.manager)
        link = zone_service.preview_url(self.manager, "/")

        with patch.object(zone_service, "PREVIEW_TOKEN_MAX_AGE", -1):
            html = self.client.get(link).content.decode()

        self.assertNotIn("TEST AD", html)

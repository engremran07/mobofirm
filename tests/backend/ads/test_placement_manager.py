"""Placement manager: scan -> list -> place -> preview -> enable/remove, all in the admin shell."""

from __future__ import annotations

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.ads.models import (
    AdCreative,
    AdPlacement,
    AdsSettings,
    AutoAdsScanResult,
    Campaign,
    ScanDiscovery,
    TemplateAdExclusion,
)
from apps.ads.services import placement_manager, scanner
from tests.backend.ads.test_zones import make_user

PAGE = "firmwares/library_hub.html"
DETAIL = "blog/post_detail.html"


def scan(path: str, **extra) -> AutoAdsScanResult:
    return AutoAdsScanResult.objects.create(
        template_path=path, template_type="page", score=70, **extra
    )


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    ROOT_URLCONF="app.urls",
    SECURE_SSL_REDIRECT=False,
)
class ManagerBase(TestCase):
    def setUp(self) -> None:
        self.manager = make_user("mgr", staff=True, perms=("change_adplacement",))
        self.manager.user_permissions.add(
            Permission.objects.get(codename="view_adplacement")
        )
        self.client.force_login(self.manager)
        scan(PAGE)
        scan(DETAIL)


class ZoneDeclarationTests(TestCase):
    def test_zones_come_from_the_page_and_the_layouts_it_extends(self):
        home = placement_manager.declared_zones(PAGE)
        detail = placement_manager.declared_zones(DETAIL)

        self.assertLessEqual({"header", "after_content", "footer"}, home)
        self.assertLessEqual({"sidebar", "in_content", "header"}, detail)
        self.assertNotIn("sidebar", home)

    def test_unknown_or_missing_templates_declare_nothing(self):
        self.assertEqual(
            placement_manager.declared_zones("nope/missing.html"), frozenset()
        )
        self.assertEqual(
            placement_manager.declared_zones("../settings.py"), frozenset()
        )

    def test_site_wide_zones_are_the_layout_zones(self):
        self.assertEqual(
            placement_manager.site_wide_zones(),
            frozenset({"header", "after_content", "footer"}),
        )


class ScannerPathTests(TestCase):
    def test_scan_stores_forward_slash_paths_that_match_template_names(self):
        result = scanner.scan_templates_for_placements()

        self.assertEqual(result["status"], "success")
        paths = set(AutoAdsScanResult.objects.values_list("template_path", flat=True))
        self.assertIn(PAGE, paths)
        self.assertFalse([p for p in paths if "\\" in p])


class ManagerAccessTests(ManagerBase):
    def test_anonymous_visitors_are_sent_to_sign_in(self):
        self.client.logout()

        response = self.client.get(reverse("ads:placement_manager"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_staff_without_the_placement_permission_get_403_everywhere(self):
        plain = make_user("plain", staff=True)
        self.client.force_login(plain)
        for name in ("ads:placement_manager", "ads:discoveries", "ads:place_ad"):
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        for name in ("ads:run_scan", "ads:test_profile"):
            with self.subTest(name):
                self.assertEqual(self.client.post(reverse(name)).status_code, 403)

    def test_changes_are_post_only(self):
        for name in ("ads:run_scan", "ads:test_profile"):
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 405)

    def test_state_changing_posts_need_csrf(self):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.manager)

        self.assertEqual(strict.post(reverse("ads:run_scan")).status_code, 403)


class ManagerPageTests(ManagerBase):
    def test_renders_in_the_admin_shell_with_scanned_templates_and_zones(self):
        response = self.client.get(reverse("ads:placement_manager"))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="mf-ui"', html)
        self.assertIn("unfold", html.lower())
        self.assertIn(PAGE, html)
        self.assertIn("Header banner", html)
        self.assertNotIn("btn btn-", html)

    def test_declared_zones_and_open_state_filter(self):
        scan("pages/page.html")
        TemplateAdExclusion.objects.create(template_path="pages/page.html")

        open_rows = placement_manager.template_rows(state="open")
        blocked = placement_manager.template_rows(state="blocked")

        self.assertIn(PAGE, [r.path for r in open_rows])
        self.assertNotIn("pages/page.html", [r.path for r in open_rows])
        self.assertEqual([r.path for r in blocked], ["pages/page.html"])

    def test_search_and_type_filters(self):
        self.assertEqual(
            [r.path for r in placement_manager.template_rows(search="post_detail")],
            [DETAIL],
        )
        self.assertEqual(placement_manager.template_rows(template_type="search"), [])

    def test_removed_placements_disappear_from_rows(self):
        placement_manager.place_ad(
            user=self.manager, template_path=PAGE, zone_code="header"
        )
        AdPlacement.objects.update(is_deleted=True)

        row = next(r for r in placement_manager.template_rows() if r.path == PAGE)

        self.assertEqual(row.placements, [])

    def test_scan_button_posts_and_redirects_with_a_result_message(self):
        response = self.client.post(reverse("ads:run_scan"), follow=True)

        self.assertEqual(
            response.redirect_chain[-1][0], reverse("ads:placement_manager")
        )
        self.assertContains(response, "Scanned")

    def test_preview_link_opens_the_real_route_with_a_signed_flag(self):
        html = self.client.get(reverse("ads:placement_manager")).content.decode()

        self.assertIn("Preview with test ads", html)
        self.assertIn("ad_preview=", html)

    def test_empty_state_before_any_scan(self):
        AutoAdsScanResult.objects.all().delete()

        response = self.client.get(reverse("ads:placement_manager"))

        self.assertContains(response, "No templates have been scanned yet")


class PlaceAdTests(ManagerBase):
    def place(self, template: str = PAGE, **data):
        payload = {"zone": "header", "device": "all", "sizes": "", **data}
        return self.client.post(
            reverse("ads:place_ad") + f"?template={template}", payload
        )

    def test_dialog_offers_only_zones_the_template_declares(self):
        html = self.client.get(
            reverse("ads:place_ad") + f"?template={PAGE}"
        ).content.decode()

        self.assertIn("Header banner", html)
        self.assertNotIn("Sidebar", html)
        self.assertIn("The template is not edited", html)

    def test_creates_a_draft_placement_bound_to_template_and_zone(self):
        response = self.place(sizes="728x90, 970x90", device="mobile")

        placement = AdPlacement.objects.get(template_reference=PAGE, zone="header")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(placement.is_enabled)
        self.assertEqual(placement.allowed_sizes, "728x90,970x90")
        self.assertEqual(placement.device_target, "mobile")
        self.assertEqual(placement.created_by, self.manager)

    def test_can_switch_on_and_attach_a_fallback_creative(self):
        campaign = Campaign.objects.create(name="House")
        creative = AdCreative.objects.create(
            campaign=campaign,
            name="B",
            creative_type="banner",
            image_url="https://x.example/a.png",
        )

        self.place(enable="on", fallback=str(creative.pk))

        placement = AdPlacement.objects.get(template_reference=PAGE)
        self.assertTrue(placement.is_enabled)
        self.assertEqual(placement.fallback_creative, creative)

    def test_a_zone_can_only_be_placed_once_per_template(self):
        self.place()
        response = self.place()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AdPlacement.objects.filter(template_reference=PAGE).count(), 1)

    def test_zone_the_template_does_not_declare_is_rejected(self):
        with self.assertRaises(placement_manager.PlacementError):
            placement_manager.place_ad(
                user=self.manager, template_path=PAGE, zone_code="sidebar"
            )

    def test_excluded_template_and_bad_input_are_rejected(self):
        TemplateAdExclusion.objects.create(template_path=PAGE)
        with self.assertRaises(placement_manager.PlacementError):
            placement_manager.place_ad(
                user=self.manager, template_path=PAGE, zone_code="header"
            )
        with self.assertRaises(placement_manager.PlacementError):
            placement_manager.place_ad(
                user=self.manager, template_path=DETAIL, zone_code="nope"
            )
        with self.assertRaises(placement_manager.PlacementError):
            placement_manager.place_ad(
                user=self.manager, template_path=DETAIL, zone_code="header", sizes="big"
            )
        with self.assertRaises(placement_manager.PlacementError):
            placement_manager.place_ad(
                user=self.manager, template_path=DETAIL, zone_code="header", device="tv"
            )

    def test_site_wide_placement_for_a_layout_zone(self):
        url = reverse("ads:place_ad")

        response = self.client.post(
            url, {"zone": "footer", "device": "all", "sizes": ""}
        )

        self.assertEqual(response.status_code, 302)
        placement = AdPlacement.objects.get(zone="footer")
        self.assertEqual(placement.template_reference, "")

    def test_links_an_existing_code_addressed_placement_with_its_creatives(self):
        existing = AdPlacement.objects.create(
            name="Legacy", code="legacy", slug="legacy"
        )

        self.place(existing=str(existing.pk))

        existing.refresh_from_db()
        self.assertEqual((existing.template_reference, existing.zone), (PAGE, "header"))
        self.assertEqual(AdPlacement.objects.count(), 1)

    def test_names_stay_unique_after_a_removed_placement(self):
        first = placement_manager.place_ad(
            user=self.manager, template_path=PAGE, zone_code="header"
        )
        first.soft_delete(self.manager)

        second = placement_manager.place_ad(
            user=self.manager, template_path=PAGE, zone_code="header"
        )

        self.assertNotEqual(first.slug, second.slug)


class PlacementActionTests(ManagerBase):
    def setUp(self) -> None:
        super().setUp()
        self.placement = placement_manager.place_ad(
            user=self.manager, template_path=PAGE, zone_code="header"
        )

    def test_switch_on_and_off(self):
        url = reverse("ads:toggle_placement", args=[self.placement.pk])

        self.client.post(url, {"action": "enable"})
        self.placement.refresh_from_db()
        self.assertTrue(self.placement.is_enabled)

        self.client.post(url, {"action": "disable"})
        self.placement.refresh_from_db()
        self.assertFalse(self.placement.is_enabled)

    def test_remove_dialog_confirms_then_soft_deletes(self):
        url = reverse("ads:remove_placement", args=[self.placement.pk])

        page = self.client.get(url)
        self.assertContains(page, "Remove this placement?")
        self.assertTrue(
            AdPlacement.objects.filter(pk=self.placement.pk, is_deleted=False).exists()
        )

        self.client.post(url)

        self.placement.refresh_from_db()
        self.assertTrue(self.placement.is_deleted)
        self.assertFalse(self.placement.is_enabled)

    def test_dialog_works_as_an_htmx_fragment(self):
        url = reverse("ads:remove_placement", args=[self.placement.pk])

        response = self.client.get(url, HTTP_HX_REQUEST="true")

        self.assertContains(response, "<dialog")
        self.assertNotContains(response, 'class="mf-ui"')

    def test_other_staff_cannot_toggle_or_remove(self):
        self.client.force_login(make_user("nobody", staff=True))

        self.assertEqual(
            self.client.post(
                reverse("ads:toggle_placement", args=[self.placement.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("ads:remove_placement", args=[self.placement.pk])
            ).status_code,
            403,
        )


class TestProfileToggleTests(ManagerBase):
    def test_switching_on_marks_every_zone_for_this_session_only(self):
        self.client.post(reverse("ads:test_profile"), {"state": "on"})

        mine = self.client.get("/").content.decode()
        self.client.logout()
        anonymous = self.client.get("/").content.decode()

        self.assertIn("EMPTY ZONE", mine)
        self.assertNotIn("EMPTY ZONE", anonymous)

    def test_switching_off(self):
        self.client.post(reverse("ads:test_profile"), {"state": "on"})
        self.client.post(reverse("ads:test_profile"), {"state": "off"})

        self.assertNotIn("EMPTY ZONE", self.client.get("/").content.decode())

    def test_following_the_preview_link_shows_test_ads(self):
        placement_manager.place_ad(
            user=self.manager, template_path=PAGE, zone_code="header"
        )
        html = self.client.get(reverse("ads:placement_manager")).content.decode()
        start = html.index('href="/?ad_preview=') + len('href="')
        link = html[start : html.index('"', start)].replace("&amp;", "&")

        page = self.client.get(link).content.decode()

        self.assertIn("PAUSED ZONE", page)
        self.assertIn("EMPTY ZONE", page)


class DiscoveryQueueTests(ManagerBase):
    def setUp(self) -> None:
        super().setUp()
        self.result = AutoAdsScanResult.objects.get(template_path=DETAIL)
        self.top = self.discovery("blog-post-detail-in-article-top")
        self.mid = self.discovery("blog-post-detail-in-article-mid")
        self.side = self.discovery("blog-post-detail-sidebar-top")

    def discovery(self, code: str) -> ScanDiscovery:
        return ScanDiscovery.objects.create(
            scan_result=self.result,
            placement_code=code,
            placement_name=code.replace("-", " ").title(),
            zone="above-fold",
            confidence=80,
        )

    def test_queue_lists_pending_suggestions_in_the_admin_shell(self):
        response = self.client.get(reverse("ads:discoveries"))

        self.assertContains(response, 'class="mf-ui"')
        self.assertContains(response, "Blog Post Detail Sidebar Top")
        self.assertContains(response, "Pending (3)")

    def test_approve_creates_a_zone_placement_with_a_forward_slash_template(self):
        self.client.post(reverse("ads:approve_discovery", args=[self.side.pk]))

        placement = AdPlacement.objects.get(zone="sidebar")
        self.assertEqual(placement.template_reference, DETAIL)
        self.assertEqual(
            placement_manager.declared_zones(placement.template_reference)
            & {"sidebar"},
            {"sidebar"},
        )
        self.side.refresh_from_db()
        self.assertEqual(self.side.status, "approved")
        self.assertEqual(self.side.placement, placement)

    def test_two_suggestions_for_one_zone_share_the_placement_instead_of_failing(self):
        self.client.post(reverse("ads:approve_discovery", args=[self.top.pk]))
        self.client.post(reverse("ads:approve_discovery", args=[self.mid.pk]))

        self.assertEqual(AdPlacement.objects.filter(zone="in_content").count(), 1)
        self.mid.refresh_from_db()
        self.assertEqual(self.mid.status, "approved")
        self.assertIsNotNone(self.mid.placement)

    def test_approving_twice_is_idempotent(self):
        url = reverse("ads:approve_discovery", args=[self.side.pk])
        self.client.post(url)
        self.client.post(url)

        self.assertEqual(AdPlacement.objects.filter(zone="sidebar").count(), 1)

    def test_bulk_reject_dialog_lists_then_rejects_only_the_selected(self):
        url = reverse("ads:reject_discoveries") + f"?id={self.top.pk}&id={self.mid.pk}"

        page = self.client.get(url)
        self.assertContains(page, "Reject 2 suggestions?")
        self.client.post(url)

        self.top.refresh_from_db()
        self.mid.refresh_from_db()
        self.side.refresh_from_db()
        self.assertEqual((self.top.status, self.mid.status), ("rejected", "rejected"))
        self.assertEqual(self.side.status, "pending")
        self.assertEqual(AdPlacement.objects.count(), 0)

    def test_bulk_approve_dialog(self):
        url = (
            reverse("ads:approve_discoveries") + f"?id={self.side.pk}&id={self.top.pk}"
        )

        self.client.post(url)

        self.assertEqual(
            set(AdPlacement.objects.values_list("zone", flat=True)),
            {"sidebar", "in_content"},
        )

    def test_status_filter_and_empty_state(self):
        ScanDiscovery.objects.all().update(status="rejected")

        pending = self.client.get(reverse("ads:discoveries"))
        rejected = self.client.get(reverse("ads:discoveries") + "?status=rejected")

        self.assertContains(pending, "No suggestions are waiting")
        self.assertContains(rejected, "Blog Post Detail Sidebar Top")


class DashboardTests(ManagerBase):
    def test_staff_screens_are_served_under_the_admin_shell_csp(self):
        for name in ("ads:placement_manager", "ads:discoveries", "ads:place_ad"):
            with self.subTest(name):
                policy = self.client.get(reverse(name))["Content-Security-Policy"]
                self.assertIn("'unsafe-eval'", policy)

    def test_public_pages_keep_the_strict_policy(self):
        policy = self.client.get("/")["Content-Security-Policy"]

        self.assertNotIn("'unsafe-eval'", policy)

    def test_dashboard_is_an_admin_shell_page_with_serving_checklist(self):
        response = self.client.get(reverse("ads:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="mf-ui"')
        self.assertContains(response, "Why am I (not) seeing ads?")
        self.assertContains(response, "The master switch is off")
        self.assertContains(response, "Placement manager")
        self.assertNotContains(response, "container-fluid")

    def test_checklist_turns_ready_when_a_creative_serves_a_placed_zone(self):
        from apps.ads.models import PlacementAssignment

        settings_row = AdsSettings.get_solo()
        settings_row.ads_enabled = True
        settings_row.save()
        placement = placement_manager.place_ad(
            user=self.manager, template_path=PAGE, zone_code="header", enabled=True
        )
        creative = AdCreative.objects.create(
            campaign=Campaign.objects.create(name="C"), name="B", creative_type="banner"
        )
        PlacementAssignment.objects.create(placement=placement, creative=creative)

        response = self.client.get(reverse("ads:dashboard"))

        self.assertContains(response, "1 placements have a live creative to show")
        self.assertNotContains(response, "To do")

    def test_removed_placements_are_not_listed(self):
        gone = placement_manager.place_ad(
            user=self.manager, template_path=PAGE, zone_code="header"
        )
        gone.soft_delete(self.manager)

        self.assertNotContains(self.client.get(reverse("ads:dashboard")), gone.name)

    def test_staff_without_placement_permission_see_no_placement_controls(self):
        self.client.force_login(make_user("viewer", staff=True))

        response = self.client.get(reverse("ads:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Place ads on templates")
        self.assertContains(response, "change ad placement")

    def test_non_staff_cannot_open_it(self):
        self.client.force_login(make_user("member"))

        self.assertEqual(self.client.get(reverse("ads:dashboard")).status_code, 302)

    def test_serving_controls_still_post_and_redirect(self):
        response = self.client.post(
            reverse("ads:toggle_settings"),
            {"ads_enabled": "1", "ad_networks_enabled": "0"},
        )

        self.assertRedirects(
            response, reverse("ads:dashboard"), fetch_redirect_response=False
        )
        self.assertTrue(AdsSettings.get_solo().ads_enabled)

from __future__ import annotations

from django.template import Context, Template
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.firmwares.models import AutomatedBlogLink, Chipset, Model
from apps.seo import api, interlinking, scan
from apps.seo.models import (
    CrawlRun,
    InterlinkExclusion,
    LinkableEntity,
    LinkSuggestion,
    PageObservation,
    PageSnapshot,
)
from apps.seo.services.internal_linking.engine import render_body_with_internal_links
from tests.backend.seo.seo_helpers import SITE, make_catalog, make_post, make_staff


def relations() -> set[str]:
    return set(
        LinkSuggestion.objects.exclude(relation="").values_list("relation", flat=True)
    )


def suggestion(relation: str) -> LinkSuggestion:
    found = LinkSuggestion.objects.filter(relation=relation).first()
    assert found is not None, relation
    return found


@override_settings(SITE_URL=SITE)
class ProposalGenerationTests(TestCase):
    def setUp(self):
        self.catalog = make_catalog()

    def test_every_catalogue_relation_yields_a_justified_proposal(self):
        report = interlinking.generate_proposals()

        self.assertGreater(report.created, 8)
        self.assertTrue(
            {
                "brand_to_model",
                "model_to_brand",
                "model_to_chipset",
                "chipset_to_model",
                "tool_to_chipset",
                "chipset_to_tool",
                "tool_to_brand",
                "brand_to_tool",
                "post_to_brand",
                "brand_to_post",
                "post_to_model",
                "model_to_post",
                "model_to_tool",
            }
            <= relations(),
            relations(),
        )
        for item in LinkSuggestion.objects.exclude(relation=""):
            self.assertNotEqual(item.source_id, item.target_id)
            self.assertEqual(item.status, LinkSuggestion.Status.PROPOSED)
            self.assertFalse(item.is_applied)
            self.assertTrue(item.reason)
            self.assertTrue(item.anchor)

    def test_regenerating_is_idempotent(self):
        interlinking.generate_proposals()
        first = LinkSuggestion.objects.count()

        again = interlinking.generate_proposals()

        self.assertEqual(again.created, 0)
        self.assertEqual(LinkSuggestion.objects.count(), first)
        self.assertEqual(
            LinkableEntity.objects.count(),
            LinkableEntity.objects.values("content_type", "object_id")
            .distinct()
            .count(),
        )

    def test_the_link_budget_caps_a_hub(self):
        for index in range(12):
            Model.objects.create(
                brand=self.catalog.brand, name=f"Phone {index}", slug=f"phone-{index}"
            )

        report = interlinking.generate_proposals()

        brand = LinkableEntity.objects.get(entity_type="brand")
        self.assertLessEqual(LinkSuggestion.objects.filter(source=brand).count(), 5)
        self.assertGreater(report.skipped_budget, 0)

    def test_anchors_rotate_for_the_same_target(self):
        interlinking.generate_proposals()

        chipset = LinkableEntity.objects.get(entity_type="chipset")
        anchors = list(
            LinkSuggestion.objects.filter(target=chipset).values_list(
                "anchor", flat=True
            )
        )

        self.assertGreaterEqual(len(anchors), 2)
        self.assertEqual(len(set(anchors)), len(anchors), anchors)

    def test_stale_proposals_are_retired_when_the_relation_disappears(self):
        interlinking.generate_proposals()
        self.assertIn("tool_to_chipset", relations())
        self.catalog.tool.chipsets.clear()

        report = interlinking.generate_proposals()

        self.assertGreater(report.retired, 0)
        self.assertNotIn("tool_to_chipset", relations())
        self.assertNotIn("model_to_tool", relations())

    def test_exclusions_block_a_pair(self):
        InterlinkExclusion.objects.create(
            phrase="Flasher", source_pattern="/firmwares/nova/", target_pattern=""
        )

        interlinking.generate_proposals()

        self.assertFalse(
            LinkSuggestion.objects.filter(
                source__entity_type="brand", target__entity_type="tool"
            ).exists()
        )

    def test_chipset_guides_link_both_ways(self):
        guide = make_post(
            "helio-g100-guide",
            "MediaTek Helio G100 flashing guide",
            self.catalog.post.author,
            self.catalog.category,
            body="<p>How to flash firmware on MediaTek Helio G100.</p>",
        )
        AutomatedBlogLink.objects.create(
            kind="chipset", chipset=self.catalog.chipset, post=guide
        )

        interlinking.generate_proposals()

        self.assertIn("chipset_to_guide", relations())
        self.assertIn("guide_to_chipset", relations())

    def test_a_site_without_the_catalogue_relations_makes_no_proposals(self):
        Chipset.objects.all().delete()
        Model.objects.all().delete()

        interlinking.generate_proposals()

        self.assertFalse(
            LinkSuggestion.objects.filter(
                relation__in=["model_to_chipset", "model_to_tool"]
            ).exists()
        )


@override_settings(SITE_URL=SITE)
class DecisionTests(TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        self.user = make_staff("linker", superuser=True)
        interlinking.generate_proposals()

    def test_accept_marks_it_applied_and_survives_regeneration(self):
        item = suggestion("post_to_model")

        interlinking.decide(item, "accept", self.user)
        interlinking.generate_proposals()

        item.refresh_from_db()
        self.assertEqual(item.status, LinkSuggestion.Status.ACCEPTED)
        self.assertTrue(item.is_applied)
        self.assertEqual(item.decided_by, self.user)
        self.assertIsNotNone(item.decided_at)

    def test_accepted_post_links_are_rendered_into_the_body(self):
        item = suggestion("post_to_model")
        interlinking.decide(item, "accept", self.user)
        body = "<p>Flash the Nova Atlas One carefully.</p>"

        rendered = render_body_with_internal_links(
            body_html=body, source_obj=self.catalog.post, max_links=3
        )

        self.assertIn(f'href="{item.target.url}"', rendered)
        unreviewed = render_body_with_internal_links(
            body_html="<p>Nova Nova</p>", source_obj=self.catalog.model, max_links=3
        )
        self.assertNotIn("<a ", unreviewed)

    def test_rejected_pairs_are_never_proposed_again(self):
        item = suggestion("brand_to_model")
        interlinking.decide(item, "reject", self.user)

        report = interlinking.generate_proposals()

        item.refresh_from_db()
        self.assertEqual(item.status, LinkSuggestion.Status.REJECTED)
        self.assertFalse(item.is_applied)
        self.assertGreater(report.skipped_rejected, 0)
        self.assertEqual(
            LinkSuggestion.objects.filter(
                source=item.source, target=item.target
            ).count(),
            1,
        )

    def test_reset_reopens_a_decision(self):
        item = suggestion("brand_to_model")
        interlinking.decide(item, "accept", self.user)

        interlinking.decide(item, "reset", self.user)

        item.refresh_from_db()
        self.assertEqual(item.status, LinkSuggestion.Status.PROPOSED)
        self.assertFalse(item.is_applied)

    def test_unknown_decisions_are_rejected(self):
        with self.assertRaises(interlinking.DecisionError):
            interlinking.decide(suggestion("brand_to_model"), "maybe", self.user)
        with self.assertRaises(interlinking.DecisionError):
            interlinking.decide_many([1], "maybe", self.user)

    def test_bulk_decisions_touch_only_the_selected(self):
        ids = list(
            LinkSuggestion.objects.exclude(relation="").values_list("pk", flat=True)[:3]
        )

        changed = interlinking.decide_many(ids, "accept", self.user)

        self.assertEqual(changed, 3)
        self.assertEqual(
            LinkSuggestion.objects.filter(
                status=LinkSuggestion.Status.ACCEPTED
            ).count(),
            3,
        )

    def test_related_links_api_and_template_tag_only_show_accepted_links(self):
        item = suggestion("model_to_chipset")
        self.assertEqual(api.related_links(self.catalog.model), [])
        interlinking.decide(item, "accept", self.user)

        links = api.related_links(self.catalog.model)

        self.assertEqual(links[0]["url"], item.target.url)
        html = Template("{% load seo_tags %}{% related_links obj %}").render(
            Context({"obj": self.catalog.model})
        )
        self.assertIn(f'href="{item.target.url}"', html)
        self.assertIn(">Related pages</h2>", html)
        empty = Template("{% load seo_tags %}{% related_links obj %}").render(
            Context({"obj": self.catalog.brand})
        )
        self.assertNotIn("<a ", empty)


class ExternalPolicyTests(TestCase):
    def test_external_links_get_nofollow_and_internal_links_do_not(self):
        html = (
            '<p><a href="https://other.example/x">out</a> '
            '<a href="https://example.org/in">same host</a> '
            '<a href="/relative">rel</a> <a href="mailto:a@b.c">mail</a> '
            '<a class="x" href="http://other.example/" rel="author">keep</a></p>'
        )

        result = interlinking.apply_external_link_policy(html, "example.org")

        self.assertIn(
            '<a href="https://other.example/x" rel="nofollow noopener noreferrer">',
            result,
        )
        self.assertIn('<a href="https://example.org/in">', result)
        self.assertIn('<a href="/relative">', result)
        self.assertIn('<a href="mailto:a@b.c">', result)
        self.assertIn('rel="author nofollow noopener noreferrer"', result)
        self.assertEqual(result.count("noopener"), 2)

    @override_settings(SITE_URL="https://example.org")
    def test_body_helper_composes_internal_links_and_the_external_policy(self):
        catalog = make_catalog()
        html = '<p>See <a href="https://other.example/">them</a>.</p>'

        result = api.link_body_html(html, catalog.post)

        self.assertIn("nofollow", result)


@override_settings(SITE_URL=SITE)
class LinkCountTests(TestCase):
    def test_render_crawl_link_counts_are_stored_on_the_inventory(self):
        PageSnapshot.objects.create(path="/a/", group="pages")
        PageSnapshot.objects.create(path="/b/", group="pages")
        PageSnapshot.objects.create(path="/c/", group="pages")
        run = CrawlRun.objects.create(base_url=SITE)
        PageObservation.objects.create(
            run=run,
            url=f"{SITE}/a/",
            internal_links=[f"{SITE}/b/", f"{SITE}/a/", f"{SITE}/c/"],
        )
        PageObservation.objects.create(
            run=run, url=f"{SITE}/b/", internal_links=[f"{SITE}/c/"]
        )

        updated = interlinking.record_link_counts(run)

        self.assertEqual(updated, 3)
        a, b, c = (PageSnapshot.objects.get(path=p) for p in ("/a/", "/b/", "/c/"))
        self.assertEqual((a.links_out, a.links_in), (2, 0))
        self.assertEqual((b.links_out, b.links_in), (1, 1))
        self.assertEqual(c.links_in, 2)


@override_settings(SITE_URL=SITE)
class InterlinkScreenTests(TestCase):
    def setUp(self):
        make_catalog()
        self.admin = make_staff("boss", superuser=True)
        self.viewer = make_staff("viewer", "view_linksuggestion")
        interlinking.generate_proposals()
        self.client.force_login(self.admin)

    def test_the_review_list_renders_in_the_admin_shell(self):
        response = self.client.get(reverse("seo:links"))

        self.assertContains(response, "mf-ui-header")
        self.assertContains(response, "Interlinking review")
        self.assertContains(response, "Generate proposals")
        self.assertContains(response, "Nova Atlas One")
        self.assertContains(response, "Accept")
        filtered = self.client.get(
            reverse("seo:links"), {"relation": "model_to_chipset", "q": "Helio"}
        )
        self.assertContains(filtered, "Model links its chipset")
        self.assertNotContains(filtered, "Brand lists its model</small>")

    def test_accept_reject_and_bulk_via_post_redirect(self):
        item = suggestion("brand_to_model")
        url = reverse("seo:link_decide", args=[item.pk, "accept"])

        response = self.client.post(url, {"next": reverse("seo:links")})

        self.assertRedirects(response, reverse("seo:links"))
        item.refresh_from_db()
        self.assertEqual(item.status, "accepted")
        ids = list(
            LinkSuggestion.objects.filter(status="proposed")
            .exclude(relation="")
            .values_list("pk", flat=True)[:2]
        )
        self.client.post(reverse("seo:links_bulk"), {"ids": ids, "decision": "reject"})
        self.assertEqual(LinkSuggestion.objects.filter(status="rejected").count(), 2)
        none = self.client.post(
            reverse("seo:links_bulk"), {"decision": "reject"}, follow=True
        )
        self.assertContains(none, "Select at least one")

    def test_generate_runs_the_engine_and_reports(self):
        LinkSuggestion.objects.all().delete()

        response = self.client.post(reverse("seo:links_generate"), follow=True)

        self.assertContains(response, "new proposals")
        self.assertTrue(LinkSuggestion.objects.exists())

    def test_permissions_and_methods(self):
        item = suggestion("brand_to_model")
        self.client.force_login(self.viewer)

        self.assertEqual(self.client.get(reverse("seo:links")).status_code, 200)
        for name, args in (
            ("link_decide", [item.pk, "accept"]),
            ("links_bulk", []),
            ("links_generate", []),
        ):
            with self.subTest(endpoint=name):
                url = reverse(f"seo:{name}", args=args)
                self.assertEqual(self.client.get(url).status_code, 405)
                self.assertEqual(self.client.post(url).status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.status, "proposed")
        page = self.client.get(reverse("seo:links"))
        self.assertNotContains(page, "Generate proposals")

    def test_an_unknown_decision_is_a_404(self):
        item = suggestion("brand_to_model")

        response = self.client.post(reverse("seo:link_decide", args=[item.pk, "nope"]))

        self.assertEqual(response.status_code, 404)

    def test_dashboard_and_inventory_show_link_data(self):
        scan.run_scan()
        self.assertContains(self.client.get(reverse("seo:dashboard")), "Link proposals")
        self.assertContains(self.client.get(reverse("seo:inventory")), "Links in")

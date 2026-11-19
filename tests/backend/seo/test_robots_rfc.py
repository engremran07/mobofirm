from __future__ import annotations

import re

from django.test import SimpleTestCase, TestCase, override_settings

from apps.seo import policy, robots
from apps.seo.models import SEOSettings
from tests.backend.seo.seo_helpers import SITE


class ParserTests(SimpleTestCase):
    """RFC 9309 matching semantics."""

    def test_longest_matching_rule_wins_regardless_of_order(self):
        rules = robots.parse(
            "User-agent: *\nDisallow: /files/\nAllow: /files/public/\n"
        )
        reverse = robots.parse(
            "User-agent: *\nAllow: /files/public/\nDisallow: /files/\n"
        )

        for parsed in (rules, reverse):
            self.assertTrue(parsed.can_fetch("AnyBot", "/files/public/a.zip"))
            self.assertFalse(parsed.can_fetch("AnyBot", "/files/private/a.zip"))

    def test_allow_wins_a_tie(self):
        parsed = robots.parse("User-agent: *\nDisallow: /page\nAllow: /page\n")

        decision = parsed.test("Bot", "/page")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.rule, robots.Rule(True, "/page"))

    def test_wildcards_and_end_anchor(self):
        parsed = robots.parse("User-agent: *\nDisallow: /*?sort=\nDisallow: /*.pdf$\n")

        self.assertFalse(parsed.can_fetch("Bot", "/list/?sort=new"))
        self.assertFalse(parsed.can_fetch("Bot", "/a/b.pdf"))
        self.assertTrue(parsed.can_fetch("Bot", "/a/b.pdf?download=1"))
        self.assertTrue(parsed.can_fetch("Bot", "/list/?page=2"))

    def test_the_most_specific_group_applies_and_star_is_the_fallback(self):
        parsed = robots.parse(
            "User-agent: *\nDisallow: /\n\n"
            "User-agent: Googlebot\nDisallow: /private/\n\n"
            "User-agent: Googlebot-Image\nDisallow: /photos/\n"
        )

        self.assertTrue(parsed.can_fetch("Googlebot", "/open/"))
        self.assertFalse(parsed.can_fetch("Googlebot", "/private/x"))
        self.assertFalse(parsed.can_fetch("googlebot-image", "/photos/a.png"))
        self.assertTrue(parsed.can_fetch("Googlebot-Image", "/private/x"))
        self.assertFalse(parsed.can_fetch("OtherBot", "/open/"))

    def test_googlebot_image_falls_back_to_googlebot_before_star(self):
        parsed = robots.parse(
            "User-agent: *\nDisallow: /\n\nUser-agent: Googlebot\nAllow: /\n"
        )

        self.assertTrue(parsed.can_fetch("Googlebot-Image", "/a.png"))

    def test_groups_for_the_same_agent_merge_and_empty_disallow_allows(self):
        parsed = robots.parse(
            "User-agent: Bot\nDisallow: /a/\n\nUser-agent: bot\nDisallow: /b/\n"
            "\nUser-agent: Free\nDisallow:\n"
        )

        self.assertFalse(parsed.can_fetch("Bot", "/a/1"))
        self.assertFalse(parsed.can_fetch("Bot", "/b/1"))
        self.assertTrue(parsed.can_fetch("Free", "/a/1"))

    def test_percent_encoding_compares_case_insensitively(self):
        parsed = robots.parse("User-agent: *\nDisallow: /caf%c3%a9/\n")

        self.assertFalse(parsed.can_fetch("Bot", "/caf%C3%A9/menu"))

    def test_no_robots_means_everything_is_allowed(self):
        decision = robots.parse("").test("Bot", "/anything")

        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.rule)

    def test_comments_sitemaps_and_crawl_delay_are_parsed(self):
        parsed = robots.parse(
            "# note\nUser-agent: bingbot # inline\nCrawl-delay: 5\nDisallow: /x\n"
            "Sitemap: https://example.org/sitemap.xml\n"
        )

        self.assertEqual(parsed.crawl_delay("bingbot"), 5)
        self.assertIsNone(parsed.crawl_delay("Googlebot"))
        self.assertEqual(parsed.sitemaps, ["https://example.org/sitemap.xml"])


@override_settings(SITE_URL=SITE)
class GeneratedRobotsTests(TestCase):
    def body(self) -> str:
        return self.client.get("/robots.txt").content.decode()

    def test_search_engines_get_explicit_groups_with_the_same_private_rules(self):
        parsed = robots.parse(self.body())

        for agent in ("Googlebot", "Googlebot-Image", "bingbot", "*"):
            with self.subTest(agent=agent):
                self.assertFalse(parsed.can_fetch(agent, "/admin/login/"))
                self.assertFalse(parsed.can_fetch(agent, "/accounts/login/"))
                self.assertTrue(parsed.can_fetch(agent, "/firmwares/nova/atlas-one/"))
        agents = {name for group in parsed.groups for name in group.agents}
        self.assertTrue({"Googlebot", "Googlebot-Image", "bingbot"} <= agents)

    def test_rules_are_ordered_most_specific_first(self):
        group = robots.parse(self.body()).groups[0]
        lengths = [len(rule.pattern) for rule in group.rules]

        self.assertEqual(lengths, sorted(lengths, reverse=True))

    def test_parameter_traps_block_duplicates_but_never_css_or_js(self):
        parsed = robots.parse(self.body())

        for url in (
            "/firmwares/browse/?sort=newest",
            "/firmwares/browse/?q=a&sort=newest",
            "/blog/?utm_source=x",
            "/blog/?sessionid=abc",
        ):
            with self.subTest(url=url):
                self.assertFalse(parsed.can_fetch("Googlebot", url))
        for url in (
            "/static/build/app.css",
            "/static/build/app.js?v=3",
            "/media/brands/logo.png",
            "/firmwares/browse/?page=2",
        ):
            with self.subTest(url=url):
                self.assertTrue(parsed.can_fetch("Googlebot", url))
                self.assertTrue(parsed.can_fetch("Googlebot-Image", url))

    def test_every_documented_ai_crawler_token_is_in_policy_and_follows_settings(self):
        expected = {
            "GPTBot",
            "OAI-SearchBot",
            "ChatGPT-User",
            "ClaudeBot",
            "Claude-SearchBot",
            "Claude-User",
            "anthropic-ai",
            "Google-Extended",
            "PerplexityBot",
            "Perplexity-User",
            "CCBot",
            "Applebot-Extended",
            "Bytespider",
            "Meta-ExternalAgent",
            "Amazonbot",
        }
        known = {
            *policy.AI_SEARCH_BOTS,
            *policy.AI_USER_AGENTS,
            *policy.AI_TRAINING_BOTS,
        }
        self.assertTrue(expected <= known)
        row = SEOSettings.get_solo()
        row.allow_ai_search_bots = False
        row.allow_ai_user_agents = False
        row.allow_ai_training_bots = False
        row.save()

        parsed = robots.parse(self.body())

        for agent in known:
            with self.subTest(agent=agent):
                self.assertFalse(parsed.can_fetch(agent, "/firmwares/"))
        self.assertTrue(parsed.can_fetch("Googlebot", "/firmwares/"))

    def test_crawl_delay_only_for_bots_that_honour_it(self):
        self.assertNotIn("Crawl-delay:", self.body())
        row = SEOSettings.get_solo()
        row.robots_crawl_delay = 7
        row.save()

        parsed = robots.parse(self.body())

        self.assertEqual(parsed.crawl_delay("bingbot"), 7)
        self.assertIsNone(parsed.crawl_delay("Googlebot"))
        self.assertIsNone(parsed.crawl_delay("*"))
        self.assertEqual(
            len(re.findall(r"^Crawl-delay:", self.body(), re.MULTILINE)), 1
        )

    def test_header_comment_size_and_sitemap_lines(self):
        body = self.body()

        self.assertTrue(body.startswith("# robots.txt generated by Mobofirm SEO"))
        self.assertLess(len(body.encode()), robots.MAX_BYTES)
        self.assertEqual(robots.validate(body), [])
        self.assertEqual(
            re.findall(r"^Sitemap: .+$", body, re.MULTILINE),
            [f"Sitemap: {SITE}/sitemap.xml"],
        )

    def test_etag_conditional_get_head_and_cache_headers(self):
        first = self.client.get("/robots.txt")

        again = self.client.get("/robots.txt", headers={"If-None-Match": first["ETag"]})
        head = self.client.head("/robots.txt")

        self.assertEqual(first["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn("stale-while-revalidate", first["Cache-Control"])
        self.assertEqual(again.status_code, 304)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head["ETag"], first["ETag"])

    def test_sitemap_line_disappears_when_sitemaps_are_disabled(self):
        row = SEOSettings.get_solo()
        row.sitemap_enabled = False
        row.save()

        self.assertNotIn("Sitemap:", self.body())

    def test_diff_between_generated_files_shows_what_changed(self):
        row = SEOSettings.get_solo()
        before = robots.generate(row, SITE)
        row.allow_ai_training_bots = True
        after = robots.generate(row, SITE)

        changed = robots.diff_lines(before, after)

        self.assertTrue(any(line.startswith("-User-agent: GPTBot") for line in changed))
        self.assertEqual(robots.diff_lines(after, after), [])

    def test_the_tester_reports_the_rule_and_group_that_matched(self):
        parsed = robots.parse(self.body())

        blocked = parsed.test("Googlebot", "/admin/login/")
        allowed = parsed.test("Googlebot", "/firmwares/")

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.rule, robots.Rule(False, "/admin/"))
        self.assertEqual(blocked.agent, "googlebot")
        self.assertIn("Disallow: /admin/", blocked.reason)
        self.assertTrue(allowed.allowed)
        self.assertIsNone(allowed.rule)

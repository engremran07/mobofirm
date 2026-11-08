"""Featured and paid posts: gated rendering, teaser, no leakage, buy dialog."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, TypedDict
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog import paywall, selectors
from apps.blog.models import BlogSettings, Post, PostAccess, PostStatus
from apps.core.app_service import AppService


class HtmxExtra(TypedDict):
    HTTP_HX_REQUEST: str


HTMX = HtmxExtra(HTTP_HX_REQUEST="true")
HIDDEN = "SECRETTAIL"
BODY = (
    "<p>"
    + " ".join(f"word{index}" for index in range(300))
    + f" {HIDDEN}</p><h2>Hidden heading</h2><p>More hidden text.</p>"
)


class FakeShop:
    """Stands in for ``apps.shop.api`` (the real one is owned by the shop)."""

    def __init__(self, *, allowed: set[int] | None = None, credit_price: int = 120):
        self.allowed = allowed if allowed is not None else set()
        self.credit_price = credit_price
        self.purchases: list[tuple[int, int, Any, str]] = []
        self.deny_purchase = ""

    def featured_blog_access(self, user, post_id, price_usd):
        if post_id in self.allowed:
            return SimpleNamespace(
                allowed=True, via="purchase", credit_price=None, reason=""
            )
        return SimpleNamespace(
            allowed=False,
            via="",
            credit_price=self.credit_price,
            reason="Buy it with credits or get a package.",
        )

    def purchase_featured_blog(self, user, post_id, price_usd, key):
        self.purchases.append((user.pk, post_id, price_usd, key))
        if self.deny_purchase:
            return SimpleNamespace(
                allowed=False, via="", credit_price=None, reason=self.deny_purchase
            )
        self.allowed.add(post_id)
        return SimpleNamespace(
            allowed=True, via="purchase", credit_price=self.credit_price, reason=""
        )


def patch_shop(shop: FakeShop | None):
    real_get = AppService.get

    def only_shop(label):
        return shop if label == "shop" else real_get(label)

    return mock.patch("apps.blog.shop_gateway.AppService.get", side_effect=only_shop)


class GatedPostBase(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="gp-author", email="gp-author@example.test"
        )
        self.reader = user_model.objects.create_user(
            username="gp-reader", email="gp-reader@example.test"
        )
        self.staff = user_model.objects.create_user(
            username="gp-staff", email="gp-staff@example.test", is_staff=True
        )
        self.post = Post.objects.create(
            title="Premium unlock guide",
            summary="",
            body=BODY,
            author=self.author,
            status=PostStatus.PUBLISHED,
            access=PostAccess.FEATURED,
            price=Decimal("4.00"),
        )
        self.url = self.post.get_absolute_url()


class GatedRenderingTests(GatedPostBase):
    def test_anonymous_sees_teaser_and_paywall_not_body(self):
        with patch_shop(FakeShop()):
            page = self.client.get(self.url)

        self.assertContains(page, "word0 word1")
        self.assertContains(page, 'class="article-paywall')
        self.assertContains(page, "Sign in to read")
        self.assertNotContains(page, HIDDEN)
        self.assertNotContains(page, "word250")
        self.assertNotContains(page, "Hidden heading")
        self.assertIn("private", page["Cache-Control"])

    def test_teaser_is_never_the_whole_short_article(self):
        short = Post.objects.create(
            title="Tiny premium note",
            body="<p>" + " ".join(f"short{i}" for i in range(20)) + "</p>",
            author=self.author,
            status=PostStatus.PUBLISHED,
            access=PostAccess.PAID,
            price=Decimal("1.00"),
        )
        teaser = paywall.teaser_text(short)

        self.assertLess(len(teaser.split()), 20)
        self.assertNotIn("short19", teaser)

    def test_structured_data_marks_the_article_paywalled(self):
        with patch_shop(FakeShop()):
            page = self.client.get(self.url)

        html = page.content.decode()
        self.assertIn("isAccessibleForFree", html)
        self.assertIn(".article-paywall", html)
        self.assertNotIn(HIDDEN, html)

    def test_free_post_is_unaffected(self):
        free = Post.objects.create(
            title="Open guide for everyone",
            body=BODY,
            author=self.author,
            status=PostStatus.PUBLISHED,
        )

        page = self.client.get(free.get_absolute_url())

        self.assertContains(page, HIDDEN)
        self.assertNotContains(page, "article-paywall")
        self.assertNotIn("private", page.get("Cache-Control", ""))

    def test_signed_in_without_access_sees_buy_button(self):
        self.client.force_login(self.reader)
        with patch_shop(FakeShop(credit_price=120)):
            page = self.client.get(self.url)

        self.assertContains(page, "Unlock for 120 credits")
        self.assertContains(
            page, reverse("blog:purchase_dialog", args=[self.post.slug])
        )
        self.assertNotContains(page, HIDDEN)

    def test_shop_absent_shows_teaser_without_buy_button(self):
        self.client.force_login(self.reader)
        with patch_shop(None):
            page = self.client.get(self.url)

        self.assertContains(page, 'class="article-paywall')
        self.assertNotContains(page, "Unlock for")
        self.assertNotContains(page, HIDDEN)

    def test_author_and_staff_read_the_full_article(self):
        for user in (self.author, self.staff):
            client = Client()
            client.force_login(user)
            with patch_shop(None):
                page = client.get(self.url)
            self.assertContains(page, HIDDEN)
            self.assertNotContains(page, 'class="article-paywall')

    def test_buyer_or_package_holder_reads_the_full_article(self):
        self.client.force_login(self.reader)
        with patch_shop(FakeShop(allowed={self.post.pk})):
            page = self.client.get(self.url)

        self.assertContains(page, HIDDEN)
        self.assertNotContains(page, 'class="article-paywall')

    def test_comments_are_hidden_while_locked(self):
        with patch_shop(FakeShop()):
            page = self.client.get(self.url)

        self.assertNotContains(page, 'id="comments"')

    def test_free_access_clears_price_fields(self):
        self.post.access = PostAccess.FREE
        self.post.save()
        self.post.refresh_from_db()

        self.assertIsNone(self.post.price)
        self.assertIsNone(self.post.proposed_price)


class NoLeakageTests(GatedPostBase):
    def test_rss_atom_and_json_feeds_carry_only_the_teaser(self):
        for name in ("blog:feed_rss", "blog:feed_atom", "blog:feed_json"):
            response = self.client.get(reverse(name))
            text = response.content.decode()
            self.assertIn("word0", text, name)
            self.assertNotIn(HIDDEN, text, name)
            self.assertNotIn("word250", text, name)
            self.assertNotIn("Hidden heading", text, name)

    def test_json_feed_labels_the_access(self):
        items = self.client.get(reverse("blog:feed_json")).json()["items"]

        self.assertEqual(items[0]["_access"], "featured")

    def test_listing_cards_use_the_teaser_when_there_is_no_summary(self):
        card = selectors.build_card(self.post)

        self.assertIn("word0", card.excerpt)
        self.assertNotIn(HIDDEN, card.excerpt)
        self.assertNotIn("word250", card.excerpt)

    def test_search_does_not_match_gated_body_text(self):
        hits = selectors.apply_search(selectors.live_posts(), HIDDEN)[0]
        self.assertFalse(hits.filter(pk=self.post.pk).exists())

        page = self.client.get(reverse("blog:post_list"), {"q": HIDDEN})
        self.assertEqual(page.context["total"], 0)

    def test_search_still_matches_gated_title(self):
        hits = selectors.apply_search(selectors.live_posts(), "Premium unlock")[0]

        self.assertTrue(hits.filter(pk=self.post.pk).exists())

    def test_global_search_provider_snippet_has_no_body(self):
        from apps.blog.search_providers import BlogPostProvider

        provider = BlogPostProvider()
        hit = provider.build_hit(self.post, 1.0, mock.Mock())

        self.assertNotIn(HIDDEN, hit.subtitle)
        self.assertNotIn("word250", hit.subtitle)

    def test_sitemap_lists_the_url_but_no_text(self):
        response = self.client.get("/sitemap.xml")
        text = response.content.decode()

        self.assertNotIn(HIDDEN, text)
        if response.status_code == 200 and "blog" in text:
            self.assertNotIn("word0", text)

    def test_meta_description_uses_teaser(self):
        with patch_shop(None):
            page = self.client.get(self.url)

        for tag in page.content.decode().split("<meta ")[1:]:
            if 'name="description"' in tag or 'property="og:description"' in tag:
                self.assertNotIn(HIDDEN, tag)
                self.assertNotIn("word250", tag)

    def test_scheduled_post_body_is_not_reachable(self):
        self.post.status = PostStatus.SCHEDULED
        self.post.publish_at = timezone.now() + timedelta(days=1)
        self.post.save()
        self.client.force_login(get_user_model().objects.get(username="gp-reader"))

        self.assertEqual(self.client.get(self.url).status_code, 404)


class PurchaseDialogTests(GatedPostBase):
    def dialog_url(self) -> str:
        return reverse("blog:purchase_dialog", args=[self.post.slug])

    def test_anonymous_is_sent_to_sign_in(self):
        response = self.client.get(self.dialog_url(), **HTMX)

        self.assertEqual(response.status_code, 204)
        self.assertIn("login", response["HX-Redirect"])

    def test_dialog_shows_price_and_posts_idempotent_key(self):
        shop = FakeShop(credit_price=120)
        self.client.force_login(self.reader)
        with patch_shop(shop):
            page = self.client.get(self.dialog_url(), **HTMX)
            self.assertContains(page, "120 credits")
            self.assertContains(page, "csrfmiddlewaretoken")
            response = self.client.post(self.dialog_url(), **HTMX)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], self.url)
        key = f"blog-featured:{self.reader.pk}:{self.post.pk}"
        self.assertEqual(
            shop.purchases, [(self.reader.pk, self.post.pk, Decimal("4.00"), key)]
        )

    def test_repeating_the_purchase_never_charges_twice(self):
        shop = FakeShop()
        self.client.force_login(self.reader)
        with patch_shop(shop):
            self.client.post(self.dialog_url(), **HTMX)
            self.client.post(self.dialog_url(), **HTMX)

        self.assertEqual(len(shop.purchases), 1)

    def test_failed_purchase_shows_error_and_does_not_unlock(self):
        shop = FakeShop()
        shop.deny_purchase = "You need 120 credits."
        self.client.force_login(self.reader)
        with patch_shop(shop):
            response = self.client.post(self.dialog_url(), **HTMX)
            page = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You need 120 credits.")
        self.assertNotContains(page, HIDDEN)

    def test_package_holder_is_not_charged(self):
        shop = FakeShop(allowed={self.post.pk})
        self.client.force_login(self.reader)
        with patch_shop(shop):
            response = self.client.post(self.dialog_url(), **HTMX)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(shop.purchases, [])

    def test_unavailable_shop_refuses_inside_the_dialog(self):
        self.client.force_login(self.reader)
        with patch_shop(None):
            response = self.client.post(self.dialog_url(), **HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "unlocked")

    def test_free_post_has_no_purchase_dialog(self):
        free = Post.objects.create(
            title="Free post for dialog test",
            body=BODY,
            author=self.author,
            status=PostStatus.PUBLISHED,
        )
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("blog:purchase_dialog", args=[free.slug]), **HTMX
        )

        self.assertEqual(response.status_code, 404)

    def test_unpublished_post_has_no_purchase_dialog(self):
        self.post.status = PostStatus.DRAFT
        self.post.save()
        self.client.force_login(self.reader)

        self.assertEqual(self.client.get(self.dialog_url(), **HTMX).status_code, 404)


class TeaserSettingTests(GatedPostBase):
    def test_teaser_length_follows_blog_settings(self):
        settings_row = BlogSettings.get_solo()
        settings_row.teaser_words = 25
        settings_row.save()

        words = paywall.teaser_text(self.post).rstrip("…").split()

        self.assertEqual(len(words), 25)

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.tags.models import Tag, TagsSettings
from apps.tags.models_keyword import KeywordProvider, KeywordSuggestion


class TagViewTestCase(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="tv-author", email="tv-author@example.test", full_name="Tag Writer"
        )
        self.staff = user_model.objects.create_user(
            username="tv-staff", email="tv-staff@example.test", is_staff=True
        )
        self.staff.user_permissions.add(Permission.objects.get(codename="change_tag"))
        self.member = user_model.objects.create_user(
            username="tv-member", email="tv-member@example.test"
        )
        self.tag = Tag.objects.create(
            name="Bootloader", description="Locking and unlocking.", is_curated=True
        )

    def publish(self, title, *tags):
        post = Post.objects.create(
            title=title,
            body=f"<p>{title} body with enough words.</p>",
            author=self.author,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now() - timedelta(minutes=1),
        )
        post.tags.add(*tags)
        return post


class PublicTagPageTests(TagViewTestCase):
    def test_tag_page_lists_live_posts_as_cards_with_public_author_names(self):
        live = self.publish("Unlocking the bootloader", self.tag)
        Post.objects.create(
            title="Draft about bootloader",
            body="<p>Draft body that is long enough.</p>",
            author=self.author,
        ).tags.add(self.tag)

        response = self.client.get(
            reverse("tags:detail", kwargs={"slug": self.tag.slug})
        )

        self.assertContains(response, live.title)
        self.assertContains(response, "Tag Writer")
        self.assertNotContains(response, "Draft about bootloader")
        self.assertNotContains(response, "tv-author@example.test")

    def test_tag_page_htmx_fragment_and_related_topics(self):
        other = Tag.objects.create(name="Fastboot", is_curated=True)
        self.publish("Fastboot and bootloader", self.tag, other)

        full = self.client.get(reverse("tags:detail", kwargs={"slug": self.tag.slug}))
        fragment = self.client.get(
            reverse("tags:detail", kwargs={"slug": self.tag.slug}),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(full, "Related")
        self.assertContains(full, "#Fastboot")
        self.assertNotContains(fragment, "<!doctype html>")
        self.assertContains(fragment, "Fastboot and bootloader")

    def test_tag_list_filters_and_shows_live_counts(self):
        self.publish("Counted post", self.tag)
        Tag.objects.create(name="Unrelated", is_curated=True)

        listing = self.client.get(reverse("tags:list"), {"q": "boot"})

        self.assertContains(listing, "Bootloader")
        self.assertNotContains(listing, "Unrelated")
        self.assertContains(listing, "1 article")

    def test_tag_list_htmx_returns_only_the_results(self):
        response = self.client.get(reverse("tags:list"), HTTP_HX_REQUEST="true")

        self.assertNotContains(response, "<!doctype html>")
        self.assertContains(response, "Bootloader")

    def test_search_endpoint_is_public_curated_only_and_bounded(self):
        Tag.objects.create(name="Boot hidden", ai_suggested=True)

        response = self.client.get(reverse("tags:search"), {"q": "boot"})

        self.assertEqual(
            [item["name"] for item in response.json()["items"]], ["Bootloader"]
        )


class TagStaffToolTests(TagViewTestCase):
    def test_staff_tools_require_the_tag_permission(self):
        urls = [
            reverse("tags:manage"),
            reverse("tags:duplicates"),
            reverse("tags:duplicates_review"),
            reverse("tags:keyword_review"),
        ]
        for user in (None, self.member):
            if user:
                self.client.force_login(user)
            for url in urls:
                self.assertIn(self.client.get(url).status_code, {302, 403, 404}, url)

        self.client.force_login(self.staff)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_review_approves_and_rejects_suggestions_via_post(self):
        suggestion = Tag.objects.create(name="Suggested", ai_suggested=True)
        self.client.force_login(self.staff)
        url = reverse("tags:review")

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {"tag_id": suggestion.pk, "decision": "approve"})
        suggestion.refresh_from_db()
        self.assertTrue(suggestion.is_curated and suggestion.is_active)
        self.assertFalse(suggestion.ai_suggested)

        self.client.post(url, {"tag_id": suggestion.pk, "decision": "reject"})
        suggestion.refresh_from_db()
        self.assertFalse(suggestion.is_curated or suggestion.is_active)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_merge_needs_confirmation_and_is_atomic(self):
        source = Tag.objects.create(name="Boot loader", is_curated=True)
        post = self.publish("Merged post", source)
        self.client.force_login(self.staff)
        url = reverse("tags:merge")

        preview = self.client.get(url, {"source": source.slug, "target": self.tag.slug})
        self.assertContains(preview, "This will change 1 article")
        post.refresh_from_db()
        self.assertIn(source, post.tags.all())

        self.client.post(
            url, {"source": source.slug, "target": self.tag.slug, "confirm": "1"}
        )

        source.refresh_from_db()
        self.assertEqual(source.merge_into, self.tag)
        self.assertTrue(source.is_deleted)
        self.assertIn(self.tag, post.tags.all())
        self.assertNotIn(source, post.tags.all())

    def test_merge_rejects_self_merges_and_unconfirmed_posts(self):
        self.client.force_login(self.staff)
        url = reverse("tags:merge")

        self.client.post(
            url, {"source": self.tag.slug, "target": self.tag.slug, "confirm": "1"}
        )
        self.tag.refresh_from_db()
        self.assertIsNone(self.tag.merge_into)

        other = Tag.objects.create(name="Other", is_curated=True)
        page = self.client.post(url, {"source": other.slug, "target": self.tag.slug})
        self.assertContains(page, "Confirm tag merge")
        other.refresh_from_db()
        self.assertIsNone(other.merge_into)

    def test_merge_is_forbidden_without_the_permission(self):
        other = Tag.objects.create(name="Other", is_curated=True)
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("tags:merge"),
            {"source": other.slug, "target": self.tag.slug, "confirm": "1"},
        )

        self.assertEqual(response.status_code, 403)
        other.refresh_from_db()
        self.assertIsNone(other.merge_into)

    def test_duplicate_search_validates_its_inputs(self):
        Tag.objects.create(name="Boot loader", is_curated=True)
        self.client.force_login(self.staff)

        junk = self.client.get(
            reverse("tags:duplicates"), {"threshold": "abc", "limit": "x"}
        )
        found = self.client.get(reverse("tags:duplicates"), {"threshold": "0.5"})

        self.assertEqual(junk.status_code, 200)
        self.assertTrue(found.json()["items"])

    def test_applying_a_keyword_creates_a_curated_tag_with_a_safe_slug(self):
        provider = KeywordProvider.objects.create(name="Manual")
        keyword = KeywordSuggestion.objects.create(
            provider=provider,
            keyword="Odin 3 / Flash Tool!",
            normalized="odin 3 / flash tool!",
        )
        self.client.force_login(self.staff)

        self.client.post(reverse("tags:apply_keyword"), {"id": keyword.pk})

        tag = Tag.objects.get(name="Odin 3 / Flash Tool!")
        self.assertTrue(tag.is_curated)
        self.assertEqual(tag.slug, "odin-3-flash-tool")
        self.assertFalse(KeywordSuggestion.objects.exists())
        self.assertEqual(
            self.client.get(
                reverse("tags:detail", kwargs={"slug": tag.slug})
            ).status_code,
            200,
        )


class SuggestEndpointTests(TagViewTestCase):
    def test_suggestions_respect_the_setting_login_and_throttle(self):
        url = reverse("tags:suggest")
        self.assertEqual(self.client.post(url, {"text": "x"}).status_code, 302)

        self.client.force_login(self.author)
        ok = self.client.post(url, {"text": "Samsung Galaxy A15 flashing guide"})
        self.assertTrue(ok.json()["ok"])
        self.assertEqual(self.client.post(url, {"text": ""}).status_code, 400)
        with patch("apps.tags.views.allow_action", return_value=False):
            self.assertEqual(
                self.client.post(url, {"text": "abc def"}).status_code, 429
            )

        TagsSettings.objects.update_or_create(
            pk=1, defaults={"allow_public_suggestions": False}
        )
        self.assertEqual(self.client.post(url, {"text": "abc"}).status_code, 403)

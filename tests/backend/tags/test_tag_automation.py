from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.blog.models import Category, Post, PostStatus
from apps.comments.models import Comment
from apps.firmwares.models import Brand, Model
from apps.tags import tasks as tag_tasks
from apps.tags.models import Tag, TagsSettings
from apps.tags.services import (
    assign_catalog_tags,
    curate_suggested_tags,
    ensure_curated_tag,
    merge_tags,
    refresh_usage_counts,
    suggest_tags_from_text,
    sync_post_tags,
)


def curated_tag(name: str) -> Tag:
    tag = ensure_curated_tag(name)
    assert tag is not None
    return tag


class TagAutomationTestCase(TestCase):
    def setUp(self):
        self.author = get_user_model().objects.create_user(
            username="tag-auto-author", email="tag-auto-author@example.test"
        )

    def publish(self, title="Automated tagging", body="<p>Body text.</p>", **extra):
        return Post.objects.create(
            title=title,
            body=body,
            author=self.author,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now(),
            **extra,
        )


class CuratedTagTests(TagAutomationTestCase):
    def test_ensure_curated_tag_is_idempotent_and_reactivates_suggestions(self):
        suggestion = Tag.objects.create(name="Odin", ai_suggested=True)

        first = curated_tag("  Odin ")
        second = curated_tag("odin")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.pk, suggestion.pk)
        first.refresh_from_db()
        self.assertTrue(first.is_curated and first.is_active)
        self.assertFalse(first.ai_suggested)
        self.assertEqual(Tag.objects.count(), 1)

    def test_ensure_curated_tag_follows_merges_and_rejects_blank_names(self):
        target = Tag.objects.create(name="Samsung", is_curated=True)
        source = Tag.objects.create(name="Samsung Mobile", is_curated=True)
        merge_tags(source, target)

        self.assertEqual(curated_tag("Samsung Mobile").pk, target.pk)
        self.assertIsNone(ensure_curated_tag("   "))

    def test_assign_catalog_tags_sets_exactly_the_given_curated_tags(self):
        post = self.publish()
        stale = curated_tag("Stale")
        post.tags.add(stale)

        tags = assign_catalog_tags(post, ["Samsung", "Samsung Galaxy A15", "Samsung"])

        self.assertEqual({t.name for t in tags}, {"Samsung", "Samsung Galaxy A15"})
        self.assertEqual(
            set(post.tags.values_list("name", flat=True)),
            {"Samsung", "Samsung Galaxy A15"},
        )
        self.assertTrue(all(t.is_curated for t in post.tags.all()))
        again = assign_catalog_tags(post, ["Samsung", "Samsung Galaxy A15"])
        self.assertEqual(len(again), 2)

    def test_usage_counts_follow_live_posts_only(self):
        tag = curated_tag("Counted")
        live = self.publish("Live one")
        draft = Post.objects.create(
            title="Draft one", body="<p>Draft body.</p>", author=self.author
        )
        live.tags.add(tag)
        draft.tags.add(tag)

        refresh_usage_counts([tag.pk])

        tag.refresh_from_db()
        self.assertEqual(tag.usage_count, 1)


class PostTagSyncTests(TagAutomationTestCase):
    def test_publishing_attaches_catalog_tags_after_commit_without_manual_steps(self):
        brand = Brand.objects.create(name="Nokia", slug="nokia")
        model = Model.objects.create(brand=brand, name="G21", slug="g21")
        category = Category.objects.create(name="Nokia Guides")

        with self.captureOnCommitCallbacks(execute=True):
            post = self.publish(
                "Nokia G21 guide",
                firmware_brand=brand,
                firmware_model=model,
                category=category,
            )

        names = set(post.tags.values_list("name", flat=True))
        self.assertTrue({"Nokia", "Nokia G21", "Nokia Guides"} <= names)
        self.assertTrue(all(tag.is_curated for tag in post.tags.all()))

    def test_sync_is_idempotent_and_keeps_editorial_tags(self):
        brand = Brand.objects.create(name="Tecno", slug="tecno")
        post = self.publish("Tecno note", firmware_brand=brand)
        manual = curated_tag("Editor pick")
        post.tags.add(manual)

        sync_post_tags(post)
        before = set(post.tags.values_list("pk", flat=True))
        sync_post_tags(post)

        self.assertEqual(set(post.tags.values_list("pk", flat=True)), before)
        self.assertIn(manual.pk, before)
        self.assertEqual(Tag.objects.filter(name="Tecno").count(), 1)

    def test_existing_curated_tags_are_matched_from_the_text(self):
        flashing = curated_tag("Flashing")
        post = self.publish("Flashing a device safely", body="<p>Flashing steps.</p>")

        sync_post_tags(post)

        self.assertIn(flashing, post.tags.all())

    def test_unknown_keywords_become_uncurated_suggestions_not_attached_tags(self):
        post = self.publish(
            "Using Zorblax Utility", body="<p>The Zorblax Utility is handy.</p>"
        )

        sync_post_tags(post)

        suggestion = Tag.objects.get(normalized_name="zorblax utility")
        self.assertFalse(suggestion.is_curated)
        self.assertTrue(suggestion.ai_suggested)
        self.assertNotIn(suggestion, post.tags.all())

    def test_automation_can_be_switched_off(self):
        TagsSettings.objects.update_or_create(
            pk=1, defaults={"enable_ai_suggestions": False}
        )
        brand = Brand.objects.create(name="Itel", slug="itel")
        post = self.publish("Itel note", firmware_brand=brand)

        self.assertEqual(sync_post_tags(post), [])
        self.assertFalse(post.tags.exists())

    def test_task_is_safe_to_run_twice_and_for_missing_posts(self):
        brand = Brand.objects.create(name="Vivo", slug="vivo")
        post = self.publish("Vivo note", firmware_brand=brand)

        first = tag_tasks.sync_post_tags(post.pk)
        second = tag_tasks.sync_post_tags(post.pk)

        self.assertEqual(first, second)
        self.assertEqual(tag_tasks.sync_post_tags(999999), 0)

    def test_comments_never_change_post_tags(self):
        post = self.publish("Comment safe", body="<p>Plain body here.</p>")
        sync_post_tags(post)
        before = set(post.tags.values_list("pk", flat=True))

        Comment.objects.create(
            post=post,
            user=self.author,
            body="Mentions unrelated Qualcomm terms.",
            status=Comment.Status.APPROVED,
        )

        self.assertEqual(set(post.tags.values_list("pk", flat=True)), before)


class CurationTests(TagAutomationTestCase):
    def test_a_suggestion_is_promoted_once_enough_live_posts_mention_it(self):
        suggestion = Tag.objects.create(
            name="Helio G99", normalized_name="helio g99", ai_suggested=True
        )
        for index in range(2):
            self.publish(f"Helio G99 note {index}")
        self.assertEqual(curate_suggested_tags(), [])

        third = self.publish("Helio G99 note 3")
        promoted = curate_suggested_tags()

        suggestion.refresh_from_db()
        self.assertEqual(promoted, [suggestion.pk])
        self.assertTrue(suggestion.is_curated)
        self.assertEqual(curate_suggested_tags(), [])

        attached = tag_tasks.reattach_curated_tag(suggestion.pk)
        self.assertEqual(attached, 3)
        self.assertIn(suggestion, third.tags.all())
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.usage_count, 3)

    def test_rejected_or_deleted_suggestions_are_never_promoted(self):
        Tag.objects.create(name="Rejected", ai_suggested=True, is_deleted=True)
        for index in range(3):
            self.publish(f"Rejected note {index}")

        self.assertEqual(curate_suggested_tags(), [])


class KeywordExtractionTests(TestCase):
    def test_extraction_is_deterministic_and_skips_stopwords(self):
        text = (
            "Samsung Galaxy A15 firmware download. Samsung Galaxy A15 flashing guide."
        )

        first = suggest_tags_from_text(text)
        second = suggest_tags_from_text(text)

        self.assertEqual(first, second)
        names = [item["normalized"] for item in first]
        self.assertIn("samsung galaxy a15", names)
        self.assertNotIn("the", names)
        self.assertEqual(suggest_tags_from_text(""), [])

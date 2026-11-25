from __future__ import annotations

from typing import ClassVar

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PostSourceMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("blog", "0011_community_contributions"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("blog", "0012_remove_autotopic_blog_autotopic_status_idx_and_more"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_source_flags_map_to_the_canonical_source(self):
        old_apps = self.migrate(self.migrate_from)
        Post = old_apps.get_model("blog", "Post")
        author = get_user_model().objects.create_user(
            username="source-migration-author",
            email="source-migration-author@example.test",
        )
        automated = Post.objects.create(
            title="Automated legacy post",
            slug="automated-legacy-post",
            body="<p>Automatically refreshed catalog content.</p>",
            author_id=author.pk,
            is_ai_generated=True,
        )
        community = Post.objects.create(
            title="Community legacy post",
            slug="community-legacy-post",
            body="<p>Member submitted content for review.</p>",
            author_id=author.pk,
            is_community_contribution=True,
        )
        editorial = Post.objects.create(
            title="Editorial legacy post",
            slug="editorial-legacy-post",
            body="<p>Human authored editorial content.</p>",
            author_id=author.pk,
        )

        new_apps = self.migrate(self.migrate_to)
        migrated_post = new_apps.get_model("blog", "Post")

        self.assertEqual(
            migrated_post.objects.get(pk=automated.pk).content_source, "automated"
        )
        self.assertEqual(
            migrated_post.objects.get(pk=community.pk).content_source,
            "community_contribution",
        )
        self.assertEqual(
            migrated_post.objects.get(pk=editorial.pk).content_source, "editorial"
        )

        old_apps = self.migrate(self.migrate_from)
        restored_post = old_apps.get_model("blog", "Post")
        self.assertTrue(restored_post.objects.get(pk=automated.pk).is_ai_generated)
        self.assertTrue(
            restored_post.objects.get(pk=community.pk).is_community_contribution
        )
        self.assertFalse(restored_post.objects.get(pk=editorial.pk).is_ai_generated)

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase

ROOT = Path(settings.BASE_DIR)
HEAD_TAGS = re.compile(
    r"<title|name=[\"']robots[\"']|name=[\"']description[\"']|rel=[\"']canonical[\"']"
    r"|property=[\"']og:|name=[\"']twitter:|application/ld\+json|hreflang"
)


class SingleSourceTests(SimpleTestCase):
    def test_only_the_seo_head_template_emits_the_managed_head_tags(self):
        allowed = {Path("templates/seo/head.html")}
        offenders: list[str] = []
        roots = [ROOT / "templates", *ROOT.glob("apps/*/templates")]
        for root in roots:
            for template in root.rglob("*.html"):
                relative = template.relative_to(ROOT)
                if relative in allowed or any(
                    part in relative.as_posix()
                    for part in ("templates/admin", "templates/unfold")
                ):
                    continue
                if HEAD_TAGS.search(template.read_text(encoding="utf-8")):
                    offenders.append(relative.as_posix())

        self.assertEqual(offenders, [])

    def test_base_template_renders_the_head_through_the_single_tag(self):
        base = (ROOT / "templates/base.html").read_text(encoding="utf-8")

        self.assertEqual(base.count("{% seo_head %}"), 1)

    def test_no_outbound_http_exists_in_the_seo_app_outside_the_safe_fetcher(self):
        pattern = re.compile(
            r"^\s*(import|from)\s+(requests|httpx|urllib\.request|urllib3|aiohttp)\b"
            r"|\burlopen\(|\brequests\.(get|post|head|put|request)\(|http\.client",
            re.MULTILINE,
        )
        offenders = [
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "apps/seo").rglob("*.py")
            if path.name != "fetcher.py"
            and "migrations" not in path.parts
            and pattern.search(path.read_text(encoding="utf-8"))
        ]

        self.assertEqual(offenders, [])

    def test_seo_app_has_one_settings_model_and_no_fake_score_fields(self):
        from django.apps import apps

        models = {model.__name__ for model in apps.get_app_config("seo").get_models()}

        self.assertIn("SEOSettings", models)
        self.assertNotIn("SeoAutomationSettings", models)
        self.assertNotIn("SchemaEntry", models)
        self.assertNotIn("BatchOperation", models)
        for model in apps.get_app_config("seo").get_models():
            names = {field.name for field in model._meta.get_fields()}
            self.assertFalse({"serp_score", "seo_score", "ranking_score"} & names)


class MigrationPreservationTests(TransactionTestCase):
    """The consolidation migration must keep valid administrator data."""

    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (("seo", "0001_initial"),)
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("seo", "0002_seo_control_center"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def test_redirects_metadata_and_settings_survive_the_upgrade(self):
        old_apps = self.migrate(self.migrate_from)
        Redirect = old_apps.get_model("seo", "Redirect")
        SEOModel = old_apps.get_model("seo", "SEOModel")
        Metadata = old_apps.get_model("seo", "Metadata")
        Settings = old_apps.get_model("seo", "SEOSettings")
        ContentType = old_apps.get_model("contenttypes", "ContentType")
        Redirect.objects.create(source="/legacy/", target="/current/")
        content_type = ContentType.objects.first()
        seo = SEOModel.objects.create(content_type=content_type, object_id="7")
        Metadata.objects.create(seo=seo, meta_title="Kept title", noindex=True)
        Settings.objects.update_or_create(pk=1, defaults={"sitemap_enabled": False})

        new_apps = self.migrate(self.migrate_to)

        self.assertEqual(
            new_apps.get_model("seo", "Redirect").objects.get().target, "/current/"
        )
        kept = new_apps.get_model("seo", "Metadata").objects.get()
        self.assertEqual((kept.meta_title, kept.noindex), ("Kept title", True))
        settings_row = new_apps.get_model("seo", "SEOSettings").objects.get(pk=1)
        self.assertFalse(settings_row.sitemap_enabled)
        self.assertTrue(settings_row.auto_schema_enabled)
        self.assertEqual(settings_row.autonomy_mode, "suggest")

        self.migrate(executor_targets())


def executor_targets():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()

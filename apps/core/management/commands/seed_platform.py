"""Fill a freshly migrated local database with complete, realistic platform data."""

from __future__ import annotations

from django.apps import apps as django_apps
from django.core.management import call_command, get_commands
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.seed_data.editorial import ARTICLES
from apps.core.seeding import (
    SeedReport,
    check,
    eager_background_tasks,
    require_local_database,
)

# Catalogue, blog and comment tables whose presence means this is not a fresh database.
EXISTING_DATA_MODELS = (
    "firmwares.Brand",
    "firmwares.Model",
    "firmwares.Chipset",
    "firmwares.FlashingTool",
    "blog.Post",
    "comments.Comment",
)

SUMMARY_MODELS = (
    ("users.CustomUser", "accounts"),
    ("firmwares.Chipset", "chipsets"),
    ("firmwares.Brand", "brands"),
    ("firmwares.Model", "models"),
    ("firmwares.Variant", "variants"),
    ("firmwares.OfficialFirmware", "official firmware listings"),
    ("firmwares.PendingFirmware", "firmware submissions"),
    ("firmwares.FlashingToolCategory", "tool categories"),
    ("firmwares.FlashingTool", "flashing tools"),
    ("firmwares.FlashingGuideTemplate", "guide templates"),
    ("firmwares.GeneratedFlashingGuide", "flashing guides"),
    ("firmwares.AutomatedBlogLink", "automated blog links"),
    ("blog.Category", "blog categories"),
    ("blog.Post", "blog posts"),
    ("tags.Tag", "tags"),
    ("comments.Comment", "comments"),
)


class Command(BaseCommand):
    help = (
        "Seed a freshly migrated LOCAL database: site bootstrap, pages, accounts, "
        "the device catalogue (chipsets, brands, models, reviewed firmware "
        "listings, tools, guides and their automated articles), editorial "
        "articles, comments and SEO rebuilds, then verify the result. "
        "Refuses non-loopback databases and requires --confirm."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required: acknowledge that records are written to this database.",
        )
        parser.add_argument(
            "--allow-existing",
            action="store_true",
            help="Seed even if catalogue, blog or comment data already exists.",
        )
        parser.add_argument(
            "--limit-brands",
            type=int,
            default=None,
            help="Seed only the first N brands (faster, for local checks).",
        )
        parser.add_argument(
            "--skip-seo",
            action="store_true",
            help="Skip the SEO graph, linking and job processing steps.",
        )
        parser.add_argument(
            "--crawl-base-url",
            default="",
            help="Crawl this running site (for example http://127.0.0.1:8000) "
            "after seeding. Without it the crawl is skipped.",
        )

    def handle(self, *args, **options):
        require_local_database(confirm=options["confirm"])
        self._require_fresh_database(allow_existing=options["allow_existing"])
        confirm = {"confirm": True}
        limit = options["limit_brands"]
        started = timezone.now()

        with eager_background_tasks():
            call_command("bootstrap_site", stdout=self.stdout)
            self._seed_pages()
            call_command("seed_people", stdout=self.stdout, **confirm)
            self._disable_ads()
            call_command(
                "seed_catalog",
                stdout=self.stdout,
                limit_brands=limit,
                skip_verify=True,
                **confirm,
            )
            call_command("seed_editorial", stdout=self.stdout, **confirm)
            call_command("seed_comments", stdout=self.stdout, **confirm)
            if not options["skip_seo"]:
                self._seo(options["crawl_base_url"])

        self._verify(limit)
        self._summary(started)

    def _require_fresh_database(self, *, allow_existing: bool) -> None:
        if allow_existing:
            return
        present = [
            label
            for label in EXISTING_DATA_MODELS
            if django_apps.get_model(label).objects.exists()
        ]
        if present:
            raise CommandError(
                "The database already contains data ("
                + ", ".join(present)
                + "). Seeding targets a freshly migrated database; pass "
                "--allow-existing to add the seed data alongside it."
            )

    def _seed_pages(self) -> None:
        if "seed_pages" not in get_commands():
            self.stdout.write("== Pages: seed_pages is not available; skipped.")
            return
        self.stdout.write("== Pages")
        call_command("seed_pages", stdout=self.stdout)

    def _disable_ads(self) -> None:
        if not django_apps.is_installed("apps.ads"):
            return
        from apps.ads.seeding import ensure_ads_disabled

        self.stdout.write("== Ads (no campaigns or networks are created)")
        for line in ensure_ads_disabled().lines():
            self.stdout.write(line)

    def _seo(self, crawl_base_url: str) -> None:
        if not django_apps.is_installed("apps.seo"):
            self.stdout.write("== SEO: apps.seo is not installed; skipped.")
            return
        self.stdout.write("== SEO (derived from the seeded records)")
        call_command("rebuild_linkable", stdout=self.stdout)
        call_command("seo_rebuild_graph", stdout=self.stdout)
        call_command("seo_process_jobs", stdout=self.stdout, limit=500)
        if crawl_base_url:
            call_command("seo_crawl", stdout=self.stdout, base_url=crawl_base_url)
        else:
            self.stdout.write(
                "SEO crawl skipped: it needs a running site. Start the server and "
                "run `manage.py seo_crawl --base-url http://127.0.0.1:8000`; no "
                "crawl results are fabricated."
            )

    def _verify(self, limit: int | None) -> None:
        from apps.blog.models import Post, PostStatus
        from apps.comments.models import Comment
        from apps.firmwares.seed_verification import MINIMUM_BRANDS, verify_catalog
        from apps.firmwares.seeding import select_brands

        brands = select_brands(limit)
        self.stdout.write("== Verification")
        result = verify_catalog(
            brands,
            today=timezone.localdate(),
            minimum_brands=min(MINIMUM_BRANDS, len(brands)),
        )
        for line in result.lines():
            self.stdout.write(line)

        slugs = [article.slug for article in ARTICLES]
        live = Post.objects.live().filter(slug__in=slugs, is_ai_generated=False)
        check(live.count() == len(slugs), "An editorial article is not public.")
        check(
            not Post.objects.filter(is_ai_generated=False)
            .exclude(status=PostStatus.PUBLISHED)
            .exists(),
            "A seeded guide or article is still unpublished.",
        )
        statuses = set(Comment.objects.values_list("status", flat=True))
        check(
            Comment.Status.APPROVED in statuses and Comment.Status.PENDING in statuses,
            "Comments were not split between approved and queued by moderation.",
        )
        self.stdout.write(f"  editorial articles public: {live.count()}")
        for status in sorted(statuses):
            total = Comment.objects.filter(status=status).count()
            self.stdout.write(f"  comments {status}: {total}")

    def _summary(self, started) -> None:
        report = SeedReport()
        for label, name in SUMMARY_MODELS:
            count = django_apps.get_model(label).objects.count()
            report.created[name] = count
        elapsed = (timezone.now() - started).total_seconds()
        self.stdout.write("== Summary (records in the database)")
        for name, count in report.created.items():
            self.stdout.write(f"  {name}: {count}")
        self.stdout.write(
            self.style.SUCCESS(f"Platform seed complete in {elapsed:.0f}s.")
        )

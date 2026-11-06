"""Contract tests for the platform seed commands and their data."""

from __future__ import annotations

import re
from datetime import date
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Count

from apps.ads.models import AdsSettings
from apps.blog.models import Post, PostStatus
from apps.comments.models import Comment
from apps.core.seed_data.comments import COMMENTS
from apps.core.seed_data.editorial import ARTICLES, CATEGORIES
from apps.core.seed_data.people import ALL_PEOPLE
from apps.core.seeding import require_local_database
from apps.firmwares.models import (
    AutomatedBlogLink,
    Brand,
    Chipset,
    FirmwareSourceType,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
    Model,
    OfficialFirmware,
    PendingFirmware,
)
from apps.firmwares.seed_data import (
    BRANDS,
    CHIPSETS,
    CHIPSETS_BY_KEY,
    GUIDES,
    TOOLS,
)
from apps.firmwares.seeding import (
    catalogue_name,
    firmware_plans,
    model_slug,
    variant_seeds,
)
from apps.users.models import CustomUser

TODAY = date(2026, 11, 5)
FORBIDDEN_WORDS = re.compile(
    r"\b(lorem|ipsum|demo|sample|placeholder|dummy|fake|test)\b", re.IGNORECASE
)


def _run(command: str, **options: object) -> str:
    out = StringIO()
    call_command(command, stdout=out, stderr=StringIO(), **options)
    return out.getvalue()


class TestSeedData:
    def test_catalogue_meets_the_size_minimums(self):
        assert len(BRANDS) >= 20
        assert all(len(brand.models) >= 5 for brand in BRANDS)

    def test_identifiers_are_unique_within_their_scope(self):
        assert len({brand.slug for brand in BRANDS}) == len(BRANDS)
        assert len({brand.name for brand in BRANDS}) == len(BRANDS)
        for brand in BRANDS:
            slugs = [model_slug(catalogue_name(brand, seed)) for seed in brand.models]
            assert len(slugs) == len(set(slugs)), brand.name
            codes = [seed.model_code for seed in brand.models if seed.model_code]
            assert len(codes) == len(set(codes)), brand.name
            for seed in brand.models:
                variants = [slug.name for slug in variant_seeds(seed)]
                assert len(variants) == len(set(variants))

    def test_every_chipset_reference_resolves(self):
        used = {
            key
            for brand in BRANDS
            for seed in brand.models
            for key in (seed.chipset, *(v.chipset for v in seed.variants))
            if key
        }
        assert used <= set(CHIPSETS_BY_KEY)
        assert len(CHIPSETS_BY_KEY) == len(CHIPSETS)
        assert used == set(CHIPSETS_BY_KEY), "unused chipsets are seeded"
        for tool in TOOLS:
            assert set(tool.chipsets) <= set(CHIPSETS_BY_KEY)

    def test_guides_reference_known_brands_and_tools(self):
        brands = {brand.slug: brand for brand in BRANDS}
        tools = {tool.slug for tool in TOOLS}
        for guide in GUIDES:
            assert guide.brand in brands
            assert set(guide.tools) <= tools
            if guide.model_name:
                names = {
                    model_slug(catalogue_name(brands[guide.brand], seed))
                    for seed in brands[guide.brand].models
                }
                assert model_slug(guide.model_name) in names

    def test_firmware_plans_are_complete_and_in_the_past(self):
        urls: set[str] = set()
        for brand in BRANDS:
            for seed in brand.models:
                plans = firmware_plans(brand, seed)
                assert len(plans) == 2
                assert plans[0].build_number != plans[1].build_number
                for plan in plans:
                    assert plan.build_date <= TODAY
                    assert plan.security_patch <= TODAY
                    assert plan.url.startswith("https://downloads.example.invalid/")
                    assert plan.url not in urls
                    urls.add(plan.url)
                    assert plan.file_name.endswith(brand.scheme.extensions[0]) or (
                        plan.file_name.endswith(brand.scheme.extensions[1])
                    )

    def test_release_dates_are_not_in_the_future(self):
        assert all(seed.released <= TODAY for brand in BRANDS for seed in brand.models)

    def test_user_visible_text_has_no_placeholder_wording(self):
        texts = [brand.description for brand in BRANDS]
        texts += [chip.description for chip in CHIPSETS]
        texts += [tool.description + tool.instructions for tool in TOOLS]
        texts += [guide.intro + guide.title for guide in GUIDES]
        texts += [
            article.title + article.summary + article.body for article in ARTICLES
        ]
        texts += [comment.body for comment in COMMENTS]
        texts += [person.full_name + person.bio for person in ALL_PEOPLE]
        offenders = [text[:60] for text in texts if FORBIDDEN_WORDS.search(text)]
        assert not offenders

    def test_editorial_minimum_and_categories(self):
        assert len(ARTICLES) >= 12
        categories = {category.slug for category in CATEGORIES}
        assert {article.category for article in ARTICLES} <= categories
        assert len({article.slug for article in ARTICLES}) == len(ARTICLES)

    def test_comment_replies_follow_their_parents(self):
        seen: set[str] = set()
        for comment in COMMENTS:
            assert not comment.reply_to or comment.reply_to in seen
            seen.add(comment.key)
        assert any(
            len(re.findall(r"https?://", comment.body)) > 2 for comment in COMMENTS
        )


class TestSeedGuards:
    def test_confirmation_is_required(self):
        with pytest.raises(CommandError, match="--confirm"):
            require_local_database(confirm=False)

    def test_non_loopback_postgres_is_refused(self):
        remote = type(
            "Connection",
            (),
            {"vendor": "postgresql", "settings_dict": {"HOST": "db.example.invalid"}},
        )()
        with (
            patch("apps.core.seeding.connection", remote),
            pytest.raises(CommandError, match="non-local database"),
        ):
            require_local_database(confirm=True)

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_postgres_is_accepted(self, host):
        local = type(
            "Connection",
            (),
            {"vendor": "postgresql", "settings_dict": {"HOST": host}},
        )()
        with patch("apps.core.seeding.connection", local):
            require_local_database(confirm=True)

    @pytest.mark.django_db
    def test_every_command_requires_confirmation(self):
        for command in (
            "seed_platform",
            "seed_people",
            "seed_catalog",
            "seed_editorial",
            "seed_comments",
        ):
            with pytest.raises(CommandError, match="--confirm"):
                _run(command)

    @pytest.mark.django_db
    def test_prebuilt_tool_categories_exist_after_migrations(self):
        assert set(FlashingToolCategory.objects.values_list("slug", flat=True)) == {
            "android-platform-tools",
            "oem-recovery-utilities",
        }

    @pytest.mark.django_db
    def test_a_populated_database_needs_allow_existing(self):
        Brand.objects.create(name="Existing", slug="existing")
        with pytest.raises(CommandError, match="allow-existing"):
            _run("seed_platform", confirm=True)
        assert Model.objects.count() == 0


@pytest.fixture
def seeded_slice(transactional_db):
    """Two brands seeded end to end (the on-commit automation must run)."""
    output = _run("seed_platform", confirm=True, limit_brands=2)
    return output


class TestSeedPlatformSlice:
    def test_catalogue_review_and_automation(self, seeded_slice):
        assert Brand.objects.count() == 2
        assert Model.objects.count() == sum(len(b.models) for b in BRANDS[:2])
        for model in Model.objects.all():
            files = OfficialFirmware.objects.filter(model=model)
            assert files.count() >= 2
            for firmware in files:
                assert firmware.reviewed_by is not None
                assert firmware.uploader is not None
                assert (
                    firmware.source_type == FirmwareSourceType.EXTERNAL
                    and firmware.is_verified
                    and firmware.reviewed_by.username == "mobofirm-reviewer"
                    and firmware.uploader.username == "mobofirm-uploader"
                )
        assert not PendingFirmware.objects.exclude(admin_decision="approved").exists()
        assert "Platform seed complete" in seeded_slice

    def test_one_automated_post_per_target_with_grouped_firmware(self, seeded_slice):
        used = {
            key
            for brand in BRANDS[:2]
            for seed in brand.models
            for key in (seed.chipset, *(v.chipset for v in seed.variants))
            if key
        }
        assert Chipset.objects.count() == len(used) < len(CHIPSETS)
        for kind, expected in (
            ("brand", Brand.objects.count()),
            ("model", Model.objects.count()),
            ("chipset", Chipset.objects.count()),
        ):
            links = AutomatedBlogLink.objects.filter(kind=kind)
            assert links.count() == expected, kind
        duplicated = (
            Post.objects.filter(is_ai_generated=True, firmware_model__isnull=False)
            .values("firmware_model")
            .annotate(total=Count("pk"))
            .filter(total__gt=1)
        )
        assert not duplicated.exists()
        for model in Model.objects.all():
            post = AutomatedBlogLink.objects.get(kind="model", model=model).post
            assert post.is_live
            assert "Official firmware (2)" in post.body
            assert "Download (external host)" in post.body
            assert post.tags.filter(name=model.brand.name).exists()

    def test_editorial_posts_are_published_by_the_workflow(self, seeded_slice):
        published = Post.objects.filter(
            slug__in=[a.slug for a in ARTICLES], status=PostStatus.PUBLISHED
        )
        assert published.count() == len(ARTICLES) >= 12
        for post in published:
            assert post.author.username == "mobofirm-editorial"
            assert post.is_live
            assert post.tags.exists()
            assert post.revisions.exists()

    def test_comments_are_moderated_by_the_service(self, seeded_slice):
        statuses = dict(
            Comment.objects.values_list("status")
            .annotate(total=Count("pk"))
            .values_list("status", "total")
        )
        assert statuses[Comment.Status.APPROVED] > statuses[Comment.Status.PENDING] > 0
        assert not Comment.objects.filter(
            status=Comment.Status.APPROVED, body__icontains="http"
        ).exists()
        assert (
            not Comment.objects.filter(user__is_staff=True)
            .exclude(user__username="mobofirm-editorial")
            .exists()
        )

    def test_guides_tools_and_ads(self, seeded_slice):
        assert FlashingTool.objects.count() == len(TOOLS)
        assert FlashingGuideTemplate.objects.count() >= 1
        guides = GeneratedFlashingGuide.objects.select_related("post")
        assert guides.exists()
        assert all(guide.post.status == PostStatus.PUBLISHED for guide in guides)
        assert not any(
            getattr(AdsSettings.get_solo(), name)
            for name in ("ads_enabled", "auto_ads_enabled", "rewarded_ads_enabled")
        )

    def test_accounts_cannot_sign_in(self, seeded_slice):
        users = CustomUser.objects.filter(username__in=[p.username for p in ALL_PEOPLE])
        assert users.count() == len(ALL_PEOPLE)
        assert not any(user.has_usable_password() for user in users)
        assert not users.filter(is_superuser=True).exists()

    def test_rerun_is_idempotent(self, seeded_slice):
        def counts() -> tuple[int, ...]:
            return (
                Brand.objects.count(),
                Model.objects.count(),
                OfficialFirmware.objects.count(),
                PendingFirmware.objects.count(),
                Post.objects.count(),
                Comment.objects.count(),
                AutomatedBlogLink.objects.count(),
                GeneratedFlashingGuide.objects.count(),
                CustomUser.objects.count(),
            )

        before = counts()
        _run("seed_platform", confirm=True, limit_brands=2, allow_existing=True)
        assert counts() == before


class TestSeedPlatformFullScale:
    def test_full_catalogue_meets_the_acceptance_criteria(self, transactional_db):
        output = _run("seed_platform", confirm=True, skip_seo=False)
        assert "Verification" in output
        assert Brand.objects.count() >= 20
        for brand in Brand.objects.all():
            assert brand.models.count() >= 5
        assert OfficialFirmware.objects.count() >= Model.objects.count() * 2
        assert AutomatedBlogLink.objects.filter(kind="model").count() == (
            Model.objects.count()
        )
        assert AutomatedBlogLink.objects.filter(kind="brand").count() == (
            Brand.objects.count()
        )
        assert AutomatedBlogLink.objects.filter(kind="chipset").count() == (
            Chipset.objects.count()
        )

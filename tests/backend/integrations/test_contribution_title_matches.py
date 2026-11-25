"""Contribution title suggestions include only public blog and catalogue records."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogSettings, Category, GuideRequest, Post, PostStatus
from apps.firmwares.chipsets import create_chipset_record
from apps.firmwares.models import (
    Brand,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
)

pytestmark = pytest.mark.django_db


def test_title_match_endpoint_finds_public_content_without_exposing_pending_records(
    client,
):
    now = timezone.now()
    user = get_user_model().objects.create_user(
        username="title-match-contributor",
        email="title-match-contributor@example.test",
        email_verified_at=now,
    )
    settings = BlogSettings.get_solo()
    settings.enable_blog = True
    settings.allow_user_blog_posts = True
    settings.save()

    brand = Brand.objects.create(name="Pixel", slug="pixel")
    chipset = create_chipset_record(
        vendor="Google", name="Tensor G4", slug="google-tensor-g4"
    )
    model = Model.objects.create(
        brand=brand,
        name="Pixel 9",
        slug="pixel-9",
        chipset_ref=chipset,
    )
    category = FlashingToolCategory.objects.create(
        name="Recovery utilities", slug="recovery-utilities"
    )
    FlashingTool.objects.create(
        category=category,
        name="Odin Recovery Utility",
        slug="odin-recovery-utility",
    )
    firmware = OfficialFirmware.objects.create(
        original_file_name="pixel-9-factory-image.zip",
        stored_file_path="/firmware/pixel-9-factory-image.zip",
        brand=brand,
        model=model,
        is_verified=True,
    )

    guide_category = Category.objects.create(name="Guides", slug="guides")
    published = Post.objects.create(
        title="Pixel 9 recovery guide",
        body="<p>Public guide content.</p>",
        author=user,
        category=guide_category,
        status=PostStatus.PUBLISHED,
        publish_at=now,
    )
    private_post = Post.objects.create(
        title="Pixel 9 secret review guide",
        body="<p>Pending content.</p>",
        author=user,
        category=guide_category,
        status=PostStatus.IN_REVIEW,
    )
    public_request = GuideRequest.objects.create(
        requester=user,
        title="Pixel 9 recovery guide request",
        status=GuideRequest.Status.APPROVED,
    )
    private_request = GuideRequest.objects.create(
        requester=user,
        title="Pixel 9 private request",
        status=GuideRequest.Status.PENDING,
    )
    client.force_login(user)

    expected = {
        "Pixel": reverse("firmwares:brand_detail", args=[brand.slug]),
        "Pixel 9": reverse("firmwares:model_detail", args=[brand.slug, model.slug]),
        "Tensor G4": reverse("firmwares:chipset_detail", args=[chipset.slug]),
        "Odin Recovery Utility": reverse(
            "firmwares:flashing_tool_detail", args=[category.tools.get().slug]
        ),
        firmware.original_file_name: reverse(
            "firmwares:firmware_detail", args=["official", firmware.pk]
        ),
        "Pixel 9 recovery guide": published.get_absolute_url(),
    }
    for title, target_url in expected.items():
        response = client.get(
            reverse("blog:contribute_title_matches"), {"title": title}
        )

        assert response.status_code == 200
        assert target_url in response.content.decode()

    response = client.get(
        reverse("blog:contribute_title_matches"),
        {"title": "Pixel 9 recovery guide"},
    )
    html = response.content.decode()
    assert public_request.get_absolute_url() in html
    assert private_post.get_absolute_url() not in html
    assert private_request.get_absolute_url() not in html


def test_title_match_endpoint_keeps_short_queries_and_rate_limits_explicit(
    client, monkeypatch: pytest.MonkeyPatch
):
    now = timezone.now()
    user = get_user_model().objects.create_user(
        username="title-match-limits",
        email="title-match-limits@example.test",
        email_verified_at=now,
    )
    settings = BlogSettings.get_solo()
    settings.enable_blog = True
    settings.allow_user_blog_posts = True
    settings.save()
    client.force_login(user)
    url = reverse("blog:contribute_title_matches")

    short = client.get(url, {"title": "ab"})

    assert short.status_code == 200
    assert short.content == b""

    monkeypatch.setattr(
        "apps.blog.views_contribute.allow_action", lambda *a, **k: False
    )
    limited = client.get(url, {"title": "Galaxy S23 guide"})

    assert limited.status_code == 429
    assert b"wait a moment" in limited.content

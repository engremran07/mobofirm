from __future__ import annotations

from django.urls import reverse

from apps.distribution import videos
from apps.distribution.models import DistributionSettings, MediaAsset, VideoJob
from apps.firmwares.models import (
    Chipset,
    ChipsetVendor,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
    Variant,
)


def library_url() -> str:
    return reverse("admin:distribution_channel_media_library")


def test_library_lists_current_public_content_types(
    admin_client, make_firmware, make_post
):
    firmware = make_firmware()
    make_firmware("pending.zip", verified=False)
    chipset_vendor = ChipsetVendor.objects.create(
        name="Northstar Silicon", slug="northstar-silicon"
    )
    chipset = Chipset.objects.create(
        vendor=chipset_vendor, name="N1", slug="northstar-n1"
    )
    firmware.model.chipset_ref = chipset
    firmware.model.save(update_fields=["chipset_ref"])
    variant_chipset = Chipset.objects.create(
        vendor=chipset_vendor, name="N2", slug="northstar-n2"
    )
    variant_firmware = make_firmware("variant.zip", model="Atlas Variant")
    variant = Variant.objects.create(
        model=variant_firmware.model,
        name="Global",
        slug="global",
        chipset_ref=variant_chipset,
    )
    variant_firmware.variant = variant
    variant_firmware.save(update_fields=["variant"])

    post = make_post()
    guide = make_post("A public flashing guide", body="<ol><li>Flash safely</li></ol>")
    template = FlashingGuideTemplate.objects.create(
        title_template="{brand} guide", body_template="A guide for {brand}"
    )
    GeneratedFlashingGuide.objects.create(
        template=template, post=guide, brand=firmware.brand
    )
    category = FlashingToolCategory.objects.create(name="Utilities", slug="utilities")
    tool = FlashingTool.objects.create(
        category=category, name="Northstar Flash", slug="northstar-flash"
    )

    firmware_response = admin_client.get(library_url(), {"kind": "firmware"})
    assert firmware_response.status_code == 200
    firmware_rows = firmware_response.context["entries"]
    assert {row["entry"].ref for row in firmware_rows} == {
        f"official:{firmware.pk}",
        f"official:{variant_firmware.pk}",
    }
    assert firmware_rows[0]["status"].card == "Not generated"
    assert firmware_rows[0]["status"].motion_video == "Not created"

    blog_response = admin_client.get(library_url(), {"kind": "blog"})
    assert [row["entry"].ref for row in blog_response.context["entries"]] == [
        f"post:{post.pk}"
    ]
    guide_response = admin_client.get(library_url(), {"kind": "guide"})
    assert [row["entry"].ref for row in guide_response.context["entries"]] == [
        f"post:{guide.pk}"
    ]
    assert guide_response.context["entries"][0]["entry"].label == "Flashing guide"

    tool_response = admin_client.get(library_url(), {"kind": "tool"})
    tool_row = tool_response.context["entries"][0]
    assert tool_row["entry"].ref == f"tool:{tool.slug}"
    assert tool_row["status"].motion_video == "No video template"
    card_action = reverse("admin:distribution_channel_media_library_bulk")
    card_data = {
        "action": "cards",
        "kind": "tool",
        "selected": [f"tool|tool:{tool.slug}"],
    }
    admin_client.post(card_action, card_data)
    assert (
        MediaAsset.objects.filter(
            content_kind="tool", object_ref=f"tool:{tool.slug}"
        ).count()
        == 2
    )
    admin_client.post(card_action, card_data)
    assert (
        MediaAsset.objects.filter(
            content_kind="tool", object_ref=f"tool:{tool.slug}"
        ).count()
        == 2
    )
    ready_tool = admin_client.get(library_url(), {"kind": "tool"}).context["entries"][0]
    assert ready_tool["status"].card == "Ready"
    assert ready_tool["status"].vertical == "Ready"
    assert ready_tool["status"].motion_video == "No video template"

    brand_response = admin_client.get(library_url(), {"kind": "brand"})
    brand_row = brand_response.context["entries"][0]
    assert brand_row["entry"].ref == f"brand:{firmware.brand.slug}"
    assert brand_row["entry"].url == reverse(
        "firmwares:brand_detail", kwargs={"slug": firmware.brand.slug}
    )
    chipset_response = admin_client.get(library_url(), {"kind": "chipset"})
    chipset_refs = {row["entry"].ref for row in chipset_response.context["entries"]}
    assert chipset_refs == {
        f"chipset:{chipset.slug}",
        f"chipset:{variant_chipset.slug}",
    }


def test_historical_video_backfill_is_capped_and_idempotent(admin_client, make_post):
    DistributionSettings.objects.update_or_create(pk=1, defaults={"video_daily_cap": 1})
    first = make_post("A public historical post")
    second = make_post("Another public historical post")
    url = reverse("admin:distribution_channel_media_library_bulk")
    form = {"action": "videos", "kind": "blog", "page": "1"}

    response = admin_client.post(url, {**form, "selected": [f"blog|post:{first.pk}"]})
    assert response.status_code == 302
    job = VideoJob.objects.get(content_kind="blog", object_ref=f"post:{first.pk}")
    assert job.status == VideoJob.Status.DRAFT and job.params["auto"] is True

    admin_client.post(url, {**form, "selected": [f"blog|post:{first.pk}"]})
    assert VideoJob.objects.count() == 1
    admin_client.post(url, {**form, "selected": [f"blog|post:{second.pk}"]})
    assert VideoJob.objects.count() == 1
    assert videos.auto_budget() == 0


def test_library_reports_missing_generated_files_as_missing(admin_client, make_post):
    post = make_post()
    ref = f"post:{post.pk}"
    MediaAsset.objects.create(
        content_kind="blog",
        object_ref=ref,
        asset_kind=MediaAsset.AssetKind.CARD,
        storage_name="distribution/missing/card.png",
    )
    VideoJob.objects.create(
        content_kind="blog",
        object_ref=ref,
        title=post.title,
        template="blog-post",
        idempotency_key=f"missing-file:{post.pk}",
        status=VideoJob.Status.READY,
        video_name="distribution/missing/video.mp4",
    )
    response = admin_client.get(library_url(), {"kind": "blog"})
    status = response.context["entries"][0]["status"]
    assert status.card == "File missing"
    assert status.motion_video == "File missing"


def test_media_library_rejects_oversized_batch_and_requires_action_permissions(
    admin_client, staff_client
):
    url = reverse("admin:distribution_channel_media_library_bulk")
    too_many = [f"blog|post:{number}" for number in range(1, 12)]
    response = admin_client.post(
        url, {"action": "videos", "kind": "blog", "selected": too_many}
    )
    assert response.status_code == 302
    assert not VideoJob.objects.exists()

    viewer = staff_client("view_videojob")
    assert viewer.get(library_url()).status_code == 200
    assert (
        viewer.post(
            url,
            {
                "action": "cards",
                "kind": "tool",
                "selected": ["tool|tool:northstar-flash"],
            },
        ).status_code
        == 403
    )
    assert (
        viewer.post(
            url,
            {
                "action": "videos",
                "kind": "blog",
                "selected": ["blog|post:1"],
            },
        ).status_code
        == 403
    )
    assert not MediaAsset.objects.exists()

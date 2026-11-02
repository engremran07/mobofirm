"""
Flashing guide auto-generation service.

Generates blog posts from FlashingGuideTemplate + Brand/Model combinations.
Uses templates with placeholder substitution and optional AI enrichment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from .models import (
    Brand,
    FlashingGuideTemplate,
    FlashingTool,
    GeneratedFlashingGuide,
    Model,
)
from .tool_matcher import filter_tools_for_brand, get_compatible_tools_from_db

if TYPE_CHECKING:
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)


def _get_recommended_tools(
    template: FlashingGuideTemplate,
    brand: Brand,
    chipset: str = "",
) -> list[FlashingTool]:
    """Get tools relevant to a brand/chipset, with chipset+brand validation.

    1. Takes template's M2M tools and filters out incompatible ones
    2. Falls back to DB query filtered by chipset family + brand
    """
    brand_name = brand.name if brand else ""

    # Try template-assigned tools first, but validate them
    raw_tools = list(template.recommended_tools.filter(is_active=True))  # type: ignore[attr-defined]
    if raw_tools:
        tools = filter_tools_for_brand(raw_tools, brand_name, chipset)
        if tools:
            return tools

    # Fallback: query DB with smart chipset+brand filtering
    return get_compatible_tools_from_db(brand_name, chipset)


def _format_tool_list(tools: list[FlashingTool]) -> str:
    """Format tools as Markdown bullet list."""
    if not tools:
        return "- Standard flashing tools for your device"
    lines: list[str] = []
    for tool in tools:
        risk = (
            f" ⚠️ {tool.get_risk_level_display()}"  # type: ignore[attr-defined]
            if tool.risk_level != "safe"
            else ""
        )
        platform = f" ({tool.get_platform_display()})"  # type: ignore[attr-defined]
        lines.append(f"- **{tool.name}**{platform}{risk}")
        if tool.description:
            lines.append(f"  {tool.description[:150]}")
    return "\n".join(lines)


def _build_standard_steps(
    guide_type: str,
    brand_name: str,
    model_name: str = "",
) -> str:
    """Build standard step-by-step instructions based on guide type."""
    device = f"{brand_name} {model_name}".strip()

    step_templates: dict[str, list[str]] = {
        "stock_flash": [
            f"1. **Download** the correct stock firmware for your {device}",
            "2. **Backup** all important data (contacts, photos, apps)",
            "3. **Charge** your device to at least 50% battery",
            "4. **Enable** USB Debugging in Developer Options",
            "5. **Install** the required flashing tool on your PC",
            "6. **Connect** your device via USB cable",
            "7. **Load** the firmware file in the flashing tool",
            "8. **Start** the flashing process and wait for completion",
            "9. **Reboot** your device — first boot may take several minutes",
            "10. **Verify** the firmware version in Settings > About Phone",
        ],
        "custom_rom": [
            "1. **Unlock** the bootloader (see our bootloader unlock guide)",
            "2. **Install** a custom recovery (TWRP recommended)",
            "3. **Download** the custom ROM and GApps package",
            "4. **Backup** your current ROM in recovery",
            "5. **Wipe** data, cache, and dalvik cache in recovery",
            "6. **Flash** the custom ROM zip file",
            "7. **Flash** the GApps package (if needed)",
            "8. **Reboot** and wait for first boot setup",
        ],
        "unlock_bootloader": [
            "1. **Enable** Developer Options (tap Build Number 7 times)",
            "2. **Enable** OEM Unlocking in Developer Options",
            "3. **Enable** USB Debugging",
            "4. **Connect** device to PC and open terminal/command prompt",
            "5. **Boot** into fastboot mode: `adb reboot bootloader`",
            "6. **Run** `fastboot oem unlock` or `fastboot flashing unlock`",
            "7. **Confirm** the unlock on your device screen",
            "8. **Reboot** — device will factory reset automatically",
        ],
        "root": [
            "1. **Unlock** the bootloader first (required for most methods)",
            "2. **Download** Magisk APK (latest stable version)",
            "3. **Extract** boot.img from your current firmware",
            "4. **Patch** boot.img using Magisk app on your phone",
            "5. **Flash** the patched boot.img via fastboot",
            "6. **Reboot** and verify root with a root checker app",
        ],
        "recovery": [
            "1. **Unlock** the bootloader (if locked)",
            "2. **Download** TWRP or other custom recovery for your device",
            "3. **Boot** into fastboot mode",
            "4. **Flash** recovery: `fastboot flash recovery recovery.img`",
            "5. **Boot** into recovery to verify installation",
        ],
        "frp_bypass": [
            "1. **Connect** to Wi-Fi during setup",
            "2. **Navigate** to accessibility settings via the setup wizard",
            "3. **Open** a browser or settings app through accessibility shortcuts",
            "4. **Download** and install an FRP bypass tool",
            "5. **Follow** the tool instructions to remove the Google account lock",
            "6. **Restart** the device and complete setup normally",
        ],
        "downgrade": [
            "1. **Find** the exact older firmware version you need",
            "2. **Backup** all data — downgrading will factory reset",
            "3. **Disable** security features (Find My Device, etc.)",
            "4. **Flash** the older firmware using the appropriate tool",
            "5. **Do NOT** update automatically — disable auto-updates",
        ],
        "repair": [
            "1. **Do NOT panic** — most bricks are recoverable",
            "2. **Try** force restart (hold Power + Volume Down for 15s)",
            "3. **Try** booting into recovery or download mode",
            "4. **Connect** to PC and use emergency flashing tools",
            "5. **Flash** a known-good firmware for your exact model",
            "6. **If** hardware brick: consider professional service",
        ],
    }

    return "\n".join(step_templates.get(guide_type, step_templates["stock_flash"]))


def _render_template(
    template_str: str,
    brand_name: str,
    model_name: str = "",
    chipset: str = "",
    guide_type_display: str = "",
    tools_text: str = "",
    steps_text: str = "",
) -> str:
    """Render a template string with placeholders."""
    return (
        template_str.replace("{brand}", brand_name)
        .replace("{model}", model_name or "devices")
        .replace("{chipset}", chipset or "")
        .replace("{guide_type}", guide_type_display)
        .replace("{tools}", tools_text)
        .replace("{steps}", steps_text)
    )


def _try_ai_enrich(
    title: str,
    body: str,
    brand_name: str,
    guide_type: str,
) -> tuple[str, str]:
    """Optionally enrich guide content with AI. Returns (title, body)."""
    try:
        from apps.ai.services import test_completion

        prompt = f"""You are a firmware expert. Improve this flashing guide article.
Make it more engaging, add technical tips, warnings, and troubleshooting advice.
Keep the same structure but enrich the content.

Title: {title}
Brand: {brand_name}
Guide Type: {guide_type}

Current Body:
{body[:3000]}

Return the improved body text only (Markdown format). Keep it under 2000 words."""

        resp = test_completion(prompt)
        enriched = resp.get("text", "")
        if enriched and len(enriched) > 200:
            return title, enriched
    except Exception as e:
        logger.debug("AI enrichment skipped: %s", e)
    return title, body


@transaction.atomic
def generate_guide_for_brand_model(
    template: FlashingGuideTemplate,
    brand: Brand,
    model_obj: Model | None = None,
    *,
    use_ai: bool = False,
) -> GeneratedFlashingGuide | None:
    """
    Generate a single blog post from a guide template for a brand/model.
    Returns the GeneratedFlashingGuide record, or None if already exists.
    """
    from apps.blog.models import Post

    # Check if already generated
    existing = GeneratedFlashingGuide.objects.filter(
        template=template,
        brand=brand,
        model=model_obj,
    ).first()
    if existing:
        return None

    brand_name = brand.name
    model_name = model_obj.name if model_obj else ""
    chipset = (model_obj.chipset if model_obj else template.chipset_filter) or ""
    guide_type_display = template.get_guide_type_display()  # type: ignore[attr-defined]

    # Build content
    tools = _get_recommended_tools(template, brand, chipset)
    tools_text = _format_tool_list(tools)
    steps_text = _build_standard_steps(template.guide_type, brand_name, model_name)

    title = _render_template(
        template.title_template,
        brand_name,
        model_name,
        chipset,
        guide_type_display,
    )
    body = _render_template(
        template.body_template,
        brand_name,
        model_name,
        chipset,
        guide_type_display,
        tools_text,
        steps_text,
    )
    summary = _render_template(
        template.summary_template
        or f"Complete {guide_type_display} guide for {{brand}} {{model}}",
        brand_name,
        model_name,
        chipset,
        guide_type_display,
    )

    # AI enrichment
    if use_ai:
        title, body = _try_ai_enrich(title, body, brand_name, template.guide_type)

    # Create slug
    slug_base = slugify(f"{brand_name}-{model_name}-{template.guide_type}".strip("-"))
    slug = slug_base[:200]
    counter = 1
    while Post.objects.filter(slug=slug).exists():
        slug = f"{slug_base[:190]}-{counter}"
        counter += 1

    # Determine status
    from apps.blog.models import PostStatus

    status = PostStatus.PUBLISHED if template.auto_publish else PostStatus.DRAFT

    # Create blog post
    post = Post.objects.create(
        title=title[:300],
        slug=slug,
        summary=summary[:500],
        body=body,
        status=status,
        firmware_brand=brand,
        firmware_model=model_obj,
        is_ai_generated=use_ai,
        publish_at=timezone.now() if status == PostStatus.PUBLISHED else None,
        published_at=timezone.now() if status == PostStatus.PUBLISHED else None,
    )

    # Create tracking record
    guide = GeneratedFlashingGuide.objects.create(
        template=template,
        post=post,
        brand=brand,
        model=model_obj,
        chipset=chipset,
    )

    # Update template counter
    FlashingGuideTemplate.objects.filter(pk=template.pk).update(
        generated_count=template.generated_count + 1
    )

    logger.info(
        "flashing_guide.generated",
        extra={
            "template": template.pk,
            "brand": brand_name,
            "model": model_name,
            "post": post.pk,
        },
    )
    return guide


def generate_guides_for_template(
    template: FlashingGuideTemplate,
    *,
    use_ai: bool = False,
    limit: int = 50,
) -> list[GeneratedFlashingGuide]:
    """
    Generate blog posts for all matching brand/model combinations.
    Uses template.brand filter and chipset_filter to find targets.
    """
    results: list[GeneratedFlashingGuide] = []

    # Get target brands
    if template.brand_id:  # type: ignore[attr-defined]
        brands: QuerySet[Brand] = Brand.objects.filter(pk=template.brand_id)  # type: ignore[attr-defined]
    else:
        brands = Brand.objects.filter(is_featured=True)

    for brand in brands:
        # Get models for this brand
        models_qs: QuerySet[Model] = Model.objects.filter(brand=brand, is_active=True)
        if template.chipset_filter:
            models_qs = models_qs.filter(chipset__icontains=template.chipset_filter)

        if models_qs.exists():
            for device_model in models_qs[:limit]:
                guide = generate_guide_for_brand_model(
                    template, brand, device_model, use_ai=use_ai
                )
                if guide:
                    results.append(guide)
                    if len(results) >= limit:
                        return results
        else:
            # Generate brand-level guide only
            guide = generate_guide_for_brand_model(template, brand, None, use_ai=use_ai)
            if guide:
                results.append(guide)
                if len(results) >= limit:
                    return results

    return results


def get_tool_stats() -> dict[str, Any]:
    """Return aggregate statistics for the flashing tools catalog."""
    from .models import FlashingToolCategory

    return {
        "total_tools": FlashingTool.objects.filter(is_active=True).count(),
        "total_categories": FlashingToolCategory.objects.filter(is_active=True).count(),
        "oem_tools": FlashingTool.objects.filter(
            is_active=True, tool_type="oem"
        ).count(),
        "free_tools": FlashingTool.objects.filter(
            is_active=True, tool_type="free"
        ).count(),
        "open_source_tools": FlashingTool.objects.filter(
            is_active=True, tool_type="open_source"
        ).count(),
        "featured_tools": FlashingTool.objects.filter(
            is_active=True, is_featured=True
        ).count(),
        "total_templates": FlashingGuideTemplate.objects.filter(is_active=True).count(),
        "auto_templates": FlashingGuideTemplate.objects.filter(
            is_active=True, auto_generate=True
        ).count(),
        "total_generated": GeneratedFlashingGuide.objects.count(),
    }

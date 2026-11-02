"""apps.firmwares.views_flashing — Public flashing tool catalog views."""

from __future__ import annotations

import logging

from django.db.models import F
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from .models import FlashingTool, FlashingToolCategory, GeneratedFlashingGuide

logger = logging.getLogger(__name__)


def flashing_tools(request: HttpRequest) -> HttpResponse:
    """Browse catalog of flashing tools, grouped by category."""
    platform = request.GET.get("platform", "").strip()

    categories = (
        FlashingToolCategory.objects.filter(is_active=True)
        .prefetch_related("tools")
        .order_by("sort_order", "name")
    )

    # Annotate each category with its active tools (optionally filtered)
    for cat in categories:
        tools_qs = cat.tools.filter(is_active=True)  # type: ignore[attr-defined]
        if platform and platform in dict(FlashingTool.Platform.choices):
            tools_qs = tools_qs.filter(platform=platform)
        cat.active_tools = list(tools_qs.order_by("-is_featured", "name"))  # type: ignore[attr-defined]

    featured_tools = FlashingTool.objects.filter(is_active=True, is_featured=True)
    if platform and platform in dict(FlashingTool.Platform.choices):
        featured_tools = featured_tools.filter(platform=platform)
    featured_tools = featured_tools.select_related("category")[:6]

    total_tools = FlashingTool.objects.filter(is_active=True).count()

    return render(
        request,
        "firmwares/flashing_tools.html",
        {
            "categories": categories,
            "featured_tools": featured_tools,
            "total_tools": total_tools,
            "platform_choices": FlashingTool.Platform.choices,
            "current_platform": platform
            if platform in dict(FlashingTool.Platform.choices)
            else "",
        },
    )


def flashing_tool_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Single flashing tool detail page."""
    tool = get_object_or_404(
        FlashingTool.objects.select_related("category"),
        slug=slug,
        is_active=True,
    )

    # Increment view count
    FlashingTool.objects.filter(pk=tool.pk).update(views_count=F("views_count") + 1)

    # Get supported brands
    brands = tool.supported_brands.all().order_by("name")

    # Related generated guides
    related_guides = (
        GeneratedFlashingGuide.objects.filter(
            template__recommended_tools=tool,
        )
        .select_related("post", "brand", "model")
        .order_by("-post__created_at")[:5]
    )

    breadcrumbs = [
        {"label": "Flashing Tools", "url": "/firmwares/tools/"},
        {"label": tool.category.name},
        {"label": tool.name},
    ]

    return render(
        request,
        "firmwares/flashing_tool_detail.html",
        {
            "tool": tool,
            "brands": brands,
            "related_guides": related_guides,
            "breadcrumbs": breadcrumbs,
        },
    )

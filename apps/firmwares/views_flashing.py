"""apps.firmwares.views_flashing — Public flashing tools and guides hub."""

from __future__ import annotations

import logging

from django.core.paginator import Paginator
from django.db.models import F, Prefetch, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.vary import vary_on_headers

from apps.core.search import list_search_context, search_queryset
from apps.core.view_counter import should_count

from .models import (
    Brand,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
)
from .search_providers import GUIDE_FIELDS, TOOL_FIELDS
from .tool_categories import (
    CategoryIndex,
    listed_groups,
    published_guides,
    resolve_category,
)

logger = logging.getLogger(__name__)

GUIDES_PAGE_SIZE = 12


def _published_guides():
    """Guides whose blog post is live; drafts are never exposed publicly."""
    return published_guides()


@vary_on_headers("HX-Request")
def flashing_tools(request: HttpRequest) -> HttpResponse:
    """Tools and guides hub: tools by category, featured tools and latest guides.

    ``q`` searches tools through the shared engine; HTMX requests receive the
    results fragment only.
    """
    platform = request.GET.get("platform", "").strip()
    valid_platform = platform if platform in dict(FlashingTool.Platform.choices) else ""

    tool_filter = Q(is_active=True, category__is_active=True)
    if valid_platform:
        tool_filter &= Q(platform=valid_platform)
    base = FlashingTool.objects.filter(tool_filter)
    result = search_queryset(base, request.GET.get("q"), TOOL_FIELDS)
    tools = result.queryset.order_by("-is_featured", "name")
    tool_total = tools.count()
    categories = list(
        FlashingToolCategory.objects.filter(is_active=True)
        .prefetch_related(Prefetch("tools", queryset=tools, to_attr="active_tools"))
        .order_by("sort_order", "name")
    )

    context = {
        "categories": categories,
        "browse_groups": []
        if result.is_active
        else listed_groups(CategoryIndex.build()),
        "featured_tools": []
        if result.is_active
        else tools.filter(is_featured=True).select_related("category")[:6],
        "tool_total": tool_total,
        "total_tools": FlashingTool.objects.filter(is_active=True).count(),
        "latest_guides": [] if result.is_active else _published_guides()[:6],
        "guide_types": FlashingGuideTemplate.GuideType.choices,
        "platform_choices": FlashingTool.Platform.choices,
        "current_platform": valid_platform,
        **list_search_context(
            result, tool_total, user=request.user, kinds=("tool", "chipset", "brand")
        ),
    }
    if request.headers.get("HX-Request"):
        return render(request, "firmwares/fragments/tool_results.html", context)
    return render(request, "firmwares/flashing_tools.html", context)


@vary_on_headers("HX-Request")
def flashing_guides(request: HttpRequest) -> HttpResponse:
    """Paginated list of published flashing guides, filterable by type and brand.

    ``q`` searches guide titles, brands and devices through the shared engine.
    """
    guide_type = request.GET.get("type", "").strip()
    brand_slug = request.GET.get("brand", "").strip()
    category_slug = request.GET.get("category", "").strip()
    guides = _published_guides()
    index = CategoryIndex.build()
    listed = index.listed()
    category = next((cat for cat in listed if cat.slug == category_slug), None)
    if category is not None:
        guides = guides.filter(pk__in=index.guide_ids(category))
    else:
        category_slug = ""
    if guide_type in dict(FlashingGuideTemplate.GuideType.choices):
        guides = guides.filter(template__guide_type=guide_type)
    else:
        guide_type = ""
    if brand_slug:
        guides = guides.filter(brand__slug=brand_slug)
    result = search_queryset(guides, request.GET.get("q"), GUIDE_FIELDS)

    page = Paginator(result.queryset, GUIDES_PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    context = {
        "guides": page,
        "guide_types": FlashingGuideTemplate.GuideType.choices,
        "brands": Brand.objects.filter(generated_guides__isnull=False)
        .distinct()
        .order_by("name"),
        "selected_type": guide_type,
        "selected_brand": brand_slug,
        "selected_category": category_slug,
        "category_options": [
            cat
            for cat in listed
            if index.guide_ids(cat) and cat.kind != FlashingToolCategory.Kind.GENERAL
        ],
        **list_search_context(
            result,
            page.paginator.count,
            user=request.user,
            kinds=("brand", "model", "tool"),
        ),
    }
    if request.headers.get("HX-Request"):
        return render(request, "firmwares/fragments/guide_results.html", context)
    return render(request, "firmwares/flashing_guides.html", context)


def flashing_category(request: HttpRequest, slug: str) -> HttpResponse:
    """Tools and guides of one category (vendor, family, brand or operating system).

    Only categories that hold an active tool or a published guide (or have a child
    that does) are public. A renamed category redirects to its current slug; an empty
    one redirects to its listed parent, else answers 404.
    """
    category, renamed = resolve_category(slug)
    if category is None:
        raise Http404("No such category.")
    if renamed:
        return redirect(category, permanent=True)
    index = CategoryIndex.build()
    parent = category.parent
    listed_parent = parent if parent is not None and index.is_listed(parent) else None
    if not index.is_listed(category):
        if listed_parent is not None:
            return redirect(listed_parent)
        raise Http404("This category is empty.")
    tools = FlashingTool.objects.filter(
        pk__in=index.tool_ids(category), is_active=True
    ).order_by("-is_featured", "name")
    guides = _published_guides().filter(pk__in=index.guide_ids(category))[:12]
    return render(
        request,
        "firmwares/flashing_category.html",
        {
            "category": category,
            "parent": listed_parent,
            "children": [
                (child, *index.counts(child))
                for child in index.listed_children(category)
            ],
            "tools": tools,
            "guides": guides,
            "tool_total": len(index.tool_ids(category)),
            "guide_total": len(index.guide_ids(category)),
            "guides_url": f"{reverse('firmwares:flashing_guides')}?category={category.slug}",
        },
    )


def flashing_tool_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Single flashing tool detail page."""
    tool = get_object_or_404(
        FlashingTool.objects.select_related("category"),
        slug=slug,
        is_active=True,
    )
    if should_count(request, "tool", tool.pk):
        FlashingTool.objects.filter(pk=tool.pk).update(views_count=F("views_count") + 1)
        tool.views_count += 1

    return render(
        request,
        "firmwares/flashing_tool_detail.html",
        {
            "tool": tool,
            "brands": tool.supported_brands.all().order_by("name"),
            "chipsets": tool.chipsets.select_related("vendor").order_by(
                "vendor__name", "name"
            ),
            "related_guides": _published_guides().filter(
                template__recommended_tools=tool
            )[:5],
            "breadcrumbs": [
                {"label": "Flashing Tools", "url": "/firmwares/tools/"},
                {"label": tool.category.name},
                {"label": tool.name},
            ],
        },
    )

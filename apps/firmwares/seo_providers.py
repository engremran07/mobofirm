"""SEO page providers for the firmware catalogue (registered by ``apps.seo``).

Only facts stored in the catalogue are used: counts come from the public queryset
(`catalog.get_public_firmware_queryset`), so thin pages are identified by the same
review boundary that decides what the public site shows.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count
from django.urls import reverse

from apps.seo import schema
from apps.seo.contentsources import (
    GraphData,
    GraphEdge,
    GraphNode,
    register_content_source,
    register_graph_source,
)
from apps.seo.contracts import Crumb, PageSEO
from apps.seo.models import SEOSettings
from apps.seo.policy import clean_text, truncate
from apps.seo.providers import ProviderContext, register_provider
from apps.seo.sitemap_registry import register_sitemap_filter
from apps.seo.siteurls import same_site_image

from . import catalog
from .models import Brand, Chipset, FlashingTool, Model

ITEM_LIMIT = 20
HOME = ("Home", "/")


def public_firmware_count(**filters: Any) -> int:
    return sum(
        catalog.get_public_firmware_queryset(model_class).filter(**filters).count()
        for _key, model_class in catalog.PUBLIC_FIRMWARE_TYPES
    )


def models_with_public_firmware(minimum: int) -> set[int]:
    counts = catalog.public_counts("model_id")
    return {pk for pk, total in counts.items() if total >= minimum}


def brands_with_models(minimum: int) -> set[int]:
    rows = (
        Model.objects.filter(is_active=True)
        .values("brand_id")
        .annotate(total=Count("pk"))
        .filter(total__gte=max(minimum, 1))
    )
    return {row["brand_id"] for row in rows}


def chipsets_with_models(minimum: int) -> set[int]:
    rows = (
        Model.objects.filter(is_active=True, chipset_ref__isnull=False)
        .values("chipset_ref_id")
        .annotate(total=Count("pk"))
        .filter(total__gte=max(minimum, 1))
    )
    return {row["chipset_ref_id"] for row in rows}


def _crumbs(*items: tuple[str, str]) -> list[Crumb]:
    return [Crumb(name, path) for name, path in items]


def _brand_name_in(brand: Brand, text: str) -> str:
    return text if brand.name.lower() in text.lower() else f"{brand.name} {text}"


def model_label(model: Model) -> str:
    return _brand_name_in(model.brand, model.display_name)


@register_provider("home", "firmwares:library_hub")
def home(ctx: ProviderContext) -> PageSEO:
    brands = Brand.objects.count()
    models = Model.objects.filter(is_active=True).count()
    files = public_firmware_count()
    derived = (
        f"Browse {brands} brands, {models} device models and {files} reviewed "
        "firmware files, with flashing tools and step-by-step guides."
    )
    return PageSEO(
        page_type="home",
        title="Mobile firmware library, device catalogue and flashing guides",
        full_title=False,
        description=ctx.describe("", derived) if files else "",
        path="/",
        sources={"canonical": "route"},
    )


@register_provider("firmwares:brand_list")
def brand_list(ctx: ProviderContext) -> PageSEO:
    total = Brand.objects.count()
    return PageSEO(
        page_type="brand_list",
        title="Firmware brands",
        description=ctx.describe(
            "", f"Browse firmware and device models for {total} phone brands."
        ),
        crumbs=_crumbs(HOME, ("Brands", reverse("firmwares:brand_list"))),
    )


@register_provider("firmwares:brand_detail")
def brand_detail(ctx: ProviderContext) -> PageSEO | None:
    brand = (
        ctx.from_context("brand", Brand)
        or Brand.objects.filter(slug=ctx.kwargs.get("slug", "")).first()
    )
    if brand is None:
        return None
    models = Model.objects.filter(brand=brand, is_active=True)
    model_total = models.count()
    files = public_firmware_count(brand=brand)
    path = reverse("firmwares:brand_detail", kwargs={"slug": brand.slug})
    items = [
        (model_label(model), ctx.absolute(_model_path(model)))
        for model in models.select_related("brand").order_by("name")[:ITEM_LIMIT]
    ]
    page = PageSEO(
        page_type="brand",
        title=f"{brand.name} firmware downloads",
        description=ctx.describe(
            brand.description,
            f"{model_total} {brand.name} device models with {files} reviewed "
            "firmware files.",
        ),
        path=path,
        image=same_site_image(brand.logo_url),
        crumbs=_crumbs(
            HOME, ("Brands", reverse("firmwares:brand_list")), (brand.name, path)
        ),
        object_ref=("firmwares.brand", str(brand.pk)),
        index=model_total >= max(ctx.settings.min_models_for_brand_index, 1),
        noindex_reason="brand has no active device models",
        modified=brand.updated_at,
    )
    page.schema.append(
        schema.collection_page(
            name=f"{brand.name} device models",
            url=ctx.absolute(path),
            items=items,
        )
    )
    return page


def _model_path(model: Model) -> str:
    return reverse(
        "firmwares:model_detail",
        kwargs={"brand_slug": model.brand.slug, "model_slug": model.slug},
    )


@register_provider("firmwares:model_detail")
def model_detail(ctx: ProviderContext) -> PageSEO | None:
    model = (
        Model.objects.select_related("brand")
        .filter(
            brand__slug=ctx.kwargs.get("brand_slug", ""),
            slug=ctx.kwargs.get("model_slug", ""),
        )
        .first()
    )
    if model is None:
        return None
    firmwares = catalog.get_all_firmwares_for_model(model)
    groups = catalog.group_by_category(firmwares)
    label = model_label(model)
    path = _model_path(model)
    facts = [
        f"Model codes {model.all_model_codes_display}"
        if model.all_model_codes_display
        else "",
        f"chipset {model.chipset}" if model.chipset else "",
        f"launch OS {model.os_version}" if model.os_version else "",
    ]
    categories = ", ".join(name for _key, name, _items in groups)
    derived = (
        f"{len(firmwares)} reviewed firmware files for {label}"
        + (f" ({categories})" if categories else "")
        + ". "
        + "; ".join(fact for fact in facts if fact)
    ).strip()
    brand_path = reverse("firmwares:brand_detail", kwargs={"slug": model.brand.slug})
    items = [
        (firmware.original_file_name, ctx.absolute(_firmware_path(firmware)))
        for firmware in firmwares[:ITEM_LIMIT]
    ]
    minimum = max(ctx.settings.min_firmware_for_model_index, 1)
    page = PageSEO(
        page_type="model",
        title=f"{label} firmware download",
        description=ctx.describe(model.description, derived),
        path=path,
        image=same_site_image(model.image_url),
        crumbs=_crumbs(
            HOME,
            ("Brands", reverse("firmwares:brand_list")),
            (model.brand.name, brand_path),
            (label, path),
        ),
        object_ref=("firmwares.model", str(model.pk)),
        index=len(firmwares) >= minimum,
        noindex_reason=f"fewer than {minimum} reviewed firmware files",
        modified=model.updated_at,
    )
    page.schema.append(
        schema.collection_page(
            name=f"{label} firmware files", url=ctx.absolute(path), items=items
        )
    )
    return page


def _firmware_path(firmware: Any) -> str:
    return reverse(
        "firmwares:firmware_detail",
        kwargs={"firmware_type": firmware.firmware_type, "firmware_id": firmware.pk},
    )


@register_provider("firmwares:chipset_list")
def chipset_list(ctx: ProviderContext) -> PageSEO:
    return PageSEO(
        page_type="chipset_list",
        title="Chipsets",
        description=ctx.describe(
            "",
            f"Phone chipsets with the device models and flashing tools that use them "
            f"({Chipset.objects.count()} chipsets).",
        ),
        crumbs=_crumbs(HOME, ("Chipsets", reverse("firmwares:chipset_list"))),
    )


@register_provider("firmwares:chipset_detail")
def chipset_detail(ctx: ProviderContext) -> PageSEO | None:
    chipset = (
        ctx.from_context("chipset", Chipset)
        or Chipset.objects.filter(slug=ctx.kwargs.get("slug", "")).first()
    )
    if chipset is None:
        return None
    models = Model.objects.filter(chipset_ref=chipset, is_active=True)
    total = models.count()
    path = reverse("firmwares:chipset_detail", kwargs={"slug": chipset.slug})
    items = [
        (model_label(model), ctx.absolute(_model_path(model)))
        for model in models.select_related("brand").order_by("name")[:ITEM_LIMIT]
    ]
    page = PageSEO(
        page_type="chipset",
        title=f"{chipset.display_name} firmware and flashing tools",
        description=ctx.describe(
            chipset.description,
            f"{total} device models use the {chipset.display_name} chipset.",
        ),
        path=path,
        crumbs=_crumbs(
            HOME,
            ("Chipsets", reverse("firmwares:chipset_list")),
            (chipset.display_name, path),
        ),
        object_ref=("firmwares.chipset", str(chipset.pk)),
        index=total >= max(ctx.settings.min_models_for_chipset_index, 1),
        noindex_reason="chipset has no active device models",
        modified=chipset.updated_at,
    )
    page.schema.append(
        schema.collection_page(
            name=f"{chipset.display_name} devices", url=ctx.absolute(path), items=items
        )
    )
    return page


@register_provider("firmwares:firmware_detail")
def firmware_detail(ctx: ProviderContext) -> PageSEO | None:
    model_class = catalog.get_firmware_model(ctx.kwargs.get("firmware_type", ""))
    if model_class is None:
        return None
    firmware = (
        catalog.get_public_firmware_queryset(model_class)
        .select_related("brand", "model__brand", "variant")
        .filter(pk=ctx.kwargs.get("firmware_id"))
        .first()
    )
    if firmware is None or firmware.brand is None or firmware.model is None:
        return None
    type_key = catalog.TYPE_KEY_BY_CLASS[model_class]
    category = catalog.FIRMWARE_TYPE_DISPLAY[type_key]
    catalog.label_firmware(firmware, type_key)
    label = model_label(firmware.model)
    path = _firmware_path(firmware)
    title = f"{label} {category.lower()} firmware"
    if firmware.build_number:
        title = f"{title} {firmware.build_number}"
    facts = [
        f"Build {firmware.build_number}" if firmware.build_number else "",
        f"Android {firmware.android_version}" if firmware.android_version else "",
        f"security patch {firmware.security_patch.isoformat()}"
        if firmware.security_patch
        else "",
        f"{catalog.format_file_size(firmware.file_size)}" if firmware.file_size else "",
    ]
    derived = f"{category} firmware for {label}. " + ", ".join(
        fact for fact in facts if fact
    )
    model_path = _model_path(firmware.model)
    brand_path = reverse("firmwares:brand_detail", kwargs={"slug": firmware.brand.slug})
    page = PageSEO(
        page_type="firmware",
        title=title,
        description=ctx.describe("", derived.rstrip(" .,") + "."),
        path=path,
        crumbs=_crumbs(
            HOME,
            ("Brands", reverse("firmwares:brand_list")),
            (firmware.brand.name, brand_path),
            (label, model_path),
            (firmware.original_file_name, path),
        ),
        object_ref=(f"firmwares.{model_class.__name__.lower()}", str(firmware.pk)),
        published=firmware.created_at,
        modified=firmware.updated_at,
    )
    page.schema.append(
        schema.web_page(
            name=title,
            url=ctx.absolute(path),
            description=truncate(derived, 300),
            published=firmware.created_at,
            modified=firmware.updated_at,
            about_name=label,
        )
    )
    return page


@register_provider("firmwares:browse")
def browse(ctx: ProviderContext) -> PageSEO:
    request = ctx.request
    firmware_type = request.GET.get("type", "").strip().lower()
    path = reverse("firmwares:browse")
    crumbs = _crumbs(HOME, ("Firmware", path))
    if firmware_type and firmware_type != "all":
        model_class = catalog.get_firmware_model(firmware_type)
        if model_class is None:
            return PageSEO(
                page_type="firmware_category",
                title="Firmware",
                path=path,
                index=False,
                noindex_reason="unknown firmware category",
            )
        type_key = catalog.TYPE_KEY_BY_CLASS[model_class]
        display = catalog.FIRMWARE_TYPE_DISPLAY[type_key]
        total = catalog.get_public_firmware_queryset(model_class).count()
        return PageSEO(
            page_type="firmware_category",
            title=f"{display} firmware downloads",
            description=ctx.describe(
                "", f"{total} reviewed {display.lower()} firmware files."
            ),
            path=path,
            canonical_params=("type",),
            crumbs=[*crumbs, Crumb(display, f"{path}?type={type_key}")],
            index=total > 0 and firmware_type == type_key,
            noindex_reason="empty category or non-canonical category alias",
        )
    return PageSEO(
        page_type="firmware_browse",
        title="Browse firmware",
        description=ctx.describe(
            "", f"{public_firmware_count()} reviewed firmware files across every brand."
        ),
        path=path,
        index=firmware_type != "all",
        noindex_reason="duplicate of the unfiltered browse page",
        crumbs=crumbs,
    )


@register_provider("firmwares:flashing_tools")
def tools_hub(ctx: ProviderContext) -> PageSEO:
    tools = FlashingTool.objects.filter(is_active=True)
    path = reverse("firmwares:flashing_tools")
    items = [
        (
            tool.name,
            ctx.absolute(reverse("firmwares:flashing_tool_detail", args=[tool.slug])),
        )
        for tool in tools[:ITEM_LIMIT]
    ]
    page = PageSEO(
        page_type="tools_hub",
        title="Firmware flashing tools",
        description=ctx.describe(
            "", f"{tools.count()} flashing, unlocking and recovery tools by platform."
        ),
        path=path,
        crumbs=_crumbs(HOME, ("Flashing tools", path)),
        index=tools.exists(),
        noindex_reason="no active flashing tools",
    )
    page.schema.append(
        schema.collection_page(
            name="Flashing tools", url=ctx.absolute(path), items=items
        )
    )
    return page


@register_provider("firmwares:flashing_tool_detail")
def tool_detail(ctx: ProviderContext) -> PageSEO | None:
    tool = (
        FlashingTool.objects.select_related("category")
        .filter(slug=ctx.kwargs.get("slug", ""), is_active=True)
        .first()
    )
    if tool is None:
        return None
    path = reverse("firmwares:flashing_tool_detail", kwargs={"slug": tool.slug})
    description = clean_text(tool.description)
    page = PageSEO(
        page_type="tool",
        title=f"{tool.name} flashing tool",
        description=ctx.describe(
            description,
            f"{tool.name}, a {FlashingTool.Platform(tool.platform).label} "
            f"{tool.category.name.lower()} tool.",
        ),
        path=path,
        image=tool.hero_image.url if tool.hero_image else "",
        crumbs=_crumbs(
            HOME,
            ("Flashing tools", reverse("firmwares:flashing_tools")),
            (tool.name, path),
        ),
        object_ref=("firmwares.flashingtool", str(tool.pk)),
        modified=tool.updated_at,
    )
    page.schema.append(
        schema.software_application(
            name=tool.name,
            url=ctx.absolute(path),
            description=description,
            operating_system=FlashingTool.Platform(tool.platform).label,
            version=tool.version,
        )
    )
    return page


@register_provider("firmwares:flashing_guides")
def guides_hub(ctx: ProviderContext) -> PageSEO:
    path = reverse("firmwares:flashing_guides")
    return PageSEO(
        page_type="guides_hub",
        title="Firmware flashing guides",
        description=ctx.describe(
            "", "Step-by-step flashing, unlocking and recovery guides by device."
        ),
        path=path,
        crumbs=_crumbs(HOME, ("Flashing guides", path)),
    )


@register_provider("firmwares:universal_search")
def search(_ctx: ProviderContext) -> PageSEO:
    return PageSEO(
        page_type="search",
        title="Search firmware and devices",
        index=False,
        noindex_reason="internal search results",
    )


@register_provider(
    "firmwares:firmware_download",
    "firmwares:firmware_download_start",
    "firmwares:firmware_ad_gate_complete",
    "firmwares:serve_local_file",
)
def download_pages(_ctx: ProviderContext) -> PageSEO:
    return PageSEO(
        page_type="private",
        title="Download",
        index=False,
        follow=False,
        noindex_reason="download and token pages are not search content",
    )


def _firmware_graph() -> GraphData:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    for brand in Brand.objects.all():
        nodes.append(
            GraphNode(
                f"brand:{brand.pk}",
                brand.name,
                "brand",
                reverse("firmwares:brand_detail", kwargs={"slug": brand.slug}),
            )
        )
    for chipset in Chipset.objects.all():
        nodes.append(
            GraphNode(
                f"chipset:{chipset.pk}",
                chipset.display_name,
                "chipset",
                reverse("firmwares:chipset_detail", kwargs={"slug": chipset.slug}),
            )
        )
    for model in Model.objects.filter(is_active=True).select_related("brand"):
        model_key = f"model:{model.pk}"
        nodes.append(
            GraphNode(model_key, model_label(model), "model", _model_path(model))
        )
        edges.append(GraphEdge(f"brand:{model.brand_id}", model_key, "has_model"))
        if model.chipset_ref_id:
            edges.append(
                GraphEdge(model_key, f"chipset:{model.chipset_ref_id}", "uses_chipset")
            )
    tools = FlashingTool.objects.filter(is_active=True)
    for tool in tools.prefetch_related("supported_brands", "chipsets"):
        key = f"tool:{tool.pk}"
        nodes.append(
            GraphNode(
                key,
                tool.name,
                "tool",
                reverse("firmwares:flashing_tool_detail", kwargs={"slug": tool.slug}),
            )
        )
        edges.extend(
            GraphEdge(key, f"brand:{brand.pk}", "supports_brand")
            for brand in tool.supported_brands.all()
        )
        edges.extend(
            GraphEdge(key, f"chipset:{chipset.pk}", "supports_chipset")
            for chipset in tool.chipsets.all()
        )
    return GraphData(nodes, edges)


register_graph_source(_firmware_graph)


def _models_filter(items: list, row: SEOSettings) -> list:
    allowed = models_with_public_firmware(max(row.min_firmware_for_model_index, 1))
    return [item for item in items if item.pk in allowed]


def _brands_filter(items: list, row: SEOSettings) -> list:
    allowed = brands_with_models(row.min_models_for_brand_index)
    return [item for item in items if item.pk in allowed]


def _chipsets_filter(items: list, row: SEOSettings) -> list:
    allowed = chipsets_with_models(row.min_models_for_chipset_index)
    return [item for item in items if item.pk in allowed]


register_sitemap_filter("models", _models_filter)
register_sitemap_filter("brands", _brands_filter)
register_sitemap_filter("chipsets", _chipsets_filter)


def _firmware_url(firmware: Any) -> str:
    return reverse(
        "firmwares:firmware_detail",
        kwargs={
            "firmware_type": catalog.TYPE_KEY_BY_CLASS[type(firmware)],
            "firmware_id": firmware.pk,
        },
    )


register_content_source(
    "firmwares.brand",
    url=lambda brand: reverse("firmwares:brand_detail", kwargs={"slug": brand.slug}),
    is_public=lambda _brand: True,
)
register_content_source(
    "firmwares.chipset",
    url=lambda chipset: reverse(
        "firmwares:chipset_detail", kwargs={"slug": chipset.slug}
    ),
    is_public=lambda _chipset: True,
)
register_content_source(
    "firmwares.model",
    url=lambda model: reverse(
        "firmwares:model_detail",
        kwargs={"brand_slug": model.brand.slug, "model_slug": model.slug},
    ),
    is_public=lambda model: model.is_active,
)
for _type_key, _model_class in catalog.PUBLIC_FIRMWARE_TYPES:
    register_content_source(
        f"firmwares.{_model_class.__name__.lower()}",
        url=_firmware_url,
        is_public=lambda firmware: firmware.is_active and firmware.is_verified,
    )

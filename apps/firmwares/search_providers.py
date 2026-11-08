"""Search providers for the firmware catalogue (public, approved content only)."""

from __future__ import annotations

from django.db.models import Count, Q, QuerySet
from django.urls import reverse

from apps.core.search import (
    QuerySetProvider,
    SearchContext,
    SearchHit,
    SearchProvider,
    register,
)
from apps.core.search.db import candidate_limit, tokens_match
from apps.core.search.text import score_text

from .catalog import (
    FIRMWARE_TYPE_DISPLAY,
    PUBLIC_FIRMWARE_TYPES,
    get_public_firmware_queryset,
)
from .models import (
    Brand,
    Chipset,
    FlashingTool,
    Model,
    Variant,
)


def _join(*parts: object) -> str:
    return " · ".join(str(part) for part in parts if part)


def _pk_filter(value: str, field: str) -> Q:
    """``<field>`` equals a numeric id, or the slug of the related row."""
    if value.isdigit():
        return Q(**{field: int(value)})
    return Q(**{f"{field}__slug": value})


class BrandProvider(QuerySetProvider[Brand]):
    kind = "brand"
    label = "Brand"
    label_plural = "Brands"
    icon = "building-2"
    priority = 10
    min_length = 1
    supports_browse = True
    search_fields = ("name", "slug")
    ordering = ("name",)

    def get_queryset(self, context: SearchContext) -> QuerySet[Brand]:
        return Brand.objects.annotate(
            model_total=Count("models", filter=Q(models__is_active=True))
        )

    def score_fields(self, obj: Brand) -> tuple[str, ...]:
        return (obj.name, obj.slug)

    def build_hit(self, obj: Brand, score: float, context: SearchContext) -> SearchHit:
        total = int(getattr(obj, "model_total", 0))
        return SearchHit(
            kind=self.kind,
            title=obj.name,
            subtitle=f"{total} model{'s' if total != 1 else ''}",
            url=reverse("firmwares:brand_detail", args=[obj.slug]),
            icon=self.icon,
            score=score,
            value=str(obj.pk),
            label=obj.name,
            extra=(("slug", obj.slug),),
        )


class ModelProvider(QuerySetProvider[Model]):
    """Device models. ``filter.brand`` (id or slug) narrows a model picker."""

    kind = "model"
    label = "Device"
    label_plural = "Devices"
    icon = "smartphone"
    priority = 20
    min_length = 1
    supports_browse = True
    search_fields = (
        "name",
        "marketing_name",
        "model_code",
        "codename",
        "also_known_as",
        "slug",
        "brand__name",
    )
    ordering = ("brand__name", "name")

    def get_queryset(self, context: SearchContext) -> QuerySet[Model]:
        queryset = Model.objects.filter(is_active=True).select_related("brand")
        brand = context.filters.get("brand", "").strip()
        if brand:
            queryset = queryset.filter(_pk_filter(brand, "brand"))
        return queryset

    def score_fields(self, obj: Model) -> tuple[str, ...]:
        return (
            obj.name,
            obj.marketing_name,
            obj.model_code,
            obj.codename,
            f"{obj.brand.name} {obj.name}",
            obj.also_known_as,
        )

    def build_hit(self, obj: Model, score: float, context: SearchContext) -> SearchHit:
        return SearchHit(
            kind=self.kind,
            title=obj.marketing_name or obj.name,
            subtitle=_join(obj.brand.name, obj.model_code),
            url=reverse("firmwares:model_detail", args=[obj.brand.slug, obj.slug]),
            icon=self.icon,
            score=score,
            value=str(obj.pk),
            label=f"{obj.brand.name} {obj.name}",
            extra=(("brand", str(obj.brand_id)),),
        )


class VariantProvider(QuerySetProvider[Variant]):
    """Regional/hardware variants. ``filter.model`` (id or slug) narrows a picker."""

    kind = "variant"
    label = "Variant"
    label_plural = "Variants"
    icon = "layers"
    priority = 30
    min_length = 1
    supports_browse = True
    search_fields = (
        "name",
        "model_code",
        "region",
        "board_id",
        "chipset",
        "model__name",
        "model__model_code",
        "model__brand__name",
    )
    ordering = ("model__brand__name", "model__name", "name")

    def get_queryset(self, context: SearchContext) -> QuerySet[Variant]:
        queryset = Variant.objects.filter(
            is_active=True, model__is_active=True
        ).select_related("model", "model__brand")
        model = context.filters.get("model", "").strip()
        if model:
            queryset = queryset.filter(_pk_filter(model, "model"))
        return queryset

    def score_fields(self, obj: Variant) -> tuple[str, ...]:
        return (
            obj.name,
            obj.model_code,
            f"{obj.model.brand.name} {obj.model.name} {obj.name}",
            obj.region,
            obj.board_id,
            obj.chipset,
        )

    def build_hit(
        self, obj: Variant, score: float, context: SearchContext
    ) -> SearchHit:
        device = f"{obj.model.brand.name} {obj.model.name}"
        return SearchHit(
            kind=self.kind,
            title=obj.name,
            subtitle=_join(device, obj.model_code, obj.region),
            url=reverse(
                "firmwares:model_detail", args=[obj.model.brand.slug, obj.model.slug]
            ),
            icon=self.icon,
            score=score,
            value=str(obj.pk),
            label=f"{device} {obj.name}".strip(),
            extra=(("model", str(obj.model_id)),),
        )


class ChipsetProvider(QuerySetProvider[Chipset]):
    kind = "chipset"
    label = "Chipset"
    label_plural = "Chipsets"
    icon = "cpu"
    priority = 40
    search_fields = ("name", "vendor", "family", "slug")
    ordering = ("vendor", "name")

    def get_queryset(self, context: SearchContext) -> QuerySet[Chipset]:
        return Chipset.objects.all()

    def score_fields(self, obj: Chipset) -> tuple[str, ...]:
        aliases = obj.aliases if isinstance(obj.aliases, list) else []
        return (obj.display_name, obj.name, obj.family, *map(str, aliases))

    def build_hit(
        self, obj: Chipset, score: float, context: SearchContext
    ) -> SearchHit:
        return SearchHit(
            kind=self.kind,
            title=obj.display_name,
            subtitle=obj.family or obj.vendor,
            url=reverse("firmwares:chipset_detail", args=[obj.slug]),
            icon=self.icon,
            score=score,
            value=str(obj.pk),
            label=obj.display_name,
        )


class ToolProvider(QuerySetProvider[FlashingTool]):
    kind = "tool"
    label = "Tool"
    label_plural = "Tools"
    icon = "wrench"
    priority = 50
    search_fields = ("name", "slug", "category__name", "version")
    ordering = ("-is_featured", "name")

    def get_queryset(self, context: SearchContext) -> QuerySet[FlashingTool]:
        return FlashingTool.objects.filter(
            is_active=True, category__is_active=True
        ).select_related("category")

    def score_fields(self, obj: FlashingTool) -> tuple[str, ...]:
        return (obj.name, obj.category.name, obj.version)

    def build_hit(
        self, obj: FlashingTool, score: float, context: SearchContext
    ) -> SearchHit:
        return SearchHit(
            kind=self.kind,
            title=obj.name,
            subtitle=_join(obj.category.name, obj.get_platform_display()),
            url=reverse("firmwares:flashing_tool_detail", args=[obj.slug]),
            icon=self.icon,
            score=score,
            value=str(obj.pk),
            label=obj.name,
        )


class FirmwareBuildProvider(SearchProvider):
    """Approved firmware builds of every classification (never pending files)."""

    kind = "firmware"
    label = "Firmware"
    label_plural = "Firmware"
    icon = "file-archive"
    priority = 60
    min_length = 3

    search_fields = (
        "original_file_name",
        "build_number",
        "android_version",
        "chipset",
        "brand__name",
        "model__name",
        "model__model_code",
        "variant__name",
    )

    def search(self, context: SearchContext) -> list[SearchHit]:
        condition = tokens_match(context, self.search_fields)
        hits: list[SearchHit] = []
        for type_key, model_class in PUBLIC_FIRMWARE_TYPES:
            rows = (
                get_public_firmware_queryset(model_class)
                .filter(condition)
                .select_related("brand", "model", "variant")
                .order_by("-created_at")[: candidate_limit(context)]
            )
            for firmware in rows:
                device = _join(
                    firmware.brand.name if firmware.brand else "",
                    firmware.model.name if firmware.model else "",
                    firmware.variant.name if firmware.variant else "",
                )
                score = score_text(
                    context.query,
                    context.tokens,
                    firmware.original_file_name,
                    firmware.build_number,
                    device,
                    firmware.android_version,
                    firmware.chipset,
                )
                if score <= 0:
                    continue
                hits.append(
                    SearchHit(
                        kind=self.kind,
                        title=firmware.original_file_name,
                        subtitle=_join(device, FIRMWARE_TYPE_DISPLAY[type_key]),
                        url=reverse(
                            "firmwares:firmware_detail", args=[type_key, firmware.pk]
                        ),
                        icon=self.icon,
                        score=score,
                        value=f"{type_key}:{firmware.pk}",
                        label=firmware.original_file_name,
                    )
                )
        return hits


register(BrandProvider())
register(ModelProvider())
register(VariantProvider())
register(ChipsetProvider())
register(ToolProvider())
register(FirmwareBuildProvider())

"""Search providers for the firmware catalogue (public, approved content only)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db.models import Count, Q, QuerySet, TextField
from django.db.models.functions import Cast
from django.urls import reverse

from apps.core.search import (
    QuerySetProvider,
    SearchContext,
    SearchHit,
    SearchProvider,
    VocabTerm,
    register,
)
from apps.core.search.db import candidate_limit, tokens_match
from apps.core.search.synonyms import FIRMWARE_TYPE_ALIASES
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
from .popularity import popular_files, popular_models


def _join(*parts: object) -> str:
    return " · ".join(str(part) for part in parts if part)


def _pk_filter(value: str, field: str) -> Q:
    """``<field>`` equals a numeric id, or the slug of the related row."""
    if value.isdigit():
        return Q(**{field: int(value)})
    return Q(**{f"{field}__slug": value})


BRAND_FIELDS = ("name", "slug")
MODEL_FIELDS = (
    "name",
    "marketing_name",
    "model_code",
    "codename",
    "also_known_as",
    "slug",
    "chipset",
    "chipset_ref__name",
    "brand__name",
)
TOOL_FIELDS = ("name", "slug", "category__name", "version")
GUIDE_FIELDS = (
    "post__title",
    "brand__name",
    "model__name",
    "model__marketing_name",
    "model__model_code",
)


class BrandProvider(QuerySetProvider[Brand]):
    """Brands by name or slug, with their active model count."""

    kind = "brand"
    label = "Brand"
    label_plural = "Brands"
    icon = "building-2"
    priority = 10
    min_length = 1
    supports_browse = True
    search_fields = BRAND_FIELDS
    ordering = ("name",)
    suggestible = True
    watches = (Brand,)

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
    search_fields = MODEL_FIELDS
    ordering = ("brand__name", "name")
    noun = "model"
    popular_title = "Popular models"
    suggestible = True
    watches = (Model, Brand)

    def popular(
        self, *, limit: int, window_days: int, scope: Mapping[str, str]
    ) -> list[SearchHit]:
        models = popular_models(
            limit=limit,
            window_days=window_days,
            brand=scope.get("brand", ""),
            chipset=scope.get("chipset", ""),
        )
        context = SearchContext(query="", tokens=(), limit=limit)
        return [self.build_hit(model, 0.0, context) for model in models]

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
            obj.chipset,
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
    noun = "variant"
    suggestible = True
    watches = (Variant, Model)

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


CHIPSET_ALIAS_TEXT = "alias_text"
CHIPSET_FIELDS = ("name", "vendor", "family", "slug", CHIPSET_ALIAS_TEXT)
CHIPSET_ANNOTATIONS = {CHIPSET_ALIAS_TEXT: Cast("aliases", TextField())}


class ChipsetProvider(QuerySetProvider[Chipset]):
    """Chipsets by name, vendor, family or alias (alias text is a JSON cast)."""

    kind = "chipset"
    label = "Chipset"
    label_plural = "Chipsets"
    icon = "cpu"
    priority = 40
    search_fields = CHIPSET_FIELDS
    ordering = ("vendor", "name")
    suggestible = True
    supports_browse = True
    watches = (Chipset,)

    def get_queryset(self, context: SearchContext) -> QuerySet[Chipset]:
        return Chipset.objects.annotate(**CHIPSET_ANNOTATIONS)

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
    """Active flashing tools in active categories."""

    kind = "tool"
    label = "Tool"
    label_plural = "Tools"
    icon = "wrench"
    priority = 50
    search_fields = TOOL_FIELDS
    ordering = ("-is_featured", "name")
    suggestible = True
    watches = (FlashingTool,)

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


class FirmwareTypeProvider(SearchProvider):
    """Firmware classifications (official, engineering, readback, ...).

    Opens the firmware browser filtered to that classification. Spellings such as
    "stock" or "eng" resolve through the shared synonyms.
    """

    kind = "firmware_type"
    label = "Firmware type"
    label_plural = "Firmware types"
    noun = "firmware type"
    icon = "layers-3"
    priority = 65
    min_length = 3

    def _types(self) -> list[tuple[str, str, tuple[str, ...]]]:
        browse = reverse("firmwares:browse")
        return [
            (
                FIRMWARE_TYPE_DISPLAY[key],
                f"{browse}?type={key}",
                (FIRMWARE_TYPE_DISPLAY[key], key, *FIRMWARE_TYPE_ALIASES.get(key, ())),
            )
            for key, _model_class in PUBLIC_FIRMWARE_TYPES
            if key in FIRMWARE_TYPE_ALIASES
        ]

    def search(self, context: SearchContext) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for title, url, spellings in self._types():
            score = score_text(context.query, context.tokens, *spellings)
            if score > 0:
                hits.append(
                    SearchHit(
                        kind=self.kind,
                        title=f"{title} firmware",
                        subtitle="Browse by type",
                        url=url,
                        icon=self.icon,
                        score=score,
                        value=title.lower(),
                        label=title,
                    )
                )
        return hits

    def vocabulary(self) -> list[VocabTerm]:
        return [
            VocabTerm(
                kind=self.kind,
                title=title,
                url=url,
                icon=self.icon,
                terms=spellings,
            )
            for title, url, spellings in self._types()
        ]


FIRMWARE_FIELDS = (
    "original_file_name",
    "build_number",
    "android_version",
    "chipset",
    "brand__name",
    "model__name",
    "model__marketing_name",
    "model__model_code",
    "variant__name",
)


class FirmwareBuildProvider(SearchProvider):
    """Approved firmware builds of every classification (never pending files)."""

    kind = "firmware"
    label = "Firmware"
    label_plural = "Firmware"
    icon = "file-archive"
    priority = 60
    min_length = 3

    search_fields = FIRMWARE_FIELDS
    popular_title = "Popular firmware"

    def watched_models(self) -> list[type]:
        return [model_class for _key, model_class in PUBLIC_FIRMWARE_TYPES]

    def _hit(self, firmware: Any, type_key: str, score: float) -> SearchHit:
        device = _join(
            firmware.brand.name if firmware.brand else "",
            firmware.model.name if firmware.model else "",
            firmware.variant.name if firmware.variant else "",
        )
        return SearchHit(
            kind=self.kind,
            title=firmware.original_file_name,
            subtitle=_join(device, FIRMWARE_TYPE_DISPLAY[type_key]),
            url=reverse("firmwares:firmware_detail", args=[type_key, firmware.pk]),
            icon=self.icon,
            score=score,
            value=f"{type_key}:{firmware.pk}",
            label=firmware.original_file_name,
        )

    def popular(
        self, *, limit: int, window_days: int, scope: Mapping[str, str]
    ) -> list[SearchHit]:
        files = popular_files(
            limit=limit,
            window_days=window_days,
            brand=scope.get("brand", ""),
            chipset=scope.get("chipset", ""),
        )
        return [self._hit(item.firmware, item.type_key, 0.0) for item in files]

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
                if score > 0:
                    hits.append(self._hit(firmware, type_key, score))
        return hits


register(BrandProvider())
register(ModelProvider())
register(VariantProvider())
register(ChipsetProvider())
register(ToolProvider())
register(FirmwareTypeProvider())
register(FirmwareBuildProvider())

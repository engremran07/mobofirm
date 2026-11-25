"""Bounded staff library of public content and its generated media status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import CharField, F, OuterRef, Q, Subquery, Value
from django.urls import NoReverseMatch, reverse
from django.utils.html import strip_tags

from apps.core.app_service import app_enabled
from apps.distribution import content as content_module
from apps.distribution import feeds
from apps.distribution.models import MediaAsset, VideoJob
from apps.distribution.video import sources as video_sources

PAGE_SIZE = 24
LIBRARY_KINDS = ("firmware", "blog", "guide", "tool", "brand", "chipset")
FIRMWARE_TYPES = ("all", *feeds.FIRMWARE_TYPES)


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    kind: str
    ref: str
    media_kind: str
    media_ref: str
    title: str
    label: str
    url: str
    lines: tuple[str, ...] = ()
    video_kind: str = ""
    video_ref: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.ref}"


@dataclass(frozen=True, slots=True)
class LibraryPage:
    entries: list[LibraryEntry]
    number: int
    num_pages: int
    count: int
    has_next: bool
    has_previous: bool


@dataclass(frozen=True, slots=True)
class MediaStatus:
    card: str
    vertical: str
    legacy_video: str
    motion_video: str


def _page(queryset: Any, number: int, convert: Any) -> LibraryPage:
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(number)
    return LibraryPage(
        entries=[entry for row in page.object_list if (entry := convert(row))],
        number=page.number,
        num_pages=paginator.num_pages,
        count=paginator.count,
        has_next=page.has_next(),
        has_previous=page.has_previous(),
    )


def _get_model(app_label: str, model_name: str) -> Any:
    return apps.get_model(app_label, model_name)


def _firmware_models(selected_type: str) -> list[tuple[str, Any]]:
    if not (apps.is_installed("apps.firmwares") and app_enabled("firmwares")):
        return []
    model_types: tuple[str, ...] = feeds.FIRMWARE_TYPES
    if selected_type in feeds.FIRMWARE_TYPES:
        model_types = (selected_type,)
    result = []
    for firmware_type in model_types:
        try:
            model = _get_model("firmwares", f"{firmware_type}firmware")
        except LookupError:
            continue
        result.append((firmware_type, model))
    return result


def _firmware_page(number: int, selected_type: str) -> LibraryPage:
    models = _firmware_models(selected_type)
    querysets = [
        model.objects.filter(is_verified=True, is_active=True)
        .annotate(firmware_type=Value(firmware_type, output_field=CharField()))
        .values("created_at", "pk", "firmware_type")
        for firmware_type, model in models
    ]
    if not querysets:
        return LibraryPage([], 1, 1, 0, False, False)
    rows = querysets[0]
    if len(querysets) > 1:
        rows = rows.union(*querysets[1:], all=True)
    rows = rows.order_by("-created_at", "-pk")
    paginator = Paginator(rows, PAGE_SIZE)
    page = paginator.get_page(number)
    grouped: dict[str, list[int]] = {}
    for row in page.object_list:
        grouped.setdefault(row["firmware_type"], []).append(row["pk"])
    records: dict[tuple[str, int], Any] = {}
    for firmware_type, model in models:
        ids = grouped.get(firmware_type, [])
        if not ids:
            continue
        found = model.objects.filter(pk__in=ids).select_related(
            "brand",
            "model",
            "variant",
            "model__chipset_ref__vendor",
            "variant__chipset_ref__vendor",
        )
        records.update({(firmware_type, row.pk): row for row in found})
    entries = []
    for row in page.object_list:
        firmware_type = row["firmware_type"]
        firmware = records.get((firmware_type, row["pk"]))
        item = (
            content_module.firmware_content(firmware, firmware_type, str(row["pk"]))
            if firmware is not None
            else None
        )
        if item is not None:
            entries.append(
                LibraryEntry(
                    kind="firmware",
                    ref=item.object_ref,
                    media_kind=item.kind,
                    media_ref=item.object_ref,
                    title=item.title,
                    label=f"{firmware_type.title()} firmware",
                    url=item.url,
                    lines=tuple(
                        value
                        for value in (
                            item.brand,
                            item.model,
                            item.chipset,
                            f"Android {item.android_version}"
                            if item.android_version
                            else "",
                            f"Build {item.build_number}" if item.build_number else "",
                        )
                        if value
                    ),
                    video_kind="firmware",
                    video_ref=item.object_ref,
                )
            )
    return LibraryPage(
        entries,
        page.number,
        paginator.num_pages,
        paginator.count,
        page.has_next(),
        page.has_previous(),
    )


def _blog_entry(post: Any) -> LibraryEntry | None:
    item = content_module.post_content(post)
    if item is None:
        return None
    label = "Flashing guide" if item.is_guide else "Blog post"
    return LibraryEntry(
        kind="guide" if item.is_guide else "blog",
        ref=item.object_ref,
        media_kind="blog",
        media_ref=item.object_ref,
        title=item.title,
        label=label,
        url=item.url,
        lines=tuple(v for v in (item.topic, item.summary) if v),
        video_kind="blog",
        video_ref=item.object_ref,
    )


def _blog_page(kind: str, number: int) -> LibraryPage:
    if not (apps.is_installed("apps.blog") and app_enabled("blog")):
        return LibraryPage([], 1, 1, 0, False, False)
    post_model = _get_model("blog", "Post")
    posts = post_model.published().select_related(
        "author", "firmware_brand", "firmware_model"
    )
    if apps.is_installed("apps.firmwares"):
        posts = posts.select_related("flashing_guide_source")
        posts = (
            posts.filter(flashing_guide_source__isnull=False)
            if kind == "guide"
            else posts.filter(flashing_guide_source__isnull=True)
        )
    posts = posts.prefetch_related("tags").order_by("-published_at", "-pk")
    return _page(posts, number, _blog_entry)


def _public_entity_ids(field: str) -> set[int]:
    found: set[int] = set()
    for _firmware_type, model in _firmware_models("all"):
        found.update(
            model.objects.filter(is_verified=True, is_active=True)
            .exclude(**{f"{field}__isnull": True})
            .values_list(field, flat=True)
            .distinct()
        )
    return found


def _spotlight_entry(kind: str, entity: Any) -> LibraryEntry | None:
    ref = f"{kind}:{entity.slug}"
    source = video_sources.load("spotlight", ref)
    if source is None:
        return None
    url_name = (
        "firmwares:brand_detail" if kind == "brand" else "firmwares:chipset_detail"
    )
    label = "Brand spotlight" if kind == "brand" else "Chipset spotlight"
    try:
        url = reverse(url_name, kwargs={"slug": entity.slug})
    except NoReverseMatch:
        return None
    return LibraryEntry(
        kind=kind,
        ref=ref,
        media_kind="spotlight",
        media_ref=ref,
        title=source.title,
        label=label,
        url=url,
        lines=tuple(title for title, _detail in source.items),
        video_kind="spotlight",
        video_ref=ref,
    )


def _brand_page(number: int) -> LibraryPage:
    if not (apps.is_installed("apps.firmwares") and app_enabled("firmwares")):
        return LibraryPage([], 1, 1, 0, False, False)
    brand_model = _get_model("firmwares", "Brand")
    queryset = brand_model.objects.filter(pk__in=_public_entity_ids("brand_id"))
    queryset = queryset.order_by("name")
    return _page(queryset, number, lambda row: _spotlight_entry("brand", row))


def _chipset_page(number: int) -> LibraryPage:
    if not (apps.is_installed("apps.firmwares") and app_enabled("firmwares")):
        return LibraryPage([], 1, 1, 0, False, False)
    chipset_model = _get_model("firmwares", "Chipset")
    chipset_ids = _public_entity_ids("model__chipset_ref_id")
    chipset_ids.update(_public_entity_ids("variant__chipset_ref_id"))
    queryset = chipset_model.objects.filter(pk__in=chipset_ids).select_related("vendor")
    queryset = queryset.order_by("vendor__name", "name")
    return _page(queryset, number, lambda row: _spotlight_entry("chipset", row))


def _tool_entry(tool: Any) -> LibraryEntry | None:
    try:
        url = reverse("firmwares:flashing_tool_detail", kwargs={"slug": tool.slug})
    except NoReverseMatch:
        return None
    details = (
        tool.get_platform_display(),
        tool.get_risk_level_display(),
        f"Version {tool.version}" if tool.version else "",
    )
    summary = " ".join(strip_tags(tool.description or "").split())
    return LibraryEntry(
        kind="tool",
        ref=f"tool:{tool.slug}",
        media_kind="tool",
        media_ref=f"tool:{tool.slug}",
        title=tool.name,
        label="Flashing tool",
        url=url,
        lines=tuple(value for value in (*details, summary[:140]) if value),
    )


def _tool_page(number: int) -> LibraryPage:
    if not (apps.is_installed("apps.firmwares") and app_enabled("firmwares")):
        return LibraryPage([], 1, 1, 0, False, False)
    tool_model = _get_model("firmwares", "FlashingTool")
    queryset = tool_model.objects.filter(is_active=True).order_by(
        "-is_featured", "name"
    )
    return _page(queryset, number, _tool_entry)


def get_page(kind: str, number: int, firmware_type: str = "all") -> LibraryPage:
    if kind not in LIBRARY_KINDS:
        kind = "firmware"
    if firmware_type not in FIRMWARE_TYPES:
        firmware_type = "all"
    if kind == "firmware":
        return _firmware_page(number, firmware_type)
    if kind in {"blog", "guide"}:
        return _blog_page(kind, number)
    if kind == "tool":
        return _tool_page(number)
    if kind == "brand":
        return _brand_page(number)
    return _chipset_page(number)


def resolve_entry(kind: str, ref: str) -> LibraryEntry | None:
    """Re-resolve an item against current public state before any bulk operation."""
    if kind == "firmware":
        item = content_module.load("firmware", ref)
        if item is None:
            return None
        firmware_type = ref.partition(":")[0]
        return LibraryEntry(
            kind="firmware",
            ref=ref,
            media_kind="firmware",
            media_ref=ref,
            title=item.title,
            label=f"{firmware_type.title()} firmware",
            url=item.url,
            lines=tuple(
                value
                for value in (
                    item.brand,
                    item.model,
                    item.chipset,
                    f"Android {item.android_version}" if item.android_version else "",
                    f"Build {item.build_number}" if item.build_number else "",
                )
                if value
            ),
            video_kind="firmware",
            video_ref=ref,
        )
    if kind in {"blog", "guide"}:
        item = content_module.load("blog", ref)
        if item is None or item.is_guide != (kind == "guide"):
            return None
        post_model = _get_model("blog", "Post")
        relations = ["author", "firmware_brand", "firmware_model"]
        if apps.is_installed("apps.firmwares"):
            relations.append("flashing_guide_source")
        post = (
            post_model.published()
            .filter(pk=ref.partition(":")[2])
            .select_related(*relations)
            .prefetch_related("tags")
            .first()
        )
        return _blog_entry(post) if post is not None else None
    if kind == "tool":
        if not (apps.is_installed("apps.firmwares") and app_enabled("firmwares")):
            return None
        prefix, separator, slug = ref.partition(":")
        if prefix != "tool" or not separator:
            return None
        tool = (
            _get_model("firmwares", "FlashingTool")
            .objects.filter(slug=slug, is_active=True)
            .first()
        )
        return _tool_entry(tool) if tool is not None else None
    if kind in {"brand", "chipset"}:
        prefix, separator, slug = ref.partition(":")
        if prefix != kind or not separator:
            return None
        source = video_sources.load("spotlight", ref)
        if source is None:
            return None
        model_name = "Brand" if kind == "brand" else "Chipset"
        entity_model = _get_model("firmwares", model_name)
        entity = entity_model.objects.filter(slug=slug).first()
        return _spotlight_entry(kind, entity) if entity is not None else None
    return None


def statuses(entries: list[LibraryEntry]) -> dict[str, MediaStatus]:
    pairs = {(entry.media_kind, entry.media_ref) for entry in entries}
    if not pairs:
        return {}
    query = Q()
    for kind, ref in pairs:
        query |= Q(content_kind=kind, object_ref=ref)
    assets = {
        (asset.content_kind, asset.object_ref, asset.asset_kind): asset
        for asset in MediaAsset.objects.filter(query)
    }
    video_pairs = {
        (entry.video_kind, entry.video_ref) for entry in entries if entry.video_kind
    }
    video_query = Q()
    for kind, ref in video_pairs:
        video_query |= Q(content_kind=kind, object_ref=ref)
    jobs = {}
    if video_pairs:
        newest = VideoJob.objects.filter(
            content_kind=OuterRef("content_kind"),
            object_ref=OuterRef("object_ref"),
        ).order_by("-created_at", "-pk")
        jobs = {
            (job.content_kind, job.object_ref): job
            for job in VideoJob.objects.filter(video_query)
            .annotate(latest_pk=Subquery(newest.values("pk")[:1]))
            .filter(pk=F("latest_pk"))
        }

    def asset_state(kind: str, ref: str, asset_kind: str) -> str:
        asset = assets.get((kind, ref, asset_kind))
        if asset is None:
            return "Not generated"
        if not asset.storage_name:
            return asset.reason or "Generation failed"
        return "Ready" if default_storage.exists(asset.storage_name) else "File missing"

    result = {}
    for entry in entries:
        job = jobs.get((entry.video_kind, entry.video_ref))
        if not entry.video_kind:
            motion_video = "No video template"
        elif job is None:
            motion_video = "Not created"
        elif (job.video_name and not default_storage.exists(job.video_name)) or (
            job.status
            in {
                VideoJob.Status.READY,
                VideoJob.Status.APPROVED,
                VideoJob.Status.POSTED,
            }
            and not job.video_name
        ):
            motion_video = "File missing"
        else:
            motion_video = job.get_status_display()
        result[entry.key] = MediaStatus(
            card=asset_state(
                entry.media_kind, entry.media_ref, MediaAsset.AssetKind.CARD
            ),
            vertical=asset_state(
                entry.media_kind, entry.media_ref, MediaAsset.AssetKind.VERTICAL
            ),
            legacy_video=asset_state(
                entry.media_kind, entry.media_ref, MediaAsset.AssetKind.VIDEO
            ),
            motion_video=motion_video,
        )
    return result

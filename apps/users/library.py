"""Data behind the My files pages: the library, uploads and quota panel.

Files served to a user are recorded once by ``apps.firmwares`` (``ServedDownload``);
paid-file entitlements and quotas come from ``apps.shop.entitlements``. Both are read
through the app registry and lazy imports so the account area keeps working when
either app is not installed (the matching panel then says so).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from apps.core.app_service import AppService, app_enabled
from apps.core.search import ItemSearch, search_items
from apps.core.utils.managers import base_manager, default_manager

TIERS = (("free", "Free"), ("featured", "Featured"), ("paid", "Paid"))
ACTIVE = "active"
HISTORY = "history"
LIBRARY_TABS = ((ACTIVE, "Active"), (HISTORY, "History"))
UPLOAD_STATUSES = (
    ("pending", "Awaiting review"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
)
# A library is per person; this only bounds a pathological account.
MAX_LIBRARY_ROWS = 1000


def firmwares_available() -> bool:
    """Whether the firmware app is installed and switched on."""
    return django_apps.is_installed("apps.firmwares") and app_enabled("firmwares")


def shop_available() -> bool:
    """Whether the shop app is installed and switched on."""
    return django_apps.is_installed("apps.shop") and app_enabled("shop")


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


@dataclass(frozen=True)
class LibraryFilters:
    tier: str = ""
    brand: str = ""
    query: str = ""
    date_from: date | None = None
    date_to: date | None = None

    @classmethod
    def from_query(cls, params: QueryDict) -> LibraryFilters:
        tier = params.get("tier", "")
        return cls(
            tier=tier if tier in dict(TIERS) else "",
            brand=params.get("brand", "").strip()[:100],
            query=params.get("q", "").strip()[:100],
            date_from=_parse_date(params.get("from", "")),
            date_to=_parse_date(params.get("to", "")),
        )

    @property
    def active(self) -> bool:
        return any((self.tier, self.brand, self.query, self.date_from, self.date_to))


@dataclass(frozen=True)
class LibraryEntry:
    """One file served to the user, with the links to open or fetch it again."""

    name: str
    brand: str
    brand_slug: str
    device: str
    variant: str
    type_label: str
    tier: str
    tier_label: str
    paid_amount: Decimal | None
    counted_in_quota: bool
    served_at: datetime
    last_served_at: datetime
    times_served: int
    detail_url: str
    download_url: str
    available: bool
    state: str = "active"
    state_code: str = "active"
    state_message: str = ""
    renew_url: str = ""
    rule_note: str = ""

    @property
    def can_download(self) -> bool:
        """Whether the file may be fetched now (still published and entitled)."""
        return self.available and self.state == ACTIVE

    @property
    def search_fields(self) -> tuple[str, ...]:
        """Strings the library search matches, most important first."""
        return (self.name, self.brand, self.device, self.variant, self.type_label)


def _tier_label(tier: str, counted: bool) -> str:
    if tier == "paid":
        return "Paid · counted in quota" if counted else "Paid"
    if tier == "featured":
        return "Featured · counted in quota" if counted else "Featured"
    return "Free"


def _firmware_rows(rows: list[Any]) -> dict[tuple[int, Any], Any]:
    """Resolve served rows to their typed firmware rows, one query per type."""
    ids_by_type: dict[int, list[Any]] = {}
    for row in rows:
        ids_by_type.setdefault(row.content_type_id, []).append(row.object_id)
    resolved: dict[tuple[int, Any], Any] = {}
    for content_type_id, ids in ids_by_type.items():
        model_class = ContentType.objects.get_for_id(content_type_id).model_class()
        if model_class is None:
            continue
        queryset = (
            base_manager(model_class)
            .filter(pk__in=ids)
            .select_related("brand", "model", "variant")
        )
        for firmware in queryset:
            resolved[(content_type_id, firmware.pk)] = firmware
    return resolved


def _entry_for(row: Any, firmware: Any, status: Any, renew_url: str) -> LibraryEntry:
    """One library entry from a served row, its firmware record and its status."""
    from apps.firmwares.catalog import FIRMWARE_TYPE_DISPLAY, TYPE_KEY_BY_CLASS

    type_key = TYPE_KEY_BY_CLASS.get(type(firmware)) if firmware else None
    links = None
    if type_key:
        kwargs = {"firmware_type": type_key, "firmware_id": row.object_id}
        links = (
            reverse("firmwares:firmware_detail", kwargs=kwargs),
            reverse("firmwares:firmware_download", kwargs=kwargs),
        )
    return LibraryEntry(
        name=firmware.original_file_name if firmware else "Removed file",
        brand=firmware.brand.name if firmware and firmware.brand else "",
        brand_slug=firmware.brand.slug if firmware and firmware.brand else "",
        device=str(firmware.model.name) if firmware and firmware.model else "",
        variant=str(firmware.variant.name) if firmware and firmware.variant else "",
        type_label=FIRMWARE_TYPE_DISPLAY.get(type_key or "", ""),
        tier=row.tier,
        tier_label=_tier_label(row.tier, row.charged_quota),
        paid_amount=row.paid_amount,
        counted_in_quota=row.charged_quota,
        served_at=row.served_at,
        last_served_at=row.last_served_at,
        times_served=row.times_served,
        detail_url=links[0] if links else "",
        download_url=links[1] if links else "",
        available=bool(
            firmware and firmware.is_active and firmware.is_verified and links
        ),
        state=ACTIVE if status.active else HISTORY,
        state_code=status.code,
        state_message=status.message,
        renew_url=renew_url if status.code == "package_expired" else "",
    )


def library_entries(user: Any) -> list[LibraryEntry]:
    """Everything served to ``user``, newest first (empty without the firmware app)."""
    if not firmwares_available():
        return []
    from apps.firmwares.access import served_statuses

    served_model = django_apps.get_model("firmwares", "ServedDownload")
    rows = list(
        default_manager(served_model)
        .filter(user=user)
        .order_by("-served_at")[:MAX_LIBRARY_ROWS]
    )
    firmware_by_ref = _firmware_rows(rows)
    statuses = served_statuses(user, rows)
    renew_url = reverse("shop:pricing") if shop_available() else ""
    return [
        _entry_for(
            row,
            firmware_by_ref.get((row.content_type_id, row.object_id)),
            statuses[row.pk],
            renew_url,
        )
        for row in rows
    ]


def entries_for_tab(entries: list[LibraryEntry], tab: str) -> list[LibraryEntry]:
    """The entries of one library tab: Active (fetchable now) or History."""
    wanted = HISTORY if tab == HISTORY else ACTIVE
    return [entry for entry in entries if entry.state == wanted]


def filter_library(
    entries: list[LibraryEntry], filters: LibraryFilters
) -> list[LibraryEntry]:
    """Apply the Library tab filters (tier, brand, served-date range, search)."""
    return search_library(entries, filters).items


def search_library(
    entries: list[LibraryEntry], filters: LibraryFilters
) -> ItemSearch[LibraryEntry]:
    """Library filters plus the shared text search (words, synonyms, typos)."""
    start = (
        timezone.make_aware(datetime.combine(filters.date_from, time.min))
        if filters.date_from
        else None
    )
    end = (
        timezone.make_aware(datetime.combine(filters.date_to, time.max))
        if filters.date_to
        else None
    )
    narrowed = [
        entry
        for entry in entries
        if (not filters.tier or entry.tier == filters.tier)
        and (not filters.brand or entry.brand_slug == filters.brand)
        and (start is None or entry.served_at >= start)
        and (end is None or entry.served_at <= end)
    ]
    return search_items(narrowed, filters.query, lambda entry: entry.search_fields)


def brand_choices(entries: list[LibraryEntry]) -> list[tuple[str, str]]:
    """(slug, name) of the brands present in the user's library."""
    brands = {entry.brand_slug: entry.brand for entry in entries if entry.brand_slug}
    return sorted(brands.items(), key=lambda item: item[1].lower())


@dataclass(frozen=True)
class UploadEntry:
    """One of the user's firmware submissions."""

    name: str
    status: str
    status_label: str
    tier_label: str
    price_note: str
    brand: str
    device: str
    variant: str
    submitted_at: datetime
    reviewed_at: datetime | None
    reviewer_note: str
    published_url: str


def _published_url(pending: Any) -> str:
    if pending.admin_decision != "approved" or not pending.routed_object_id:
        return ""
    from apps.firmwares.catalog import TYPE_KEY_BY_CLASS, get_firmware_model

    model_class = get_firmware_model(pending.routed_category)
    if (
        model_class is None
        or not model_class.objects.filter(
            pk=pending.routed_object_id, is_active=True, is_verified=True
        ).exists()
    ):
        return ""
    return reverse(
        "firmwares:firmware_detail",
        kwargs={
            "firmware_type": TYPE_KEY_BY_CLASS[model_class],
            "firmware_id": pending.routed_object_id,
        },
    )


def _price_note(pending: Any) -> str:
    if pending.access_tier != "paid":
        return ""
    if pending.approved_price is not None:
        return f"Approved price USD {pending.approved_price:.2f}"
    if pending.admin_decision == "rejected":
        return "No price approved"
    proposed = (
        f" (you proposed USD {pending.proposed_price:.2f})"
        if pending.proposed_price is not None
        else ""
    )
    return f"Price awaiting staff review{proposed}"


def upload_entries(user: Any, status: str = "") -> list[UploadEntry]:
    """The user's own submissions, newest first, optionally of one review status."""
    if not firmwares_available():
        return []
    pending_model = django_apps.get_model("firmwares", "PendingFirmware")
    queryset = (
        default_manager(pending_model)
        .filter(uploader=user)
        .select_related("uploaded_brand", "uploaded_model", "uploaded_variant")
    )
    if status in dict(UPLOAD_STATUSES):
        queryset = queryset.filter(admin_decision=status)
    labels = dict(UPLOAD_STATUSES)
    entries: list[UploadEntry] = []
    for pending in queryset.order_by("-created_at")[:MAX_LIBRARY_ROWS]:
        entries.append(
            UploadEntry(
                name=pending.original_file_name,
                status=pending.admin_decision,
                status_label=labels.get(pending.admin_decision, pending.admin_decision),
                tier_label=pending.get_access_tier_display(),
                price_note=_price_note(pending),
                brand=pending.uploaded_brand.name if pending.uploaded_brand else "",
                device=pending.uploaded_model.name if pending.uploaded_model else "",
                variant=(
                    pending.uploaded_variant.name if pending.uploaded_variant else ""
                ),
                submitted_at=pending.created_at,
                reviewed_at=pending.reviewed_at,
                reviewer_note=(
                    pending.admin_notes if pending.admin_decision == "rejected" else ""
                ),
                published_url=_published_url(pending),
            )
        )
    return entries


@dataclass(frozen=True)
class QuotaPanel:
    """Everything the quota panel shows; ``available`` is false without the shop."""

    available: bool
    windows: list[Any]
    headline: Any | None
    sources: tuple[str, ...]
    purchased: list[Any]
    plans_url: str
    orders_url: str
    plan_summary: str = ""
    devices_text: str = ""
    offers: tuple[Any, ...] = ()


def _offers(user: Any) -> tuple[Any, ...]:
    """Running offers for this user; empty when the promotions app is off."""
    api = AppService.get("promotions")
    return tuple(api.offers_for_user(user)) if api is not None else ()


def quota_panel(user: Any) -> QuotaPanel:
    if not shop_available():
        return QuotaPanel(False, [], None, (), [], "", "")
    from apps.shop import entitlements

    entitled = entitlements.effective_entitlements(user)
    return QuotaPanel(
        available=True,
        windows=entitlements.quota_windows(user),
        headline=entitlements.quota_snapshot(user),
        sources=entitled.sources,
        purchased=entitlements.purchased_unserved(user),
        plans_url=reverse("shop:pricing"),
        orders_url=reverse("shop:order_history"),
        plan_summary=entitled.plan_summary,
        devices_text=entitled.devices_text,
        offers=_offers(user),
    )

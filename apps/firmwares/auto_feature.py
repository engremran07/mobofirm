"""Automatic promotion of very popular free files to featured.

Configured in the admin (:class:`AutoFeatureSettings`): on/off, dry-run, the counting
window, the minimum downloads, unique downloaders and recent velocity, a cooldown and
a cap per run. It reads the real download analytics (completed
:class:`DownloadToken` rows), records every promotion as an access change by the
``system`` (reversible from the admin) and tells the uploader.

Guards: only free files are ever touched (paid files are never moved), a file that a
person once took out of featured is never promoted again, a file whose access changed
within the cooldown or that has an open change request is left to staff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q
from django.utils import timezone

from .access_change_models import (
    AccessChangeLog,
    AccessChangeRequest,
    AccessChangeSource,
    AutoFeatureSettings,
)
from .access_changes import (
    AccessChange,
    AccessChangeError,
    apply_access_change,
    manually_demoted,
)
from .catalog import PUBLIC_FIRMWARE_TYPES
from .models import AccessTier, BaseFirmware, DownloadToken


@dataclass(frozen=True)
class Candidate:
    """A free file with its popularity figures over the configured window."""

    firmware: BaseFirmware
    downloads: int
    unique_downloaders: int
    velocity: Decimal

    def summary(self) -> str:
        """One-line audit reason."""
        return (
            f"Popular: {self.downloads} downloads by {self.unique_downloaders} people, "
            f"{self.velocity}/day recently."
        )

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly form for the admin report."""
        return {
            "id": str(self.firmware.pk),
            "file": str(self.firmware.original_file_name),
            "downloads": self.downloads,
            "unique": self.unique_downloaders,
            "velocity": str(self.velocity),
        }


@dataclass
class AutoFeatureResult:
    """Outcome of one run."""

    dry_run: bool
    enabled: bool
    promoted: list[Candidate] = field(default_factory=list)
    would_promote: list[Candidate] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly form (task result and the admin's last-run report)."""
        return {
            "dry_run": self.dry_run,
            "enabled": self.enabled,
            "promoted": [item.as_dict() for item in self.promoted],
            "would_promote": [item.as_dict() for item in self.would_promote],
            "skipped": self.skipped,
        }


def _popular_ids(
    content_type: ContentType, config: AutoFeatureSettings, now: Any
) -> dict[Any, tuple[int, int, int]]:
    """``{file id: (downloads, unique people, recent downloads)}`` over the window."""
    since = now - timedelta(days=config.window_days)
    recent_since = now - timedelta(days=config.velocity_days)
    rows = (
        DownloadToken.objects.filter(
            content_type=content_type,
            status=DownloadToken.Status.USED,
            used_at__gte=since,
        )
        .order_by()
        .values("object_id")
        .annotate(
            downloads=Count("pk"),
            members=Count("user", distinct=True),
            visitors=Count("ip", filter=Q(user__isnull=True), distinct=True),
            recent=Count("pk", filter=Q(used_at__gte=recent_since)),
        )
        .filter(downloads__gte=config.min_downloads)
    )
    return {
        row["object_id"]: (
            row["downloads"],
            row["members"] + row["visitors"],
            row["recent"],
        )
        for row in rows
    }


def _blocked_reason(
    firmware: BaseFirmware, content_type: ContentType, config: AutoFeatureSettings
) -> str:
    """Why a file must not be promoted now (empty = it may be)."""
    history = AccessChangeLog.objects.filter(
        content_type=content_type, object_id=firmware.pk
    )
    if manually_demoted(firmware):
        return "demoted_by_staff"
    cooldown = timezone.now() - timedelta(days=config.cooldown_days)
    if history.filter(created_at__gte=cooldown).exists():
        return "cooldown"
    if AccessChangeRequest.objects.filter(
        content_type=content_type,
        object_id=firmware.pk,
        status=AccessChangeRequest.Status.PENDING,
    ).exists():
        return "open_request"
    return ""


def find_candidates(
    config: AutoFeatureSettings,
) -> tuple[list[Candidate], dict[str, int]]:
    """Free files that meet every threshold, most downloaded first, and skip counts."""
    now = timezone.now()
    skipped: dict[str, int] = {}
    found: list[Candidate] = []
    for _key, model_class in PUBLIC_FIRMWARE_TYPES:
        content_type = ContentType.objects.get_for_model(
            model_class, for_concrete_model=False
        )
        stats = _popular_ids(content_type, config, now)
        files = model_class.objects.filter(
            pk__in=stats.keys(),
            access_tier=AccessTier.FREE,
            is_active=True,
            is_verified=True,
        )
        for firmware in files:
            downloads, unique, recent = stats[firmware.pk]
            velocity = (Decimal(recent) / config.velocity_days).quantize(
                Decimal("0.01")
            )
            if unique < config.min_unique_downloaders:
                reason = "too_few_people"
            elif velocity < config.min_velocity:
                reason = "too_slow"
            else:
                reason = _blocked_reason(firmware, content_type, config)
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            found.append(Candidate(firmware, downloads, unique, velocity))
    found.sort(key=lambda item: item.downloads, reverse=True)
    return found, skipped


def run_auto_feature(
    *, force_dry_run: bool = False, ignore_enabled: bool = False
) -> AutoFeatureResult:
    """Promote (or, in a dry run, list) the popular free files per the settings.

    ``force_dry_run`` and ``ignore_enabled`` serve the management command's
    ``--dry-run`` preview, which works even while the rule is switched off.
    """
    config = AutoFeatureSettings.get_solo()
    dry_run = force_dry_run or config.dry_run
    result = AutoFeatureResult(dry_run=dry_run, enabled=config.enabled)
    if not config.enabled and not ignore_enabled:
        result.skipped["disabled"] = 1
        return result
    candidates, result.skipped = find_candidates(config)
    chosen = candidates[: config.max_promotions_per_run]
    if len(candidates) > len(chosen):
        result.skipped["over_run_limit"] = len(candidates) - len(chosen)
    for candidate in chosen:
        if dry_run:
            result.would_promote.append(candidate)
            continue
        try:
            apply_access_change(
                candidate.firmware,
                AccessChange(
                    to_tier=AccessTier.FEATURED,
                    source=AccessChangeSource.AUTO,
                    reason=candidate.summary(),
                    notify=config.notify_uploader,
                ),
            )
        except AccessChangeError:
            result.skipped["refused"] = result.skipped.get("refused", 0) + 1
            continue
        result.promoted.append(candidate)
    config.last_run_at = timezone.now()
    config.last_run_report = result.as_dict()
    config.save(update_fields=["last_run_at", "last_run_report"])
    return result

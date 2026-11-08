"""Flash-guide coverage: one 'how to flash' guide per model and one per chipset.

The automated firmware posts (``firmwares.AutomatedBlogLink``) own the guide content;
this module audits their coverage, names what is missing (guide, tool, chipset link),
and asks the owning app to refresh them through its registered Celery task, run
synchronously here so no broker is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from celery import current_app
from django.apps import apps as django_apps
from django.db.models import Count, Q
from django.urls import reverse

from .models import Issue, PageSnapshot
from .object_checks import find_guide_post

SYNC_TASK = "apps.firmwares.tasks.sync_automated_blogs"
MAX_GENERATE = 25
STATUS_LABELS = {
    "ok": "Covered",
    "missing_guide": "No guide",
    "no_tool": "No tool",
    "no_chipset": "No chipset",
}


@dataclass
class GuideRow:
    kind: str
    pk: int
    label: str
    url: str
    public: bool
    guide_url: str
    guide_title: str
    tools: list[str]
    lists_firmware: bool
    status: str
    open_issues: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def needs_work(self) -> bool:
        return self.status != "ok"


def _model(label: str) -> Any | None:
    try:
        return django_apps.get_model(label)
    except LookupError:
        return None


def _status(has_guide: bool, tools: list[str], has_chipset: bool) -> str:
    if not has_guide:
        return "missing_guide"
    if not has_chipset:
        return "no_chipset"
    if not tools:
        return "no_tool"
    return "ok"


def _issue_counts(urls: list[str]) -> dict[str, int]:
    return dict(
        Issue.objects.filter(status=Issue.Status.OPEN, url__in=urls)
        .values_list("url")
        .annotate(total=Count("pk"))
    )


def model_rows(*, only_missing: bool = False) -> list[GuideRow]:
    model_class = _model("firmwares.Model")
    tool_class = _model("firmwares.FlashingTool")
    if model_class is None or tool_class is None:
        return []
    indexable = dict(
        PageSnapshot.objects.filter(object_label="firmwares.model").values_list(
            "object_pk", "indexable"
        )
    )
    rows: list[GuideRow] = []
    models = model_class.objects.filter(is_active=True).select_related(
        "brand", "chipset_ref"
    )
    for model in models.order_by("brand__name", "name"):
        public = bool(indexable.get(str(model.pk), False))
        guide = find_guide_post(model_pk=model.pk)
        tool_filter = Q(supported_brands=model.brand_id)
        if model.chipset_ref_id:
            tool_filter |= Q(chipsets=model.chipset_ref_id)
        tools = list(
            tool_class.objects.filter(tool_filter, is_active=True)
            .distinct()
            .values_list("name", flat=True)[:4]
        )
        row = GuideRow(
            kind="model",
            pk=model.pk,
            label=f"{model.brand.name} {model.name}",
            url=reverse(
                "firmwares:model_detail",
                kwargs={"brand_slug": model.brand.slug, "model_slug": model.slug},
            ),
            public=public,
            guide_url=guide.get_absolute_url() if guide else "",
            guide_title=guide.title if guide else "",
            tools=tools,
            lists_firmware=bool(guide and "firmware" in guide.body.lower()),
            status=_status(guide is not None, tools, bool(model.chipset_ref_id)),
        )
        if not only_missing or (row.needs_work and row.public):
            rows.append(row)
    counts = _issue_counts([row.url for row in rows])
    for row in rows:
        row.open_issues = counts.get(row.url, 0)
    return rows


def chipset_rows(*, only_missing: bool = False) -> list[GuideRow]:
    chipset_class = _model("firmwares.Chipset")
    if chipset_class is None:
        return []
    rows: list[GuideRow] = []
    chipsets = chipset_class.objects.annotate(
        device_total=Count("models", filter=Q(models__is_active=True), distinct=True)
    ).filter(device_total__gt=0)
    for chipset in chipsets.order_by("vendor", "name"):
        guide = find_guide_post(chipset_pk=chipset.pk)
        tools = list(
            chipset.flashing_tools.filter(is_active=True).values_list(
                "name", flat=True
            )[:4]
        )
        row = GuideRow(
            kind="chipset",
            pk=chipset.pk,
            label=chipset.display_name,
            url=reverse("firmwares:chipset_detail", kwargs={"slug": chipset.slug}),
            public=True,
            guide_url=guide.get_absolute_url() if guide else "",
            guide_title=guide.title if guide else "",
            tools=tools,
            lists_firmware=bool(guide and "firmware" in guide.body.lower()),
            status=_status(guide is not None, tools, True),
        )
        if not only_missing or row.needs_work:
            rows.append(row)
    counts = _issue_counts([row.url for row in rows])
    for row in rows:
        row.open_issues = counts.get(row.url, 0)
    return rows


def summary() -> dict[str, int]:
    models = model_rows()
    chipsets = chipset_rows()
    return {
        "models": len([r for r in models if r.public]),
        "models_missing": len([r for r in models if r.public and r.needs_work]),
        "chipsets": len(chipsets),
        "chipsets_missing": len([r for r in chipsets if r.needs_work]),
    }


class GuideTaskUnavailable(Exception):
    """The owning app's guide task is not installed."""


def generate(
    *, model_ids: list[int] | None = None, chipset_ids: list[int] | None = None
) -> int:
    """Refresh the guide posts through the owning app's task, synchronously."""
    # Tasks register lazily; make sure the owning app's task module is imported.
    current_app.loader.import_default_modules()
    task = current_app.tasks.get(SYNC_TASK)
    if task is None:
        current_app.autodiscover_tasks(force=True)
        task = current_app.tasks.get(SYNC_TASK)
    if task is None:
        raise GuideTaskUnavailable("The firmware blog automation is not installed.")
    result = task.apply(
        kwargs={
            "model_ids": list(model_ids or []),
            "chipset_ids": list(chipset_ids or []),
        }
    )
    outcome = result.get()
    return int(outcome.get("synced", 0)) if isinstance(outcome, dict) else 0

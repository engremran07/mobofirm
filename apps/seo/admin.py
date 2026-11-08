"""SEO Control Center administration (Unfold).

Manual edits of redirects, metadata and settings are recorded in the change ledger
so they can be rolled back. Ledger decisions (approve/reject/rollback), issue
triage, job retries and crawls call the same services as the SEO dashboard and keep
their dedicated permissions (``seo.approve_changeledger`` and friends).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.db import models
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
)
from unfold.forms import BaseDialogForm

from apps.core.admin_base import (
    BADGE_JOB,
    ModelAdmin,
    NoAddModelAdmin,
    SingletonModelAdmin,
    action,
    change_url,
    changelist_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)

from . import ledger
from .crawler import CrawlUnavailable, run_crawl
from .jobs import process_pending
from .managers import default_manager
from .models import (
    ChangeLedger,
    CrawlRun,
    InterlinkExclusion,
    Issue,
    LinkableEntity,
    LinkSuggestion,
    Metadata,
    PageObservation,
    PageSnapshot,
    Redirect,
    SeoEntity,
    SeoEntityEdge,
    SEOJob,
    SeoKeywordHistory,
    SEOModel,
    SEOSettings,
    SitemapEntry,
)

BADGE_SEO: Mapping[str, str] = {
    **BADGE_JOB,
    "proposed": "warning",
    "approved": "info",
    "rejected": "danger",
    "applied": "success",
    "rolled_back": "info",
    "error": "danger",
    "warning": "warning",
    "info": "info",
    "open": "warning",
    "resolved": "success",
    "ignored": "info",
}

LedgerOperation = Callable[[ChangeLedger, Any], ChangeLedger]


class LedgerRecordingAdmin(ModelAdmin):
    """Records every admin edit in the change ledger so it can be rolled back."""

    def save_model(
        self, request: HttpRequest, obj: models.Model, form: Any, change: bool
    ) -> None:
        before = {}
        if change:
            stored = default_manager(type(obj)).filter(pk=obj.pk).first()
            before = ledger.serialize(stored) if stored else {}
        super().save_model(request, obj, form, change)
        action_kind = (
            ChangeLedger.Action.UPDATE if change else ChangeLedger.Action.CREATE
        )
        after = ledger.serialize(obj)
        if action_kind == ChangeLedger.Action.CREATE:
            after.pop("id", None)
        ledger.record_manual(
            user=request.user,
            action=action_kind,
            instance=obj,
            before=before,
            after=after,
        )

    def delete_model(self, request: HttpRequest, obj: models.Model) -> None:
        before = ledger.serialize(obj)
        pk = obj.pk
        super().delete_model(request, obj)
        obj.pk = pk
        ledger.record_manual(
            user=request.user,
            action=ChangeLedger.Action.DELETE,
            instance=obj,
            before=before,
            after={},
        )


class SeoReadOnlyAdmin(NoAddModelAdmin):
    """Crawler and automation output: browsable, never edited by hand."""

    def has_change_permission(
        self, request: HttpRequest, obj: models.Model | None = None
    ) -> bool:
        return False


@register(SEOModel)
class SEOModelAdmin(ModelAdmin):
    list_display = ("content_type", "object_id", "locked", "ai_generated", "updated_at")
    list_filter = ("locked", "ai_generated", ("updated_at", RangeDateTimeFilter))
    search_fields = ("object_id",)


@register(Metadata)
class MetadataAdmin(LedgerRecordingAdmin):
    list_display = ("seo", "meta_title", "noindex", "nofollow", "updated_at")
    list_filter = ("noindex", "nofollow", ("updated_at", RangeDateTimeFilter))
    search_fields = ("meta_title", "meta_description")
    raw_id_fields = ("seo",)


@register(SitemapEntry)
class SitemapEntryAdmin(ModelAdmin):
    list_display = ("url", "is_active", "priority", "lastmod", "last_status")
    list_filter = (
        "is_active",
        ("changefreq", ChoicesDropdownFilter),
        ("lastmod", RangeDateTimeFilter),
    )
    search_fields = ("url",)
    readonly_fields = ("last_status", "last_checked_at")


@register(Redirect)
class RedirectAdmin(LedgerRecordingAdmin):
    list_display = ("source", "target", "is_permanent", "is_active", "created_at")
    list_filter = ("is_permanent", "is_active", ("created_at", RangeDateTimeFilter))
    search_fields = ("source", "target")


@register(LinkableEntity)
class LinkableEntityAdmin(ModelAdmin):
    list_display = ("title", "entity_type", "url", "is_active", "updated_at")
    list_filter = (("entity_type", ChoicesDropdownFilter), "is_active")
    search_fields = ("title", "url")
    exclude = ("embedding", "vector")


@register(LinkSuggestion)
class LinkSuggestionAdmin(ModelAdmin):
    list_display = (
        "source",
        "target",
        "relation",
        "status",
        "score",
        "is_applied",
        "is_active",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("relation", ChoicesDropdownFilter),
        "is_applied",
        "locked",
        "is_active",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("source__title", "target__title", "anchor")
    raw_id_fields = ("source", "target", "decided_by")


@register(SEOSettings)
class SEOSettingsAdmin(LedgerRecordingAdmin, SingletonModelAdmin):
    list_display = (
        "seo_enabled",
        "sitemap_enabled",
        "autonomy_mode",
        "indexnow_enabled",
    )
    fieldsets = (
        (
            _("Automation"),
            {
                "fields": ("seo_enabled", "sitemap_enabled", "autonomy_mode"),
                "classes": ["tab"],
            },
        ),
        (
            _("Metadata"),
            {
                "fields": (
                    "site_name",
                    "title_separator",
                    "default_description",
                    "default_social_image",
                    "organization_logo",
                    "twitter_site",
                    "auto_meta_enabled",
                    "auto_schema_enabled",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Thin-page policy"),
            {
                "fields": (
                    "min_firmware_for_model_index",
                    "min_models_for_brand_index",
                    "min_models_for_chipset_index",
                    "min_posts_for_tag_index",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Crawler policy"),
            {
                "fields": (
                    "allow_ai_search_bots",
                    "allow_ai_user_agents",
                    "allow_ai_training_bots",
                    "crawl_max_pages",
                    "crawl_delay_ms",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Integrations"),
            {
                "fields": (
                    "indexnow_enabled",
                    "indexnow_key",
                    "search_console_enabled",
                    "pagespeed_enabled",
                    "auto_linking_enabled",
                ),
                "classes": ["tab"],
            },
        ),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


@register(SeoKeywordHistory)
class SeoKeywordHistoryAdmin(ModelAdmin):
    list_display = ("keyword", "target_url", "position", "intent", "created_at")
    list_filter = (
        ("intent", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("keyword", "target_url")


@register(SeoEntity)
class SeoEntityAdmin(ModelAdmin):
    list_display = ("name", "slug", "entity_type", "is_active", "updated_at")
    list_filter = (("entity_type", ChoicesDropdownFilter), "is_active")
    search_fields = ("name", "slug")


@register(SeoEntityEdge)
class SeoEntityEdgeAdmin(ModelAdmin):
    list_display = ("source", "target", "relation_type", "weight", "created_at")
    list_filter = (("relation_type", ChoicesDropdownFilter),)
    search_fields = ("source__name", "target__name")
    raw_id_fields = ("source", "target")


@register(InterlinkExclusion)
class InterlinkExclusionAdmin(ModelAdmin):
    list_display = ("phrase", "source_pattern", "target_pattern", "is_active")
    list_filter = ("is_active",)
    search_fields = ("phrase", "source_pattern", "target_pattern")


# ---------------------------------------------------------------------------
# Crawl output, issues and jobs
# ---------------------------------------------------------------------------


@register(CrawlRun)
class CrawlRunAdmin(SeoReadOnlyAdmin):
    list_display = (
        "pk",
        "kind",
        "status_badge",
        "trigger",
        "pages_crawled",
        "pages_with_errors",
        "partial",
        "started_at",
        "finished_at",
    )
    list_filter = (
        ("kind", ChoicesDropdownFilter),
        ("status", ChoicesDropdownFilter),
        ("trigger", ChoicesDropdownFilter),
        ("started_at", RangeDateTimeFilter),
    )
    list_select_related = ("initiated_by",)
    actions_list = ("start_crawl",)

    def has_crawl_permission(
        self, request: HttpRequest, _obj: object | None = None
    ) -> bool:
        return request.user.has_perm("seo.run_crawl")

    @display(description=_("Status"), ordering="status", label=BADGE_SEO)
    def status_badge(self, obj: CrawlRun) -> str:
        return obj.status

    @action(
        description=_("Start crawl"),
        icon="travel_explore",
        permissions=["crawl"],
        url_path="start-crawl",
        dialog=confirm_dialog(
            _("Start a site crawl"),
            _(
                "Crawls the public site within the configured page and delay limits "
                "and audits what it finds."
            ),
            submit=_("Start crawl"),
        ),
    )
    def start_crawl(self, request: HttpRequest, _form: BaseDialogForm) -> HttpResponse:
        try:
            run = run_crawl(user=request.user)
        except CrawlUnavailable as reason:
            self.message_user(request, str(reason), messages.ERROR)
        else:
            self.message_user(
                request,
                _("Crawl %(id)s finished: %(pages)d pages, %(errors)d with errors.")
                % {
                    "id": run.pk,
                    "pages": run.pages_crawled,
                    "errors": run.pages_with_errors,
                },
            )
        return htmx_redirect(return_url(request, changelist_url(self)))


@register(PageSnapshot)
class PageSnapshotAdmin(SeoReadOnlyAdmin):
    list_display = (
        "path",
        "group",
        "page_type",
        "indexable",
        "in_sitemap",
        "word_count",
        "links_in",
        "links_out",
        "scanned_at",
    )
    list_filter = (
        ("group", ChoicesDropdownFilter),
        "indexable",
        "in_sitemap",
        ("scanned_at", RangeDateTimeFilter),
    )
    search_fields = ("path", "title", "object_title")
    raw_id_fields = ("last_run",)


@register(PageObservation)
class PageObservationAdmin(SeoReadOnlyAdmin):
    list_display = ("url", "status_code", "run", "in_sitemap", "observed_at")
    list_filter = (
        "status_code",
        "in_sitemap",
        "blocked_by_robots",
        ("observed_at", RangeDateTimeFilter),
    )
    search_fields = ("url",)
    raw_id_fields = ("run",)


@register(Issue)
class IssueAdmin(SeoReadOnlyAdmin):
    list_display = (
        "code",
        "severity_badge",
        "status_badge",
        "group",
        "object_title",
        "fixable",
        "url",
        "updated_at",
    )
    list_filter = (
        ("severity", ChoicesDropdownFilter),
        ("status", ChoicesDropdownFilter),
        ("group", ChoicesDropdownFilter),
        ("code", ChoicesDropdownFilter),
        "fixable",
        ("updated_at", RangeDateTimeFilter),
    )
    search_fields = ("url", "message", "object_title")
    raw_id_fields = ("first_seen_run", "last_seen_run", "fix_ledger", "ignored_by")
    actions = ("mark_ignored", "mark_open")
    actions_row = ("ignore_issue", "reopen_issue")
    actions_detail = ("ignore_issue", "reopen_issue")

    def has_manage_permission(
        self, request: HttpRequest, _obj: object | None = None
    ) -> bool:
        return request.user.has_perm("seo.change_issue")

    @display(description=_("Severity"), ordering="severity", label=BADGE_SEO)
    def severity_badge(self, obj: Issue) -> str:
        return obj.severity

    @display(description=_("Status"), ordering="status", label=BADGE_SEO)
    def status_badge(self, obj: Issue) -> str:
        return obj.status

    @action(description="Ignore selected issues", permissions=["manage"])
    def mark_ignored(self, request: HttpRequest, queryset: models.QuerySet) -> None:
        count = queryset.update(status=Issue.Status.IGNORED)
        self.message_user(request, f"{count} issue(s) ignored.")

    @action(description="Re-open selected issues", permissions=["manage"])
    def mark_open(self, request: HttpRequest, queryset: models.QuerySet) -> None:
        count = queryset.update(status=Issue.Status.OPEN)
        self.message_user(request, f"{count} issue(s) re-opened.")

    def _set_status(
        self, request: HttpRequest, object_id: str, status: Issue.Status
    ) -> HttpResponse:
        issue = self.get_object(request, unquote(object_id))
        if issue is None:
            raise Http404
        Issue.objects.filter(pk=issue.pk).update(status=status)
        self.message_user(request, _("Issue marked %(status)s.") % {"status": status})
        return htmx_redirect(return_url(request, change_url(self, issue.pk)))

    @action(
        description=_("Ignore"),
        icon="visibility_off",
        permissions=["manage"],
        url_path="ignore-issue",
        dialog=confirm_dialog(
            _("Ignore this issue"),
            _("Ignored issues stop counting until they are re-opened."),
            submit=_("Ignore"),
        ),
    )
    def ignore_issue(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._set_status(request, object_id, Issue.Status.IGNORED)

    @action(
        description=_("Re-open"),
        icon="replay",
        permissions=["manage"],
        url_path="reopen-issue",
        dialog=confirm_dialog(
            _("Re-open this issue"),
            submit=_("Re-open"),
        ),
    )
    def reopen_issue(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._set_status(request, object_id, Issue.Status.OPEN)


@register(ChangeLedger)
class ChangeLedgerAdmin(SeoReadOnlyAdmin):
    list_display = (
        "created_at",
        "kind",
        "target_label",
        "status_badge",
        "requires_approval",
        "source",
        "mode",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("kind", ChoicesDropdownFilter),
        "requires_approval",
        ("mode", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("target_label", "reason", "source")
    list_select_related = ("decided_by",)
    raw_id_fields = ("decided_by",)
    actions = ("approve_selected", "reject_selected", "rollback_selected")
    actions_row = ("approve_change", "reject_change", "rollback_change")
    actions_detail = ("approve_change", "reject_change", "rollback_change")
    fieldsets = (
        (
            _("Change"),
            {
                "fields": (
                    "kind",
                    "action",
                    "target_model",
                    "target_pk",
                    "target_label",
                    "reason",
                    "source",
                    "mode",
                    "requires_approval",
                    "idempotency_key",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Before and after"),
            {
                "fields": ("before", "after", "validation", "error"),
                "classes": ["tab"],
            },
        ),
        (
            _("Decision"),
            {
                "fields": (
                    "status",
                    "decided_by",
                    "decided_at",
                    "applied_at",
                    "verified_at",
                    "rolled_back_at",
                ),
                "classes": ["tab"],
            },
        ),
    )

    def has_approve_permission(
        self, request: HttpRequest, _obj: object | None = None
    ) -> bool:
        return request.user.has_perm("seo.approve_changeledger")

    def has_rollback_permission(
        self, request: HttpRequest, _obj: object | None = None
    ) -> bool:
        return request.user.has_perm("seo.rollback_changeledger")

    @display(description=_("Status"), ordering="status", label=BADGE_SEO)
    def status_badge(self, obj: ChangeLedger) -> str:
        return obj.status

    def _run(
        self,
        request: HttpRequest,
        queryset: models.QuerySet[ChangeLedger],
        operation: LedgerOperation,
    ) -> None:
        done = 0
        for entry in queryset:
            try:
                operation(entry, request.user)
            except ledger.LedgerError as error:
                self.message_user(request, f"{entry}: {error}", messages.ERROR)
            else:
                done += 1
        self.message_user(request, f"{done} change(s) processed.")

    def _run_one(
        self, request: HttpRequest, object_id: str, operation: LedgerOperation
    ) -> HttpResponse:
        entry = self.get_object(request, unquote(object_id))
        if entry is None:
            raise Http404
        self._run(request, ChangeLedger.objects.filter(pk=entry.pk), operation)
        return htmx_redirect(return_url(request, change_url(self, entry.pk)))

    @action(description="Approve and apply selected proposals", permissions=["approve"])
    def approve_selected(self, request: HttpRequest, queryset: models.QuerySet) -> None:
        self._run(request, queryset, ledger.approve)

    @action(description="Reject selected proposals", permissions=["approve"])
    def reject_selected(self, request: HttpRequest, queryset: models.QuerySet) -> None:
        self._run(request, queryset, ledger.reject)

    @action(description="Roll back selected applied changes", permissions=["rollback"])
    def rollback_selected(
        self, request: HttpRequest, queryset: models.QuerySet
    ) -> None:
        self._run(request, queryset, ledger.rollback)

    @action(
        description=_("Approve and apply"),
        icon="check_circle",
        permissions=["approve"],
        url_path="approve-change",
        dialog=confirm_dialog(
            _("Approve this proposal"),
            _("Applies the change, verifies it and records the decision."),
            submit=_("Approve and apply"),
        ),
    )
    def approve_change(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._run_one(request, object_id, ledger.approve)

    @action(
        description=_("Reject"),
        icon="block",
        permissions=["approve"],
        url_path="reject-change",
        dialog=confirm_dialog(
            _("Reject this proposal"),
            _("The proposal is closed without applying anything."),
            submit=_("Reject"),
        ),
    )
    def reject_change(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._run_one(request, object_id, ledger.reject)

    @action(
        description=_("Roll back"),
        icon="undo",
        permissions=["rollback"],
        url_path="rollback-change",
        dialog=confirm_dialog(
            _("Roll back this change"),
            _("Restores the values recorded before the change was applied."),
            submit=_("Roll back"),
        ),
    )
    def rollback_change(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._run_one(request, object_id, ledger.rollback)


@register(SEOJob)
class SEOJobAdmin(SeoReadOnlyAdmin):
    list_display = (
        "kind",
        "job_key",
        "status_badge",
        "attempts",
        "available_at",
        "finished_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("kind", ChoicesDropdownFilter),
        ("available_at", RangeDateTimeFilter),
    )
    search_fields = ("job_key", "last_error")
    actions = ("retry_failed",)
    actions_row = ("retry_job",)
    actions_detail = ("retry_job",)
    actions_list = ("process_jobs",)

    def has_manage_permission(
        self, request: HttpRequest, _obj: object | None = None
    ) -> bool:
        return request.user.has_perm("seo.change_seojob")

    @display(description=_("Status"), ordering="status", label=BADGE_SEO)
    def status_badge(self, obj: SEOJob) -> str:
        return obj.status

    @action(description="Retry selected failed jobs", permissions=["manage"])
    def retry_failed(self, request: HttpRequest, queryset: models.QuerySet) -> None:
        count = queryset.filter(status=SEOJob.Status.FAILED).update(
            status=SEOJob.Status.PENDING, attempts=0, last_error=""
        )
        self.message_user(request, f"{count} job(s) queued for retry.")

    @action(
        description=_("Retry"),
        icon="replay",
        permissions=["manage"],
        url_path="retry-job",
        dialog=confirm_dialog(
            _("Retry this job"),
            _("Only failed jobs are re-queued; their attempt counter is reset."),
            submit=_("Retry"),
        ),
    )
    def retry_job(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        job = self.get_object(request, unquote(object_id))
        if job is None:
            raise Http404
        count = SEOJob.objects.filter(pk=job.pk, status=SEOJob.Status.FAILED).update(
            status=SEOJob.Status.PENDING, attempts=0, last_error=""
        )
        if count:
            self.message_user(request, _("Job queued for retry."))
        else:
            self.message_user(
                request, _("Only failed jobs can be retried."), messages.ERROR
            )
        return htmx_redirect(return_url(request, change_url(self, job.pk)))

    @action(
        description=_("Process pending jobs"),
        icon="play_circle",
        permissions=["manage"],
        url_path="process-jobs",
        dialog=confirm_dialog(
            _("Process pending jobs"),
            _("Runs every job that is due now."),
            submit=_("Process"),
        ),
    )
    def process_jobs(self, request: HttpRequest, _form: BaseDialogForm) -> HttpResponse:
        counts = process_pending()
        self.message_user(
            request,
            "Processed: "
            + ", ".join(f"{key}={value}" for key, value in counts.items()),
        )
        return htmx_redirect(return_url(request, changelist_url(self)))

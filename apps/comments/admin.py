"""Comment moderation admin: queue, row/detail decisions and automatic-moderation settings."""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
)
from unfold.enums import ActionVariant
from unfold.widgets import UnfoldAdminTextareaWidget

from apps.core.admin_base import (
    BADGE_APPROVAL,
    ConfirmForm,
    ModelAdmin,
    SingletonModelAdmin,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)

from .models import Comment, CommentSettings
from .services.decisions import MAX_NOTE_LENGTH, moderate_comment


class DecisionNoteForm(ConfirmForm):
    """Moderation dialog with an optional note kept on the comment's audit entry."""

    note = forms.CharField(
        label=_("Note"),
        required=False,
        max_length=MAX_NOTE_LENGTH,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
        help_text=_("Optional. Stored with the decision."),
    )


@admin.register(Comment)
class CommentAdmin(ModelAdmin):
    list_display = (
        "excerpt",
        "post",
        "user",
        "status_badge",
        "toxicity_score",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("toxicity_score", RangeNumericFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("body", "user__email", "post__title")
    raw_id_fields = ("post", "user", "parent", "deleted_by")
    readonly_fields = (
        "status",
        "is_approved",
        "moderation_flags",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("body", "post", "user", "parent")}),
        (
            _("Moderation"),
            {
                "classes": ("tab",),
                "fields": (
                    "status",
                    "is_approved",
                    "toxicity_score",
                    "score",
                    "moderation_flags",
                ),
            },
        ),
        (
            _("Technical"),
            {
                "classes": ("tab",),
                "fields": (
                    "content_type",
                    "object_id",
                    "metadata",
                    "edited_at",
                    "created_at",
                    "updated_at",
                    "is_deleted",
                    "deleted_at",
                    "deleted_by",
                ),
            },
        ),
    )
    list_select_related = ("post", "user")
    actions = (
        "approve_selected",
        "reject_selected",
        "mark_selected_spam",
        "soft_delete",
    )
    actions_row = ("approve", "reject", "spam")
    actions_detail = ("approve", "reject", "spam")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Comment]:
        return super().get_queryset(request).filter(is_deleted=False)

    @display(description=_("Comment"))
    def excerpt(self, obj: Comment) -> str:
        body = " ".join(obj.body.split())
        return body if len(body) <= 80 else f"{body[:79]}…"

    @display(description=_("Status"), ordering="status", label=BADGE_APPROVAL)
    def status_badge(self, obj: Comment) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    # -- bulk actions ---------------------------------------------------------
    def _decide(
        self,
        request: HttpRequest,
        queryset: QuerySet[Comment],
        decision: str,
        label: str,
    ) -> None:
        done = 0
        for comment in queryset:
            try:
                moderate_comment(comment, decision, request.user)
            except (PermissionDenied, ValidationError):
                continue
            done += 1
        self.message_user(request, f"{label} {done} comments.")

    @action(description=_("Approve selected comments"))
    def approve_selected(
        self, request: HttpRequest, queryset: QuerySet[Comment]
    ) -> None:
        self._decide(request, queryset, "approve", "Approved")

    @action(description=_("Reject selected comments"))
    def reject_selected(
        self, request: HttpRequest, queryset: QuerySet[Comment]
    ) -> None:
        self._decide(request, queryset, "reject", "Rejected")

    @action(description=_("Mark selected comments as spam"))
    def mark_selected_spam(
        self, request: HttpRequest, queryset: QuerySet[Comment]
    ) -> None:
        self._decide(request, queryset, "spam", "Marked as spam:")

    @action(description=_("Soft delete selected comments"))
    def soft_delete(self, request: HttpRequest, queryset: QuerySet[Comment]) -> None:
        queryset.update(
            is_deleted=True, deleted_at=timezone.now(), deleted_by=request.user
        )

    # -- row / detail decisions: each link opens a dialog with an optional note -----
    def _moderate(
        self,
        request: HttpRequest,
        object_id: str,
        form: DecisionNoteForm,
        decision: str,
        success: str,
    ) -> HttpResponse:
        comment = self.get_object(request, object_id)
        if comment is None:
            raise Http404
        try:
            moderate_comment(
                comment, decision, request.user, form.cleaned_data.get("note", "")
            )
        except (PermissionDenied, ValidationError):
            self.message_user(
                request, _("You may not moderate this comment."), messages.ERROR
            )
        else:
            self.log_change(request, comment, success)
            self.message_user(request, success, messages.SUCCESS)
        return htmx_redirect(return_url(request, change_url(self, comment.pk)))

    @action(
        description=_("Approve"),
        icon="check_circle",
        variant=ActionVariant.SUCCESS,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Approve this comment?"),
            _("The comment becomes visible and its author is notified."),
            form_class=DecisionNoteForm,
            submit=_("Approve"),
        ),
    )
    def approve(
        self, request: HttpRequest, form: DecisionNoteForm, object_id: str
    ) -> HttpResponse:
        return self._moderate(request, object_id, form, "approve", str(_("Approved.")))

    @action(
        description=_("Reject"),
        icon="cancel",
        permissions=["change"],
        dialog=confirm_dialog(
            _("Reject this comment?"),
            form_class=DecisionNoteForm,
            submit=_("Reject"),
        ),
    )
    def reject(
        self, request: HttpRequest, form: DecisionNoteForm, object_id: str
    ) -> HttpResponse:
        return self._moderate(request, object_id, form, "reject", str(_("Rejected.")))

    @action(
        description=_("Mark as spam"),
        icon="report",
        variant=ActionVariant.DANGER,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Mark this comment as spam?"),
            form_class=DecisionNoteForm,
            submit=_("Mark as spam"),
        ),
    )
    def spam(
        self, request: HttpRequest, form: DecisionNoteForm, object_id: str
    ) -> HttpResponse:
        return self._moderate(
            request, object_id, form, "spam", str(_("Marked as spam."))
        )


@admin.register(CommentSettings)
class CommentSettingsAdmin(SingletonModelAdmin):
    list_display = ("enable_comments", "auto_approve_clean", "max_links")
    fieldsets = (
        (None, {"fields": ("enable_comments",)}),
        (
            _("Automatic moderation"),
            {"fields": ("auto_approve_clean", "max_links", "report_threshold")},
        ),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

"""Tag curation admin: approve/reject suggestions, merge duplicates, keyword sources."""

from __future__ import annotations

from functools import partial
from typing import ClassVar

from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    BooleanRadioFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.enums import ActionVariant
from unfold.widgets import UnfoldAdminTextInputWidget

from apps.core.admin_base import (
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

from .models import Tag, TagsSettings
from .models_keyword import KeywordProvider, KeywordSuggestion
from .services import automation

BADGE_TAG_STATE: dict[str, str] = {
    "curated": "success",
    "suggested": "warning",
    "rejected": "danger",
    "merged": "info",
}


class MergeTagForm(ConfirmForm):
    """Dialog choosing the tag that should absorb the tag being merged."""

    target = forms.CharField(
        label=_("Merge into"),
        max_length=80,
        widget=UnfoldAdminTextInputWidget,
        help_text=_("Slug or exact name of the tag to keep."),
    )

    def clean_target(self) -> Tag:
        value = str(self.cleaned_data["target"]).strip()
        candidates = Tag.objects.filter(is_deleted=False, merge_into__isnull=True)
        target = (
            candidates.filter(slug=value).first()
            or candidates.filter(name__iexact=value).first()
        )
        if target is None:
            raise forms.ValidationError(_("No active tag matches that slug or name."))
        if str(target.pk) == str(self.object_id):
            raise forms.ValidationError(_("A tag cannot be merged into itself."))
        return target


@admin.register(Tag)
class TagAdmin(ModelAdmin):
    actions = ("approve_selected", "reject_selected")
    actions_row = ("approve", "reject")
    actions_detail = ("approve", "reject", "merge")
    list_display = (
        "name",
        "slug",
        "state",
        "usage_count",
        "is_active",
        "ai_suggested",
        "merge_into",
    )
    list_filter = (
        ("is_active", BooleanRadioFilter),
        ("is_curated", BooleanRadioFilter),
        ("ai_suggested", BooleanRadioFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("name", "slug", "normalized_name", "synonyms_text")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    autocomplete_fields = ("parent", "merge_into")
    raw_id_fields = ("deleted_by",)
    readonly_fields = (
        "usage_count",
        "co_occurrence",
        "last_suggested_at",
        "is_curated",
    )
    fieldsets = (
        (None, {"fields": ("name", "normalized_name", "slug", "description")}),
        (
            _("Classification"),
            {
                "classes": ("tab",),
                "fields": ("is_active", "is_curated", "ai_suggested", "importance"),
            },
        ),
        (
            _("Synonyms"),
            {"classes": ("tab",), "fields": ("synonyms_text", "synonyms")},
        ),
        (
            _("Merge / moderation"),
            {
                "classes": ("tab",),
                "fields": (
                    "parent",
                    "path_cache",
                    "merge_into",
                    "deleted_at",
                    "deleted_by",
                ),
            },
        ),
        (
            _("AI meta"),
            {
                "classes": ("tab",),
                "fields": (
                    "ai_score",
                    "content_hash",
                    "last_suggested_at",
                    "suggestions",
                ),
            },
        ),
        (
            _("Analytics"),
            {"classes": ("tab",), "fields": ("usage_count", "co_occurrence")},
        ),
    )

    @display(description=_("State"), label=BADGE_TAG_STATE)
    def state(self, obj: Tag) -> tuple[str, str]:
        if obj.merge_into_id:
            return "merged", str(_("Merged"))
        if obj.is_curated:
            return "curated", str(_("Curated"))
        if obj.is_active:
            return "suggested", str(_("Suggested"))
        return "rejected", str(_("Rejected"))

    def save_model(
        self, request: HttpRequest, obj: Tag, form: ModelForm, change: bool
    ) -> None:
        if not change:
            obj.is_curated = False
        super().save_model(request, obj, form, change)

    # -- shared decisions -----------------------------------------------------------
    @staticmethod
    def _approve(queryset: QuerySet[Tag]) -> int:
        from .tasks import reattach_curated_tag

        approved = list(
            queryset.filter(is_deleted=False, merge_into__isnull=True).values_list(
                "pk", flat=True
            )
        )
        updated = queryset.filter(pk__in=approved).update(
            is_curated=True,
            is_active=True,
            ai_suggested=False,
            updated_at=timezone.now(),
        )
        for tag_id in approved:
            transaction.on_commit(partial(reattach_curated_tag.delay, tag_id))
        return updated

    @staticmethod
    def _reject(queryset: QuerySet[Tag]) -> int:
        return queryset.update(
            is_curated=False, is_active=False, updated_at=timezone.now()
        )

    # -- bulk actions ---------------------------------------------------------------
    @action(description=_("Approve selected tags"), permissions=["change"])
    def approve_selected(self, request: HttpRequest, queryset: QuerySet[Tag]) -> None:
        self.message_user(request, f"Approved {self._approve(queryset)} tags.")

    @action(description=_("Reject selected tags"), permissions=["change"])
    def reject_selected(self, request: HttpRequest, queryset: QuerySet[Tag]) -> None:
        self.message_user(request, f"Rejected {self._reject(queryset)} tags.")

    # -- row / detail actions: each link opens a confirmation dialog -------------------
    def _finish(
        self,
        request: HttpRequest,
        tag: Tag,
        message: str,
        level: int = messages.SUCCESS,
    ) -> HttpResponse:
        if level == messages.SUCCESS:
            self.log_change(request, tag, message)
        self.message_user(request, message, level)
        return htmx_redirect(return_url(request, change_url(self, tag.pk)))

    def _get_tag(self, request: HttpRequest, object_id: str) -> Tag:
        tag = self.get_object(request, object_id)
        if tag is None:
            raise Http404
        return tag

    @action(
        description=_("Approve"),
        icon="check_circle",
        variant=ActionVariant.SUCCESS,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Approve this tag?"),
            _("The tag becomes curated and is attached to matching posts."),
            submit=_("Approve"),
        ),
    )
    def approve(
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        tag = self._get_tag(request, object_id)
        if not self._approve(Tag.objects.filter(pk=tag.pk)):
            return self._finish(
                request,
                tag,
                str(_("Merged or deleted tags cannot be approved.")),
                messages.ERROR,
            )
        return self._finish(request, tag, str(_("Tag approved.")))

    @action(
        description=_("Reject"),
        icon="cancel",
        permissions=["change"],
        dialog=confirm_dialog(
            _("Reject this tag?"),
            _("The tag is deactivated and no longer offered or attached."),
            submit=_("Reject"),
        ),
    )
    def reject(
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        tag = self._get_tag(request, object_id)
        self._reject(Tag.objects.filter(pk=tag.pk))
        return self._finish(request, tag, str(_("Tag rejected.")))

    @action(
        description=_("Merge into another tag"),
        icon="merge",
        variant=ActionVariant.WARNING,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Merge this tag?"),
            _(
                "Every post using this tag moves to the tag you choose, and this tag "
                "is retired."
            ),
            form_class=MergeTagForm,
            submit=_("Merge"),
        ),
    )
    def merge(
        self, request: HttpRequest, form: MergeTagForm, object_id: str
    ) -> HttpResponse:
        source = self._get_tag(request, object_id)
        target: Tag = form.cleaned_data["target"]
        if source.is_deleted or source.merge_into_id:
            return self._finish(
                request, source, str(_("This tag was already merged.")), messages.ERROR
            )
        automation.merge_tags(source, target, user=request.user)
        message = _("Merged “%(source)s” into “%(target)s”.") % {
            "source": source.name,
            "target": target.name,
        }
        self.log_change(request, source, message)
        self.message_user(request, message, messages.SUCCESS)
        return htmx_redirect(return_url(request, change_url(self, target.pk)))


@admin.register(KeywordProvider)
class KeywordProviderAdmin(ModelAdmin):
    list_display = ("name", "source", "is_enabled", "last_run_at", "last_status")
    list_filter = (("is_enabled", BooleanRadioFilter), "source")
    search_fields = ("name", "source")
    readonly_fields = ("last_run_at", "last_status")


@admin.register(KeywordSuggestion)
class KeywordSuggestionAdmin(ModelAdmin):
    list_display = ("keyword", "provider", "score", "locale", "category", "created_at")
    search_fields = ("keyword", "normalized", "provider__name")
    list_filter = (("provider", RelatedDropdownFilter), "locale", "category")


@admin.register(TagsSettings)
class TagsSettingsAdmin(SingletonModelAdmin):
    list_display = (
        "allow_public_suggestions",
        "enable_ai_suggestions",
        "show_tag_usage",
    )
    fieldsets = (
        (
            _("Tag suggestions"),
            {"fields": ("allow_public_suggestions", "enable_ai_suggestions")},
        ),
        (_("Display"), {"fields": ("show_tag_usage",)}),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

"""Blog admin: posts with the editorial workflow, categories, queue and translations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.enums import ActionVariant
from unfold.widgets import UnfoldAdminSplitDateTimeWidget

from apps.core.admin_base import (
    BADGE_JOB,
    ConfirmForm,
    ModelAdmin,
    SingletonModelAdmin,
    TabularInline,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)

from .models import (
    AutoTopic,
    BlogSettings,
    Category,
    CategoryTranslation,
    Post,
    PostRevision,
    PostStatus,
    PostTranslation,
    TagTranslation,
)
from .services import workflow
from .services.history import PostHistoryService

BADGE_POST_STATUS: dict[str, str] = {
    PostStatus.DRAFT: "info",
    PostStatus.IN_REVIEW: "warning",
    PostStatus.SCHEDULED: "info",
    PostStatus.PUBLISHED: "success",
    PostStatus.ARCHIVED: "danger",
}
BADGE_SOURCE: dict[str, str] = {"automated": "info", "manual": "success"}


class ScheduleForm(ConfirmForm):
    """Dialog asking when a post should go live."""

    publish_at = forms.SplitDateTimeField(
        label=_("Publish at"),
        widget=UnfoldAdminSplitDateTimeWidget,
        help_text=_("The post is published automatically at this time."),
    )


class PostRevisionInline(TabularInline):
    """Read-only revision history shown on its own tab."""

    model = PostRevision
    tab = True
    fields = ("created_at", "user")
    readonly_fields = ("created_at", "user")
    can_delete = False
    ordering = ("-created_at",)
    verbose_name_plural = _("History")

    def has_add_permission(self, request: HttpRequest, obj: Post | None = None) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Post | None = None
    ) -> bool:
        return False


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ("name", "parent", "slug")
    search_fields = ("name", "slug")
    list_filter = (("parent", RelatedDropdownFilter),)
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}


@admin.register(Post)
class PostAdmin(ModelAdmin):
    list_display = (
        "title",
        "status_badge",
        "content_source",
        "category",
        "firmware_brand",
        "firmware_model",
        "author",
        "allow_comments",
        "noindex",
        "published_at",
        "updated_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("category", RelatedDropdownFilter),
        ("firmware_brand", RelatedDropdownFilter),
        ("is_ai_generated", BooleanRadioFilter),
        ("allow_comments", BooleanRadioFilter),
        ("noindex", BooleanRadioFilter),
        ("updated_at", RangeDateTimeFilter),
    )
    search_fields = (
        "title",
        "slug",
        "summary",
        "body",
        "firmware_brand__name",
        "firmware_model__name",
        "ai_run_id",
    )
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("title",)}
    autocomplete_fields = ("category", "tags")
    raw_id_fields = ("author", "firmware_brand", "firmware_model", "ad_gate_config")
    readonly_fields = (
        "status",
        "is_published",
        "published_at",
        "version",
        "views_count",
        "likes_count",
        "comments_count",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("title", "slug", "summary", "body")}),
        (
            _("Organisation"),
            {
                "classes": ("tab",),
                "fields": ("author", "category", "tags", "featured", "hero_image"),
            },
        ),
        (
            _("Publishing"),
            {
                "classes": ("tab",),
                "fields": (
                    "status",
                    "is_published",
                    "publish_at",
                    "published_at",
                    "version",
                    "allow_comments",
                    "reading_time",
                ),
            },
        ),
        (
            _("SEO"),
            {
                "classes": ("tab",),
                "fields": ("seo_title", "seo_description", "canonical_url", "noindex"),
            },
        ),
        (
            _("Firmware & automation"),
            {
                "classes": ("tab",),
                "fields": (
                    "firmware_brand",
                    "firmware_model",
                    "is_ai_generated",
                    "ai_run_id",
                    "ai_error",
                ),
            },
        ),
        (
            _("Ads & metrics"),
            {
                "classes": ("tab",),
                "fields": (
                    "ad_gate_enabled",
                    "ad_gate_min_views",
                    "ad_gate_config",
                    "views_count",
                    "likes_count",
                    "comments_count",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )
    inlines = (PostRevisionInline,)
    date_hierarchy = "updated_at"
    actions = (
        "submit_selected_for_review",
        "publish_selected",
        "archive_selected",
    )
    actions_row = ("request_review", "publish", "archive")
    actions_detail = ("request_review", "publish", "schedule", "archive")
    list_select_related = ("author", "category", "firmware_brand", "firmware_model")

    @display(description=_("Status"), ordering="status", label=BADGE_POST_STATUS)
    def status_badge(self, obj: Post) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @display(description=_("Source"), ordering="is_ai_generated", label=BADGE_SOURCE)
    def content_source(self, obj: Post) -> tuple[str, str]:
        if obj.is_ai_generated:
            return "automated", str(_("Automated"))
        return "manual", str(_("Manual"))

    def save_model(
        self, request: HttpRequest, obj: Post, form: ModelForm, change: bool
    ) -> None:
        if not change:
            super().save_model(request, obj, form, change)
            return
        values = {
            name: value for name, value in form.cleaned_data.items() if name != "tags"
        }
        PostHistoryService.update_post(
            obj,
            user=request.user,
            change_summary="Edited in Django admin",
            tags=form.cleaned_data["tags"],
            **values,
        )

    def save_related(
        self, request: HttpRequest, form: ModelForm, formsets: Any, change: bool
    ) -> None:
        super().save_related(request, form, formsets, change)
        if not change:
            PostHistoryService.record_current_version(
                form.instance,
                user=request.user,
                change_summary="Initial version",
            )

    # -- bulk actions ---------------------------------------------------------
    def _run_workflow(
        self,
        request: HttpRequest,
        queryset: QuerySet[Post],
        step: Callable[..., object],
        done_label: str,
    ) -> None:
        done = 0
        skipped = 0
        for post in queryset:
            try:
                step(post, user=request.user)
            except (PermissionDenied, ValidationError):
                skipped += 1
            else:
                done += 1
        self.message_user(
            request,
            f"{done_label} {done} posts; {skipped} were not eligible or lacked permission.",
        )

    @action(description=_("Submit selected posts for review"))
    def submit_selected_for_review(
        self, request: HttpRequest, queryset: QuerySet[Post]
    ) -> None:
        self._run_workflow(request, queryset, workflow.request_review, "Submitted")

    @action(description=_("Publish selected posts"))
    def publish_selected(self, request: HttpRequest, queryset: QuerySet[Post]) -> None:
        self._run_workflow(request, queryset, workflow.publish, "Published")

    @action(description=_("Archive selected posts"))
    def archive_selected(self, request: HttpRequest, queryset: QuerySet[Post]) -> None:
        self._run_workflow(request, queryset, workflow.archive, "Archived")

    # -- row / detail actions: each link opens a confirmation dialog ---------------
    def _decide(
        self,
        request: HttpRequest,
        object_id: str,
        step: Callable[[Post], object],
        success: str,
    ) -> HttpResponse:
        post = self.get_object(request, object_id)
        if post is None:
            raise Http404
        try:
            step(post)
        except PermissionDenied:
            self.message_user(
                request, _("You may not do that to this post."), messages.ERROR
            )
        except ValidationError as exc:
            self.message_user(request, " ".join(exc.messages), messages.ERROR)
        else:
            self.log_change(request, post, success)
            self.message_user(request, success, messages.SUCCESS)
        return htmx_redirect(return_url(request, change_url(self, post.pk)))

    @action(
        description=_("Request review"),
        icon="rate_review",
        permissions=["change"],
        dialog=confirm_dialog(
            _("Submit for review?"),
            _("The post moves to the review queue."),
            submit=_("Submit"),
        ),
    )
    def request_review(
        self, request: HttpRequest, form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._decide(
            request,
            object_id,
            lambda post: workflow.request_review(post, user=request.user),
            str(_("Submitted for review.")),
        )

    @action(
        description=_("Publish"),
        icon="publish",
        variant=ActionVariant.PRIMARY,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Publish this post?"),
            _("The post becomes visible on the public site immediately."),
            submit=_("Publish"),
        ),
    )
    def publish(
        self, request: HttpRequest, form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._decide(
            request,
            object_id,
            lambda post: workflow.publish(post, user=request.user),
            str(_("Published.")),
        )

    @action(
        description=_("Schedule"),
        icon="schedule",
        permissions=["change"],
        dialog=confirm_dialog(
            _("Schedule publication"),
            form_class=ScheduleForm,
            submit=_("Schedule"),
        ),
    )
    def schedule(
        self, request: HttpRequest, form: ScheduleForm, object_id: str
    ) -> HttpResponse:
        when = form.cleaned_data["publish_at"]
        return self._decide(
            request,
            object_id,
            lambda post: workflow.schedule(post, when, user=request.user),
            str(_("Scheduled.")),
        )

    @action(
        description=_("Archive"),
        icon="inventory_2",
        variant=ActionVariant.DANGER,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Archive this post?"),
            _("Archived posts are removed from the public site."),
            submit=_("Archive"),
        ),
    )
    def archive(
        self, request: HttpRequest, form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._decide(
            request,
            object_id,
            lambda post: workflow.archive(post, user=request.user),
            str(_("Archived.")),
        )


@admin.register(BlogSettings)
class BlogSettingsAdmin(SingletonModelAdmin):
    list_display = ("enable_blog", "enable_blog_comments", "allow_user_blog_posts")
    fieldsets = (
        (None, {"fields": ("enable_blog", "enable_blog_comments")}),
        (_("User posting"), {"fields": ("allow_user_blog_posts",)}),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


@admin.register(AutoTopic)
class AutoTopicAdmin(ModelAdmin):
    list_display = (
        "topic",
        "status_badge",
        "post",
        "retry_count",
        "created_by",
        "created_at",
        "last_attempt_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        "retry_count",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("topic", "post__title")
    raw_id_fields = ("post", "created_by")
    actions = ("requeue_selected",)

    @display(description=_("Status"), ordering="status", label=BADGE_JOB)
    def status_badge(self, obj: AutoTopic) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @action(description=_("Put selected failed topics back in the queue"))
    def requeue_selected(
        self, request: HttpRequest, queryset: QuerySet[AutoTopic]
    ) -> None:
        updated = queryset.filter(status="failed").update(
            status="queued", last_error=""
        )
        self.message_user(
            request,
            _("%(count)d failed topic(s) queued again.") % {"count": updated},
            messages.SUCCESS if updated else messages.WARNING,
        )


@admin.register(PostTranslation)
class PostTranslationAdmin(ModelAdmin):
    list_display = ("post", "language", "title")
    list_filter = ("language",)
    search_fields = ("title", "summary", "post__title")
    raw_id_fields = ("post",)


@admin.register(CategoryTranslation)
class CategoryTranslationAdmin(ModelAdmin):
    list_display = ("category", "language", "name")
    list_filter = ("language",)
    search_fields = ("name", "category__name")
    raw_id_fields = ("category",)


@admin.register(TagTranslation)
class TagTranslationAdmin(ModelAdmin):
    list_display = ("tag", "language", "name")
    list_filter = ("language",)
    search_fields = ("name", "tag__name")
    raw_id_fields = ("tag",)

"""Blog admin: posts with the editorial workflow, categories, queue and translations."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.admin_base import (
    BADGE_APPROVAL,
    ActionVariant,
    ConfirmForm,
    ModelAdmin,
    NoAddModelAdmin,
    ReasonForm,
    SingletonModelAdmin,
    TabularInline,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)
from apps.core.admin_filters import (
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    DropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from apps.core.admin_forms import ConsoleSplitDateTimeWidget
from apps.core.editor.admin import RichTextAdminMixin
from apps.core.editor.sanitizer import render_rich_text

from . import freshness, guide_requests, shop_gateway
from .models import (
    BlogSettings,
    Category,
    CategoryTranslation,
    GuideRequest,
    Post,
    PostAccess,
    PostRevision,
    PostSource,
    PostStatus,
    PostTranslation,
    TagTranslation,
)
from .services import contributions, workflow
from .services.history import PostHistoryService

BADGE_POST_STATUS: dict[str, str] = {
    PostStatus.DRAFT: "info",
    PostStatus.IN_REVIEW: "warning",
    PostStatus.SCHEDULED: "info",
    PostStatus.PUBLISHED: "success",
    PostStatus.ARCHIVED: "danger",
}
BADGE_SOURCE: dict[str, str] = {
    PostSource.AUTOMATED: "info",
    PostSource.EDITORIAL: "success",
    PostSource.COMMUNITY_CONTRIBUTION: "warning",
}
BADGE_FRESHNESS: dict[str, str] = {
    "stale": "warning",
    "fresh": "success",
    "idle": "info",
}


class ScheduleForm(ConfirmForm):
    """Dialog asking when a post should go live."""

    publish_at = forms.SplitDateTimeField(
        label=_("Publish at"),
        widget=ConsoleSplitDateTimeWidget,
        help_text=_("The post is published automatically at this time."),
    )


class LowerAccessForm(ReasonForm):
    """Dialog: lower a post's access type and tell the author why."""

    access = forms.ChoiceField(
        label=_("New access type"),
        choices=[
            (PostAccess.FREE, _("Free")),
            (PostAccess.FEATURED, _("Featured")),
        ],
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


class FreshnessFilter(DropdownFilter):
    """Editors' content-freshness filter driven by ``BlogSettings.stale_after_days``."""

    title = _("freshness")
    parameter_name = "freshness"

    def lookups(
        self, request: HttpRequest, model_admin: admin.ModelAdmin
    ) -> list[tuple[str, str]]:
        days = freshness.stale_after_days()
        return [
            ("stale", str(_("Stale (not updated for %(days)s days)")) % {"days": days}),
            ("fresh", str(_("Not stale"))),
        ]

    def queryset(
        self, request: HttpRequest, queryset: QuerySet[Post]
    ) -> QuerySet[Post]:
        value = self.value()
        if value == "stale":
            return freshness.stale_posts(queryset)
        if value == "fresh":
            return queryset.exclude(pk__in=freshness.stale_posts(queryset).values("pk"))
        return queryset


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ("name", "parent", "slug")
    search_fields = ("name", "slug")
    list_filter = (("parent", RelatedDropdownFilter),)
    list_select_related = ("parent",)
    prepopulated_fields: ClassVar[dict[str, Sequence[str]]] = {"slug": ("name",)}


@admin.register(Post)
class PostAdmin(RichTextAdminMixin, ModelAdmin):
    rich_text_fields: ClassVar[Mapping[str, str]] = {"body": "blog_post"}
    list_display = (
        "title",
        "status_badge",
        "content_source",
        "category",
        "access",
        "firmware_brand",
        "firmware_model",
        "author",
        "allow_comments",
        "noindex",
        "published_at",
        "views_count",
        "updated_at",
        "freshness_badge",
    )
    list_filter = (
        FreshnessFilter,
        ("status", ChoicesDropdownFilter),
        ("content_source", ChoicesDropdownFilter),
        ("access", ChoicesDropdownFilter),
        ("category", RelatedDropdownFilter),
        ("firmware_brand", RelatedDropdownFilter),
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
    )
    prepopulated_fields: ClassVar[dict[str, Sequence[str]]] = {"slug": ("title",)}
    autocomplete_fields = ("category", "tags")
    raw_id_fields = (
        "author",
        "firmware_brand",
        "firmware_model",
        "ad_gate_config",
        "guide_request",
    )
    readonly_fields = (
        "status",
        "is_published",
        "content_source",
        "review_note",
        "published_at",
        "published_by",
        "price_approved_by",
        "price_approved_at",
        "buyers",
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
            _("Access & price"),
            {
                "classes": ("tab",),
                "description": _(
                    "Featured and paid posts show a teaser to readers without access. "
                    "The approved price is in USD and is converted to credits at the "
                    "wallet rate. Publishing a post approves its proposed price."
                ),
                "fields": (
                    "access",
                    "price",
                    "proposed_price",
                    "price_approved_by",
                    "price_approved_at",
                    "guide_request",
                    "published_by",
                    "buyers",
                ),
            },
        ),
        (
            _("Publishing"),
            {
                "classes": ("tab",),
                "fields": (
                    "status",
                    "is_published",
                    "review_note",
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
                    "content_source",
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
    actions_row = ("request_review", "publish", "reject", "archive")
    actions_detail = (
        "request_review",
        "publish",
        "reject",
        "lower_access",
        "schedule",
        "archive",
    )
    list_select_related = ("author", "category", "firmware_brand", "firmware_model")

    @display(description=_("Status"), ordering="status", label=BADGE_POST_STATUS)
    def status_badge(self, obj: Post) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @display(description=_("Freshness"), ordering="updated_at", label=BADGE_FRESHNESS)
    def freshness_badge(self, obj: Post) -> tuple[str, str]:
        """Stale live posts are flagged with how long ago they were last updated."""
        if not obj.is_live:
            return "idle", "—"
        if freshness.is_stale(obj):
            days = freshness.days_since_update(obj)
            return "stale", str(_("Stale · %(days)s days")) % {"days": days}
        return "fresh", str(_("Fresh"))

    @display(description=_("Source"), ordering="content_source", label=BADGE_SOURCE)
    def content_source(self, obj: Post) -> tuple[str, str]:
        return obj.content_source, obj.get_content_source_display()

    @display(description=_("Buyers"))
    def buyers(self, obj: Post | None) -> str:
        """Read-only list of buyers, supplied by the shop (it owns purchases)."""
        if obj is None or obj.pk is None or not obj.is_gated:
            return "—"
        rows = shop_gateway.buyers(obj.pk)
        if not rows:
            return str(_("No purchases recorded."))
        return ", ".join(
            f"{row.get('user', '?')} ({row.get('credits', '?')} credits)"
            for row in rows
        )

    def save_model(
        self, request: HttpRequest, obj: Post, form: ModelForm, change: bool
    ) -> None:
        if not change:
            if obj.access != PostAccess.FREE and obj.price is not None:
                obj.price_approved_by_id = request.user.pk
                obj.price_approved_at = timezone.now()
            super().save_model(request, obj, form, change)
            return
        values = {
            name: value for name, value in form.cleaned_data.items() if name != "tags"
        }
        if values.get("price") is not None and values["price"] != obj.price:
            values["price_approved_by_id"] = request.user.pk
            values["price_approved_at"] = timezone.now()
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
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
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
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._decide(
            request,
            object_id,
            lambda post: workflow.publish(post, user=request.user),
            str(_("Published.")),
        )

    @action(
        description=_("Reject"),
        icon="cancel",
        variant=ActionVariant.DANGER,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Send this post back?"),
            _("The post returns to its author's drafts and they read your reason."),
            ReasonForm,
            submit=_("Reject"),
        ),
    )
    def reject(
        self, request: HttpRequest, form: ReasonForm, object_id: str
    ) -> HttpResponse:
        """Send a post under review back to its author with a reason."""
        reason = str(form.cleaned_data.get("reason", ""))
        return self._decide(
            request,
            object_id,
            lambda post: contributions.reject(request.user, post, reason),
            str(_("Sent back to the author.")),
        )

    @action(
        description=_("Lower access type"),
        icon="lock_open",
        permissions=["change"],
        dialog=confirm_dialog(
            _("Lower the access type?"),
            _(
                "Use this when the author's Featured or Paid choice is not "
                "appropriate. The author is told the reason."
            ),
            LowerAccessForm,
            submit=_("Lower access"),
        ),
    )
    def lower_access(
        self, request: HttpRequest, form: LowerAccessForm, object_id: str
    ) -> HttpResponse:
        """Downgrade Paid/Featured to a lower tier with a reason for the author."""
        access = str(form.cleaned_data["access"])
        reason = str(form.cleaned_data.get("reason", ""))
        return self._decide(
            request,
            object_id,
            lambda post: contributions.lower_access(request.user, post, access, reason),
            str(_("Access type lowered.")),
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
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
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
        (
            _("User posting"),
            {"fields": ("allow_user_blog_posts", "contributions_per_day")},
        ),
        (_("Featured and paid articles"), {"fields": ("teaser_words",)}),
        (_("Content freshness"), {"fields": ("stale_after_days",)}),
        (
            _("Guide requests"),
            {
                "fields": (
                    "allow_guide_requests",
                    "guide_requests_per_day",
                    "guide_requests_open_limit",
                )
            },
        ),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


@admin.register(GuideRequest)
class GuideRequestAdmin(NoAddModelAdmin):
    """Review queue: staff decide; nothing else about a request is edited here."""

    list_display = (
        "title",
        "kind",
        "status_badge",
        "requester",
        "context",
        "created_at",
        "reviewed_by",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("kind", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = (
        "title",
        "description",
        "tried",
        "model_code",
        "variant_code",
        "requester__username",
        "requester__email",
    )
    list_select_related = (
        "requester",
        "brand",
        "firmware_model",
        "variant",
        "chipset",
        "related_post",
        "reviewed_by",
    )
    ordering = ("status", "-created_at")
    readonly_fields = (
        "requester",
        "title",
        "tried_text",
        "expected_outcome",
        "bounty_offer",
        "description_text",
        "kind",
        "brand",
        "firmware_model",
        "model_code",
        "variant",
        "variant_code",
        "chipset",
        "search_keywords",
        "related_article",
        "follower_total",
        "status",
        "reviewed_by",
        "reviewed_at",
        "decision_reason",
        "fulfilled_by_post",
        "fulfilled_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("title", "kind", "requester")}),
        (
            _("What the requester tried"),
            {
                "fields": (
                    "tried_text",
                    "expected_outcome",
                    "bounty_offer",
                    "description_text",
                )
            },
        ),
        (
            _("Device and article"),
            {
                "fields": (
                    "brand",
                    "firmware_model",
                    "model_code",
                    "variant",
                    "variant_code",
                    "chipset",
                    "search_keywords",
                    "related_article",
                    "follower_total",
                )
            },
        ),
        (
            _("Decision"),
            {
                "fields": (
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "decision_reason",
                    "fulfilled_by_post",
                    "fulfilled_at",
                )
            },
        ),
        (_("Dates"), {"fields": ("created_at", "updated_at")}),
    )
    actions = ("approve_selected",)
    actions_row = ("approve_request", "reject_request")
    actions_detail = ("approve_request", "reject_request")

    def has_delete_permission(
        self, request: HttpRequest, obj: GuideRequest | None = None
    ) -> bool:
        """Requests are never deleted: they are cancelled or rejected."""
        return False

    @display(description=_("Status"), ordering="status", label=BADGE_APPROVAL)
    def status_badge(self, obj: GuideRequest) -> tuple[str, str]:
        """Status as a coloured badge."""
        return obj.status, obj.get_status_display()

    @display(description=_("Device"))
    def context(self, obj: GuideRequest) -> str:
        """Brand, model, variant and chipset the request is about."""
        return obj.label or "—"

    @display(description=_("Article that did not help"))
    def related_article(self, obj: GuideRequest) -> str:
        """Link to the article the requester says did not resolve the issue."""
        post = obj.related_post
        if post is None:
            return "—"
        url = reverse("admin:blog_post_change", args=[post.pk])
        return format_html('<a href="{}">{}</a>', url, post.title)

    @display(description=_("What the requester tried"))
    def tried_text(self, obj: GuideRequest) -> str:
        """The requester's formatted text, re-sanitised for display."""
        return render_rich_text(obj.tried) or "—"

    @display(description=_("Extra details"))
    def description_text(self, obj: GuideRequest) -> str:
        """The requester's optional formatted details, re-sanitised for display."""
        return render_rich_text(obj.description) or "—"

    @display(description=_("Followers"))
    def follower_total(self, obj: GuideRequest) -> int:
        """Members who also need this answer."""
        return obj.followers.count()

    def _decide(
        self,
        request: HttpRequest,
        object_id: str,
        step: Callable[[GuideRequest], object],
        success: str,
    ) -> HttpResponse:
        item = self.get_object(request, object_id)
        if item is None:
            raise Http404
        try:
            step(item)
        except guide_requests.GuideRequestError as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.log_change(request, item, success)
            self.message_user(request, success, messages.SUCCESS)
        return htmx_redirect(return_url(request, change_url(self, item.pk)))

    @action(description=_("Approve selected requests"), permissions=["change"])
    def approve_selected(
        self, request: HttpRequest, queryset: QuerySet[GuideRequest]
    ) -> None:
        """Approve every pending request in the selection."""
        done = skipped = 0
        for item in queryset.filter(status=GuideRequest.Status.PENDING):
            try:
                guide_requests.approve(item.pk, reviewer=request.user)
            except guide_requests.GuideRequestError:
                skipped += 1
            else:
                done += 1
        self.message_user(request, f"Approved {done} requests; {skipped} skipped.")

    @action(
        description=_("Approve"),
        icon="check_circle",
        variant=ActionVariant.PRIMARY,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Approve this request?"),
            _(
                "It becomes public and can carry credit bounties. "
                "The requester is notified."
            ),
            submit=_("Approve"),
        ),
    )
    def approve_request(
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        """Approve the request through the service (idempotent, notifies)."""
        return self._decide(
            request,
            object_id,
            lambda item: guide_requests.approve(item.pk, reviewer=request.user),
            str(_("Request approved.")),
        )

    @action(
        description=_("Reject"),
        icon="cancel",
        variant=ActionVariant.DANGER,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Reject this request?"),
            _("The requester is told the reason."),
            ReasonForm,
            submit=_("Reject"),
        ),
    )
    def reject_request(
        self, request: HttpRequest, form: ReasonForm, object_id: str
    ) -> HttpResponse:
        """Reject the request with the reason the requester will read."""
        reason = str(form.cleaned_data.get("reason", ""))
        return self._decide(
            request,
            object_id,
            lambda item: guide_requests.reject(
                item.pk, reviewer=request.user, reason=reason
            ),
            str(_("Request rejected.")),
        )


@admin.register(PostTranslation)
class PostTranslationAdmin(RichTextAdminMixin, ModelAdmin):
    rich_text_fields: ClassVar[Mapping[str, str]] = {"body": "blog_post"}
    list_display = ("post", "language", "title")
    list_filter = ("language",)
    search_fields = ("title", "summary", "post__title")
    raw_id_fields = ("post",)
    list_select_related = ("post",)


@admin.register(CategoryTranslation)
class CategoryTranslationAdmin(ModelAdmin):
    list_display = ("category", "language", "name")
    list_filter = ("language",)
    search_fields = ("name", "category__name")
    raw_id_fields = ("category",)
    list_select_related = ("category",)


@admin.register(TagTranslation)
class TagTranslationAdmin(ModelAdmin):
    list_display = ("tag", "language", "name")
    list_filter = ("language",)
    search_fields = ("name", "tag__name")
    raw_id_fields = ("tag",)
    list_select_related = ("tag",)

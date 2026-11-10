"""CMS page administration (Unfold): publishing workflow and draft preview."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
)
from unfold.forms import BaseDialogForm

from apps.core.admin_base import (
    ModelAdmin,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)
from apps.core.editor.admin import RichTextAdminMixin

from .models import Page

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise

STATUS_BADGES = {
    Page.Status.DRAFT.value: "warning",
    Page.Status.PUBLISHED.value: "success",
    Page.Status.ARCHIVED.value: "info",
}
VISIBILITY_BADGES = {
    "live": "success",
    "scheduled": "info",
    "expired": "warning",
    "not published": "danger",
}


@register(Page)
class PageAdmin(RichTextAdminMixin, ModelAdmin):
    # The editor only applies to HTML pages; Markdown pages keep the plain textarea.
    rich_text_fields: ClassVar[Mapping[str, str]] = {"content": "page"}
    rich_text_when: ClassVar[Mapping[str, tuple[str, str]]] = {
        "content": ("content_format", Page.ContentFormat.HTML)
    }
    list_display = (
        "title",
        "slug",
        "status_badge",
        "visibility_badge",
        "access_level",
        "include_in_sitemap",
        "updated_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("access_level", ChoicesDropdownFilter),
        "include_in_sitemap",
        ("changefreq", ChoicesDropdownFilter),
        ("updated_at", RangeDateTimeFilter),
    )
    search_fields = ("title", "slug", "seo_title")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("title",)}
    readonly_fields = ("created_by", "updated_by", "created_at", "updated_at")
    actions = ("publish_selected", "unpublish_selected")
    actions_row = ("publish_page", "unpublish_page")
    actions_detail = ("preview_page", "publish_page", "unpublish_page")
    fieldsets = (
        (
            _("Content"),
            {
                "fields": (
                    "title",
                    "slug",
                    "content",
                    "content_format",
                    "status",
                    "access_level",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Publishing"),
            {"fields": ("publish_at", "unpublish_at"), "classes": ["tab"]},
        ),
        (
            _("SEO"),
            {
                "fields": (
                    "seo_title",
                    "seo_description",
                    "og_image",
                    "canonical_url",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Sitemap"),
            {
                "fields": ("include_in_sitemap", "changefreq", "priority"),
                "classes": ["tab"],
            },
        ),
        (
            _("Audit"),
            {
                "fields": ("created_by", "updated_by", "created_at", "updated_at"),
                "classes": ["tab"],
            },
        ),
    )

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: Page) -> str:
        return obj.status

    @display(description=_("Visibility"), label=VISIBILITY_BADGES)
    def visibility_badge(self, obj: Page) -> str:
        if obj.status != Page.Status.PUBLISHED:
            return "not published"
        now = timezone.now()
        if obj.publish_at and obj.publish_at > now:
            return "scheduled"
        if obj.unpublish_at and obj.unpublish_at <= now:
            return "expired"
        return "live"

    def save_model(
        self, request: HttpRequest, obj: Page, form: ModelForm[Any], change: bool
    ) -> None:
        if not change:
            obj.created_by_id = request.user.pk
        obj.updated_by_id = request.user.pk
        super().save_model(request, obj, form, change)

    def _transition(
        self, request: HttpRequest, queryset: QuerySet[Page], status: Page.Status
    ) -> int:
        """Save each page so the widget-cache and sitemap signals still fire."""
        changed = 0
        for page in queryset.exclude(status=status):
            page.status = status
            page.updated_by_id = request.user.pk
            page.save(update_fields=["status", "updated_by", "updated_at"])
            changed += 1
        return changed

    def _transition_one(
        self,
        request: HttpRequest,
        object_id: str,
        status: Page.Status,
        done: StrOrPromise,
    ) -> HttpResponse:
        page = self.get_object(request, unquote(object_id))
        if page is None:
            raise Http404
        if self._transition(request, Page.objects.filter(pk=page.pk), status):
            self.message_user(request, done)
        else:
            self.message_user(
                request,
                _("The page is already %(status)s.") % {"status": status.label.lower()},
                messages.WARNING,
            )
        return htmx_redirect(return_url(request, change_url(self, page.pk)))

    @action(description=_("Publish selected pages"), permissions=["change"])
    def publish_selected(self, request: HttpRequest, queryset: QuerySet[Page]) -> None:
        count = self._transition(request, queryset, Page.Status.PUBLISHED)
        self.message_user(request, _("%(count)d page(s) published.") % {"count": count})

    @action(
        description=_("Unpublish selected pages (back to draft)"),
        permissions=["change"],
    )
    def unpublish_selected(
        self, request: HttpRequest, queryset: QuerySet[Page]
    ) -> None:
        count = self._transition(request, queryset, Page.Status.DRAFT)
        self.message_user(
            request, _("%(count)d page(s) returned to draft.") % {"count": count}
        )

    @action(
        description=_("Publish"),
        icon="publish",
        permissions=["change"],
        url_path="publish-page",
        dialog=confirm_dialog(
            _("Publish this page"),
            _(
                "The page becomes visible to its access level, within the "
                "publish and unpublish dates."
            ),
            submit=_("Publish"),
        ),
    )
    def publish_page(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._transition_one(
            request, object_id, Page.Status.PUBLISHED, _("Page published.")
        )

    @action(
        description=_("Unpublish"),
        icon="unpublished",
        permissions=["change"],
        url_path="unpublish-page",
        dialog=confirm_dialog(
            _("Unpublish this page"),
            _("The page returns to draft and disappears from the public site."),
            submit=_("Unpublish"),
        ),
    )
    def unpublish_page(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._transition_one(
            request, object_id, Page.Status.DRAFT, _("Page returned to draft.")
        )

    @action(
        description=_("Preview"),
        icon="visibility",
        permissions=["view"],
        url_path="preview-page",
    )
    def preview_page(self, request: HttpRequest, object_id: str) -> HttpResponse:
        page = self.get_object(request, unquote(object_id))
        if page is None:
            raise Http404
        context = {
            **self.admin_site.each_context(request),
            "title": _("Preview: %(title)s") % {"title": page.title},
            "page": page,
            "rendered": page.rendered,
            "is_live": page.is_published,
            "change_url": change_url(self, page.pk),
        }
        return TemplateResponse(request, "admin/pages/page_preview.html", context)

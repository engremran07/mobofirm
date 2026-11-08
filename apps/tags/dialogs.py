"""Tag curation dialogs: reject a suggestion and merge two tags. Tags owns the copy."""

from __future__ import annotations

from typing import Any

from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _

from apps.blog.models import Post
from apps.core.admin_dialogs import StaffDialogView
from apps.core.dialogs import DialogError, DialogForm
from apps.tags.models import Tag
from apps.tags.services import automation


def can_curate(request: HttpRequest) -> bool:
    """Staff holding ``tags.change_tag`` may curate tags."""
    user = request.user
    return bool(user.is_staff and user.has_perm("tags.change_tag"))


class RejectSuggestionDialog(StaffDialogView):
    """Confirm retiring a suggested tag."""

    template_name = "tags/dialogs/reject_suggestion.html"
    dialog_id = "tag-reject-dialog"
    dialog_title = _("Reject this suggested tag?")
    dialog_message = _("It is retired and will not be attached to articles.")
    confirm_label = _("Reject tag")
    danger = True
    success_url_name = "tags:manage"
    rejected_name = ""

    def has_permission(self, request: HttpRequest) -> bool:
        """Staff holding ``tags.change_tag``."""
        return can_curate(request)

    def get_tag(self) -> Tag:
        """The pending suggestion named in the URL."""
        return get_object_or_404(
            Tag,
            pk=self.kwargs["tag_id"],
            is_deleted=False,
            is_curated=False,
            ai_suggested=True,
        )

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        """Add the tag being rejected."""
        context = super().get_context_data(form)
        context["tag"] = self.get_tag()
        return context

    def perform(self, form: DialogForm) -> None:
        """Retire the suggestion through the tags service."""
        tag = self.get_tag()
        automation.reject_suggestion(tag)
        self.rejected_name = tag.name

    def get_success_message(self) -> str:
        """Confirmation shown after the redirect."""
        return f"Rejected “{self.rejected_name}”."


class MergeTagsDialog(StaffDialogView):
    """Preview the impact of merging ``source`` into ``target`` (slugs in the query)."""

    template_name = "tags/dialogs/merge.html"
    dialog_id = "tag-merge-dialog"
    confirm_label = _("Merge tags")
    danger = True
    size = "md"
    success_url_name = "tags:manage"
    merged: tuple[str, str] = ("", "")

    def has_permission(self, request: HttpRequest) -> bool:
        """Staff holding ``tags.change_tag``."""
        return can_curate(request)

    def load_tags(self) -> tuple[Tag, Tag]:
        """The source and destination tags named in the query string."""
        source_slug = self.request.GET.get("source", "").strip()
        target_slug = self.request.GET.get("target", "").strip()
        if not source_slug or not target_slug:
            raise Http404
        source = get_object_or_404(Tag, slug=source_slug, is_deleted=False)
        target = get_object_or_404(
            Tag, slug=target_slug, is_deleted=False, merge_into__isnull=True
        )
        return source, target

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        """Add the title and the number of articles the merge changes."""
        source, target = self.load_tags()
        context = super().get_context_data(form)
        context.update(
            dialog_title=_("Merge “%(source)s” into “%(target)s”?")
            % {"source": source.name, "target": target.name},
            source=source,
            target=target,
            affected=Post.objects.filter(tags=source).count(),
        )
        return context

    def perform(self, form: DialogForm) -> None:
        """Merge atomically through the tags service."""
        source, target = self.load_tags()
        if source.pk == target.pk:
            raise DialogError("A tag cannot be merged into itself.")
        automation.merge_tags(source, target, user=self.request.user)
        self.merged = (source.name, target.name)

    def get_success_message(self) -> str:
        """Confirmation shown after the redirect."""
        return f"Merged “{self.merged[0]}” into “{self.merged[1]}”."

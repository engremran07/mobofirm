"""Comment moderation dialogs: comments owns the decision and its audit note."""

from __future__ import annotations

from functools import cached_property
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _

from apps.comments.models import Comment
from apps.comments.services.decisions import moderate_comment
from apps.core.admin_dialogs import StaffDialogView
from apps.core.dialogs import DialogError, DialogForm

COPY: dict[str, dict[str, Any]] = {
    "reject": {
        "title": _("Reject this comment?"),
        "message": _("It stays hidden from readers. The note is kept for the record."),
        "confirm": _("Reject"),
    },
    "spam": {
        "title": _("Mark this comment as spam?"),
        "message": _("It is hidden and counted against the author's trust."),
        "confirm": _("Mark as spam"),
    },
}


class ModerationNoteForm(DialogForm):
    """An optional note kept with the decision."""

    reason_required = False
    reason_label = _("Note for the record (optional)")


class ModerationDialog(StaffDialogView):
    """Reject or mark a pending comment as spam, with an optional note."""

    template_name = "comments/dialogs/moderate.html"
    form_class = ModerationNoteForm
    danger = True
    success_url_name = "comments:moderation_queue"

    @cached_property
    def action(self) -> str:
        """The moderation action named in the URL (reject or spam)."""
        action = str(self.kwargs["action"])
        if action not in COPY:
            raise Http404
        return action

    @cached_property
    def comment(self) -> Comment:
        """The comment being moderated."""
        return get_object_or_404(
            Comment.objects.select_related("post", "user"),
            pk=self.kwargs["comment_id"],
            is_deleted=False,
        )

    def has_permission(self, request: HttpRequest) -> bool:
        """Staff holding ``comments.change_comment``."""
        user = request.user
        return bool(user.is_staff and user.has_perm("comments.change_comment"))

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        """Add the per-action copy and the comment."""
        context = super().get_context_data(form)
        copy = COPY[self.action]
        context.update(
            dialog_id="comment-moderation-dialog",
            dialog_title=copy["title"],
            dialog_message=copy["message"],
            confirm_label=copy["confirm"],
            comment=self.comment,
        )
        return context

    def perform(self, form: DialogForm) -> None:
        """Record the decision and note through the comments service."""
        try:
            moderate_comment(
                self.comment, self.action, self.request.user, note=form.reason
            )
        except (ValidationError, PermissionDenied) as error:
            raise DialogError(
                " ".join(getattr(error, "messages", [])) or str(error)
            ) from error

    def get_success_message(self) -> str:
        """Confirmation shown after the redirect."""
        return "Decision recorded."

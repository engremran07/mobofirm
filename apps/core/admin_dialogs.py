"""App-owned dialogs for staff tool pages rendered inside the Unfold admin shell.

``StaffDialogView`` is the admin-shell counterpart of ``DialogView`` rendering:
the HTMX fragment extends ``admin/staff_dialog.html`` (an Alpine-opened native
``<dialog>``, no Bootstrap) and the no-JavaScript fallback is a full admin page
(``admin/staff_dialog_page.html``) carrying the same form. Pages that open these
dialogs include ``admin/staff_dialog_slot.html`` for the ``#dialog-slot`` target.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.template.response import TemplateResponse

from apps.core.admin_pages import admin_page_context
from apps.core.dialogs import DialogForm, DialogView, is_dialog_request

__all__ = ["StaffDialogView"]


class StaffDialogView(DialogView):
    """``DialogView`` whose fallback page renders in the admin shell."""

    page_template_name = "admin/staff_dialog_page.html"

    def perform(self, form: DialogForm) -> None:
        """Abstract: concrete staff dialogs do the work for a valid ``form``."""
        raise NotImplementedError(
            "StaffDialogView subclasses must implement perform()."
        )

    def render_dialog(self, form: DialogForm) -> HttpResponse:
        """A fragment for HTMX, an admin-shell page (same dialog, open) otherwise."""
        if is_dialog_request(self.request):
            return super().render_dialog(form)
        context = self.get_context_data(form)
        return TemplateResponse(
            self.request,
            self.page_template_name,
            admin_page_context(
                self.request, title=str(context["dialog_title"]), context=context
            ),
        ).render()

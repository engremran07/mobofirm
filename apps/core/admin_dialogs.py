"""App-owned dialogs for staff pages rendered in the staff console.

``StaffDialogView`` is the console counterpart of ``DialogView``: one accessible dialog
pattern for every staff action.

* ``GET`` renders the form and never changes data. Over HTMX the fragment is the Bootstrap
  modal of ``admin/staff_dialog.html``, loaded into ``#console-dialog``; without
  JavaScript it is a full console page (``admin/staff_dialog_page.html``) carrying the
  same form.
* ``POST`` is CSRF-protected and permission-checked. Invalid input re-renders the form
  with its errors (HTTP 200); success answers ``HX-Trigger`` (``dialog:close``,
  ``dialog:done`` and, when the page stays, a ``toast``) plus ``HX-Redirect`` or
  ``HX-Refresh``, or a POST-Redirect-GET for plain browsers.

Concrete dialogs live in the owning app: subclass, set ``form_class``/copy, override
``has_permission`` and ``perform`` and (optionally) ``template_name`` with a template that
extends ``admin/staff_dialog.html``. Open them with
``{% staff_button dialog_url=... %}``, which targets ``#console-dialog``.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils.cache import patch_vary_headers

from apps.core.admin_pages import admin_page_context
from apps.core.dialogs import DialogForm, DialogView, is_dialog_request

__all__ = ["StaffDialogView"]


class StaffDialogView(DialogView):
    """``DialogView`` rendered with the console templates."""

    template_name = "console/staff/dialog_form.html"
    page_template_name = "admin/staff_dialog_page.html"
    dialog_id = "staff-dialog"

    def perform(self, form: DialogForm) -> None:
        """Abstract: concrete staff dialogs do the work for a valid ``form``."""
        raise NotImplementedError(
            "StaffDialogView subclasses must implement perform()."
        )

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Dialog GETs differ between HTMX and plain requests, so caches must vary."""
        response = super().dispatch(request, *args, **kwargs)
        patch_vary_headers(response, ("HX-Request", "HX-Boosted"))
        return response

    def render_dialog(self, form: DialogForm) -> HttpResponse:
        """A fragment for HTMX, a console page (same form, open) otherwise."""
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

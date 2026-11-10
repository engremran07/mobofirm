"""Server-side half of the app-owned dialog system.

``core`` owns only the generic shell: ``components/dialog.html``, the Alpine
``appDialog`` component and this module. Every concrete dialog (its template,
form and view) lives in the app that owns the behaviour and subclasses
``DialogView`` here, so core never imports a domain app.

Conventions (see ``docs/05-frontend/dialogs.md``):

* ``GET`` returns a dialog fragment for HTMX (``hx-get`` into ``#dialog-slot``)
  or, without JavaScript, a full page carrying the same form.
* ``POST`` validates. Errors re-render the dialog (HTTP 200) in place. Success
  returns ``HX-Trigger: dialog:close, dialog:done`` plus ``HX-Redirect`` or
  ``HX-Refresh`` for HTMX, and a redirect (POST-Redirect-GET) otherwise.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views import View

__all__ = [
    "ConfirmDialogForm",
    "DialogError",
    "DialogForm",
    "DialogView",
    "dialog_success",
    "is_dialog_request",
    "safe_next_url",
]

REASON_MAX_LENGTH = 1000


class DialogError(Exception):
    """A business rule rejected the action; shown inside the dialog, not as a 500."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def is_dialog_request(request: HttpRequest) -> bool:
    """True for an HTMX request that expects a fragment (not a boosted page)."""
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-Boosted") != "true"
    )


def safe_next_url(request: HttpRequest, fallback: str | None = None) -> str | None:
    """The ``next`` parameter when it stays on this host, otherwise ``fallback``."""
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def dialog_success(
    request: HttpRequest,
    *,
    redirect_url: str | None = None,
    message: str = "",
    payload: Mapping[str, Any] | None = None,
    refresh: bool = True,
) -> HttpResponse:
    """Finish a dialog action.

    HTMX: an empty 204 carrying ``HX-Trigger`` (``dialog:close`` and
    ``dialog:done`` with ``payload``) and ``HX-Redirect`` to ``redirect_url`` or,
    without one, ``HX-Refresh``. With ``refresh=False`` the page stays put and
    listeners (``hx-trigger="dialog:done from:body"``) update what changed; the
    ``message`` then reaches the visitor as an immediate success toast (a ``toast``
    trigger event) instead of a flash message that would only appear on the next
    page. Other requests get a POST-Redirect-GET.
    """
    stays = bool(
        message and is_dialog_request(request) and not redirect_url and not refresh
    )
    if message and not stays:
        messages.success(request, message)
    if not is_dialog_request(request):
        return HttpResponseRedirect(redirect_url or safe_next_url(request, "/") or "/")
    response = HttpResponse(status=204)
    triggers: dict[str, Any] = {
        "dialog:close": {},
        "dialog:done": dict(payload or {}),
    }
    if stays:
        triggers["toast"] = {"message": message, "level": "success"}
    response["HX-Trigger"] = json.dumps(triggers)
    if redirect_url:
        response["HX-Redirect"] = redirect_url
    elif refresh:
        response["HX-Refresh"] = "true"
    return response


class DialogForm(forms.Form):
    """Base form for dialogs: an optional reason and an optional typed phrase.

    Set ``reason_required`` (``True``/``False``) to add a ``reason`` textarea and
    ``confirm_phrase`` to require the user to type that exact text. Both can also
    be passed as keyword arguments. The phrase is enforced server-side; the
    client only keeps the submit button disabled until it matches.
    """

    reason_required: bool | None = None
    reason_label: Any = _("Reason")
    confirm_phrase: str = ""

    def __init__(
        self,
        *args: Any,
        reason_required: bool | None = None,
        confirm_phrase: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if reason_required is not None:
            self.reason_required = reason_required
        if confirm_phrase is not None:
            self.confirm_phrase = confirm_phrase
        if self.reason_required is not None:
            self.fields["reason"] = forms.CharField(
                label=self.reason_label,
                max_length=REASON_MAX_LENGTH,
                required=self.reason_required,
                widget=forms.Textarea(attrs={"rows": 3}),
            )
        if self.confirm_phrase:
            self.fields["confirmation"] = forms.CharField(
                label=gettext("Type “%(phrase)s” to confirm")
                % {"phrase": self.confirm_phrase},
                widget=forms.TextInput(
                    attrs={"autocomplete": "off", "autocapitalize": "off"}
                ),
            )

    def clean_confirmation(self) -> str:
        """Require the exact phrase (surrounding whitespace ignored)."""
        typed = str(self.cleaned_data.get("confirmation", "")).strip()
        if typed != self.confirm_phrase:
            raise forms.ValidationError(
                gettext("Type “%(phrase)s” exactly to confirm.")
                % {"phrase": self.confirm_phrase}
            )
        return typed

    @property
    def reason(self) -> str:
        """The cleaned reason text, empty when none was asked for."""
        return str(self.cleaned_data.get("reason", "")).strip()


class ConfirmDialogForm(DialogForm):
    """Confirmation only: no fields, just the CSRF-protected POST."""


class DialogView(View):
    """GET renders the dialog, POST validates and performs ``perform``.

    Subclass in the owning app, set the copy and ``form_class`` (or a custom
    ``template_name`` extending ``components/dialog.html``), override
    ``has_permission`` to authorize and ``perform`` to do the work. Raise
    ``DialogError`` from ``perform`` to show a rule violation inside the dialog;
    the work runs in one transaction that rolls back on it.
    """

    template_name = "components/dialog_form.html"
    page_template_name = "components/dialog_page.html"
    form_class: type[DialogForm] = ConfirmDialogForm
    dialog_id = "app-dialog"
    dialog_title: Any = _("Please confirm")
    dialog_message: Any = ""
    confirm_label: Any = _("Confirm")
    cancel_label: Any = _("Cancel")
    danger = False
    size = "sm"
    static_backdrop: bool | None = None
    esc_closes = True
    unsaved_guard = False
    success_url: str | None = None
    success_url_name: str | None = None
    success_message: Any = ""
    refresh_on_success = True
    login_required = True

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any):
        """Gate on sign-in and ``has_permission`` before any handler runs."""
        if self.login_required and not request.user.is_authenticated:
            return self.unauthenticated(request)
        if not self.has_permission(request):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def unauthenticated(self, request: HttpRequest) -> HttpResponse:
        """Send anonymous users to sign-in (``HX-Redirect`` for HTMX)."""
        login_url = str(settings.LOGIN_URL)
        if is_dialog_request(request):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = f"{login_url}?{urlencode({'next': request.path})}"
            return response
        return redirect_to_login(request.get_full_path(), login_url)

    def has_permission(self, request: HttpRequest) -> bool:
        """Authorization hook; the default allows every signed-in user."""
        return request.user.is_authenticated or not self.login_required

    def get_form_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for the form (bound to POST data on POST)."""
        kwargs: dict[str, Any] = {"initial": self.get_initial()}
        if self.request.method == "POST":
            kwargs["data"] = self.request.POST
        return kwargs

    def get_initial(self) -> dict[str, Any]:
        """Initial form values."""
        return {}

    def get_form_class(self) -> type[DialogForm]:
        """The form class; override to choose one per request."""
        return self.form_class

    def get_form(self) -> DialogForm:
        """Instantiate the form."""
        return self.get_form_class()(**self.get_form_kwargs())

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        """Template context for ``components/dialog.html``."""
        static = self.danger if self.static_backdrop is None else self.static_backdrop
        inline = not is_dialog_request(self.request)
        return {
            "form": form,
            "dialog_id": self.dialog_id,
            "dialog_title": self.dialog_title,
            "dialog_message": self.dialog_message,
            "confirm_label": self.confirm_label,
            "cancel_label": self.cancel_label,
            "danger": self.danger,
            "dialog_size": self.size,
            "static_backdrop": static,
            "esc_blocked": not self.esc_closes,
            "unsaved_guard": self.unsaved_guard,
            "confirm_phrase": form.confirm_phrase,
            "dialog_action": self.request.get_full_path(),
            "dialog_hx": not inline,
            "dialog_inline": inline,
            "auto_open": not inline,
            "cancel_url": self.get_success_url() or "/",
            "dialog_template": self.template_name,
        }

    def render_dialog(self, form: DialogForm) -> HttpResponse:
        """A fragment for HTMX, a full page (same dialog, open) otherwise."""
        template = (
            self.template_name
            if is_dialog_request(self.request)
            else self.page_template_name
        )
        return TemplateResponse(
            self.request, template, self.get_context_data(form)
        ).render()

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Render the dialog."""
        return self.render_dialog(self.get_form())

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Validate the form, then perform the action or re-render with errors."""
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_invalid(self, form: DialogForm) -> HttpResponse:
        """Re-render the dialog in place with the form errors (HTTP 200)."""
        return self.render_dialog(form)

    def form_valid(self, form: DialogForm) -> HttpResponse:
        """Run ``perform`` atomically; a ``DialogError`` rolls back and re-renders."""
        try:
            with transaction.atomic():
                self.perform(form)
        except DialogError as error:
            form.add_error(None, error.message)
            return self.form_invalid(form)
        return self.success_response()

    def perform(self, form: DialogForm) -> None:
        """Do the work for a valid ``form``; raise ``DialogError`` to refuse."""
        raise NotImplementedError("DialogView subclasses must implement perform().")

    def get_success_url(self) -> str | None:
        """A same-host ``next``, else ``success_url``/``success_url_name``."""
        fallback = self.success_url
        if fallback is None and self.success_url_name:
            fallback = reverse(self.success_url_name)
        return safe_next_url(self.request, fallback)

    def get_success_message(self) -> str:
        """Flash message queued on success (empty for none)."""
        return str(self.success_message)

    def get_done_payload(self) -> Mapping[str, Any]:
        """Data carried by the ``dialog:done`` event."""
        return {}

    def success_response(self) -> HttpResponse:
        """The ``dialog_success`` response for this view's settings."""
        return dialog_success(
            self.request,
            redirect_url=self.get_success_url(),
            message=self.get_success_message(),
            payload=self.get_done_payload(),
            refresh=self.refresh_on_success,
        )

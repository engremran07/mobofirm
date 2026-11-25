"""Row/detail/list/submit-line actions and display badges for the staff console.

Apps never import this module directly; they use ``apps.core.admin_base``.

``@action`` (also valid for plain bulk actions)
    Wraps the function, records the metadata below on the wrapper and, for a
    ``dialog=`` action, runs the dialog flow: **GET renders the dialog form and never
    mutates**; **POST** (CSRF is enforced by the admin view) validates the form and
    only then calls ``func(model_admin, request, form, *args, **kwargs)``. An invalid
    POST re-renders the dialog with the errors. ``permissions`` entries are model
    admin permission names (``"change"`` -> ``has_change_permission``) or full
    ``app.codename`` permissions; ``dialog=`` without ``permissions=`` is refused at
    import time because such an endpoint would write without any permission check.
    Row/detail/list actions without a dialog are navigation-only (redirects); they
    must not write.

    Wrapper attributes: ``allowed_permissions``,
    ``short_description``, ``url_path``, ``icon``, ``dialog``, ``variant``, ``attrs``,
    ``extra_options``, ``original_function_name``. ``icon`` is a lucide icon name;
    Material-style names (``rate_review``) are translated through ``ICON_ALIASES``.

    A dialog action that answers with ``htmx_redirect(url)`` (``HX-Redirect`` header)
    is turned into an ordinary 302 when the request did not come from HTMX, so a
    successful browser POST always ends with POST-Redirect-GET.

Template contract (``changelist_view`` / ``changeform_view`` ``extra_context``)
    * ``actions_list`` / ``actions_detail``: list of dicts ``{"title", "icon",
      "variant" (ActionVariant; use ``variant.value``), "attrs" (dict), "dialog"
      (truthy => open as a dialog), "path"}`` or dropdown dicts ``{"title", "icon",
      "variant", "items": [dict, ...]}``. ``actions_detail`` only exists on
      change pages of existing objects.
    * ``actions_row``: list of ``{"title", "icon", "variant", "attrs", "dialog",
      "display_in_dropdown", "raw_path"}``; the URL is ``{% url raw_path obj.pk %}``.
    * ``actions_submit_line``: list of ``ConsoleAction`` (``action_name``,
      ``description``, ``icon``, ``variant``, ``attrs``); render a submit button
      named ``action_name``; the action runs after the object is saved.
    * ``actions_list_hide_default`` / ``actions_detail_hide_default`` booleans.

Dialog template: ``ACTION_DIALOG_TEMPLATE`` (``console/crud/action_dialog.html``)
    context ``dialog`` (the ``confirm_dialog`` mapping: ``title``, ``description``,
    ``form_class``, ``form_submit_text``), ``title``, ``description``, ``form`` (a
    ``BaseDialogForm``; render all of it, it carries the hidden ``_form_submitted``
    field), ``form_submit_text``, ``variant``, ``action_url`` (POST target, the
    request path) and ``is_htmx`` (fragment vs. full page).

``@display``
    ``label=`` turns the cell into a badge rendered by
    ``console/components/badge.html`` with context ``{"label": text, "tone": tone,
    "value": value}``; ``tone`` is one of ``BADGE_TONES``. The function returns the
    value, or ``(value, text)``; ``label`` is ``True`` (neutral), a tone string, or a
    mapping value -> tone. Everything is escaped by the template engine.
    ``header=True`` renders ``(title, subtitle)`` as a two-line cell.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from functools import update_wrapper, wraps
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from django.contrib import admin
from django.contrib.admin.options import BaseModelAdmin
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.core.admin_forms import BaseDialogForm

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise

__all__ = [
    "ACTION_DIALOG_TEMPLATE",
    "BADGE_TEMPLATE",
    "BADGE_TONES",
    "ICON_ALIASES",
    "ActionDialog",
    "ActionVariant",
    "ConsoleAction",
    "ConsoleActionsMixin",
    "action",
    "display",
    "lucide_icon",
    "render_badge",
]

ACTION_DIALOG_TEMPLATE = "console/crud/action_dialog.html"
BADGE_TEMPLATE = "console/components/badge.html"

BADGE_TONES = frozenset({"default", "primary", "success", "info", "warning", "danger"})

_STANDARD_PERMISSIONS = frozenset({"add", "change", "delete", "view"})
_EMPTY = "—"


class ActionVariant(StrEnum):
    DEFAULT = "default"
    PRIMARY = "primary"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    INFO = "info"


class ActionDialog(TypedDict):
    title: StrOrPromise
    description: NotRequired[StrOrPromise | None]
    form_class: NotRequired[type[BaseDialogForm] | None]
    form_submit_text: NotRequired[StrOrPromise | None]


# Material-style icon names used by existing apps -> lucide names.
ICON_ALIASES: Mapping[str, str] = {
    "add": "plus",
    "bar_chart": "chart-column",
    "block": "ban",
    "cancel": "circle-x",
    "check_circle": "circle-check",
    "close": "x",
    "content_copy": "copy",
    "edit_note": "notebook-pen",
    "fact_check": "clipboard-check",
    "inventory_2": "package",
    "key_off": "key-round",
    "lock_open": "lock-open",
    "monitor_heart": "heart-pulse",
    "monitoring": "activity",
    "notifications_active": "bell-ring",
    "archive": "folder-archive",
    "paid": "badge-dollar-sign",
    "payments": "banknote",
    "person_off": "user-round-x",
    "play_arrow": "play",
    "play_circle": "circle-play",
    "publish": "upload",
    "rate_review": "message-square-text",
    "replay": "rotate-ccw",
    "report": "flag",
    "restart_alt": "rotate-ccw",
    "schedule": "clock",
    "stop_circle": "circle-stop",
    "swap_vert": "arrow-up-down",
    "sync": "refresh-cw",
    "task_alt": "circle-check-big",
    "travel_explore": "globe",
    "tune": "sliders-horizontal",
    "undo": "undo-2",
    "unpublished": "circle-slash",
    "verified": "badge-check",
    "verified_user": "shield-check",
    "visibility": "eye",
    "visibility_off": "eye-off",
    "wifi_tethering": "radio-tower",
}


def lucide_icon(name: str | None) -> str | None:
    """A lucide icon name: aliases translated, ``snake_case`` turned into ``kebab-case``."""
    if not name:
        return None
    return ICON_ALIASES.get(name, name.replace("_", "-"))


# --------------------------------------------------------------------------- display
def _check_tone(tone: str) -> str:
    if tone not in BADGE_TONES:
        raise ImproperlyConfigured(
            f"Unknown badge tone {tone!r}; use one of {sorted(BADGE_TONES)}."
        )
    return tone


def render_badge(
    raw: object, label: bool | str | Mapping[Any, str] | None = True
) -> SafeString | None:
    """Render a display value as a badge; ``None``/empty values render nothing."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, tuple):
        value, text = raw
    else:
        value = text = raw
    if label is None or label is True or label is False:
        tone = "default"
    elif isinstance(label, str):
        tone = label
    else:
        tone = label.get(value, "default")
    return SafeString(
        render_to_string(
            BADGE_TEMPLATE, {"label": str(text), "tone": tone, "value": str(value)}
        )
    )


def _render_header(raw: Any) -> SafeString | str | None:
    if raw is None or raw == "":
        return None
    parts = [raw] if isinstance(raw, str) else list(raw)
    title = parts[0]
    subtitle = parts[1] if len(parts) > 1 and parts[1] else ""
    return format_html(
        '<div class="console-cell-header"><span class="console-cell-header__title">'
        "{}</span>{}</div>",
        title,
        format_html('<span class="console-cell-header__subtitle">{}</span>', subtitle)
        if subtitle
        else "",
    )


def display(
    function: Callable[..., Any] | None = None,
    *,
    boolean: bool | None = None,
    ordering: Any = None,
    description: StrOrPromise | None = None,
    empty_value: str | None = None,
    label: bool | str | Mapping[Any, str] | None = None,
    header: bool | None = None,
) -> Callable[..., Any]:
    """Like ``django.contrib.admin.display`` plus ``label=`` badges and ``header=``."""
    if boolean is not None and empty_value is not None:
        raise ValueError(
            "The boolean and empty_value arguments to @display are mutually exclusive."
        )
    if isinstance(label, str):
        _check_tone(label)
    elif isinstance(label, Mapping):
        for tone in label.values():
            _check_tone(tone)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        target = func
        if label is not None:

            @wraps(func)
            def badge(*args: Any, **kwargs: Any) -> Any:
                return render_badge(func(*args, **kwargs), label)

            target = badge
        elif header:

            @wraps(func)
            def two_line(*args: Any, **kwargs: Any) -> Any:
                return _render_header(func(*args, **kwargs))

            target = two_line
        options: dict[str, Any] = {
            "boolean": boolean,
            "ordering": ordering,
            "description": description,
            "empty_value": empty_value,
        }
        decorated = admin.display(**options)(target)
        extras: dict[str, Any] = {}
        if label is not None:
            extras["label"] = label
        if header is not None:
            extras["header"] = header
        decorated.__dict__.update(extras)
        return decorated

    if function is None:
        return decorator
    return decorator(function)


# --------------------------------------------------------------------------- actions
def _permission_allowed(
    model_admin: BaseModelAdmin,
    request: HttpRequest,
    permission: str,
    *,
    object_id: object,
    pass_object: bool,
) -> bool:
    if "." in permission:
        return bool(request.user.has_perm(permission))
    rule = getattr(model_admin, f"has_{permission}_permission")
    # The four stock rules take a model *instance* as ``obj``; only project-defined
    # rules (``has_adjust_permission(request, _object_id)``) receive the object id.
    if pass_object and permission not in _STANDARD_PERMISSIONS:
        return bool(rule(request, object_id))
    return bool(rule(request))


def _dialog_response(
    request: HttpRequest,
    dialog: ActionDialog,
    form: BaseDialogForm,
    variant: ActionVariant,
) -> HttpResponse:
    # Imported here: the template tag module needs the app registry (see MobofirmAdminSite.each_context).
    from apps.core.templatetags.console_ui import console_layout

    return render(
        request,
        ACTION_DIALOG_TEMPLATE,
        {
            # `render()` bypasses AdminSite.each_context, so choose the layout here: an HTMX dialog request must
            # get the content-only fragment, never the whole shell.
            "console_layout": console_layout(request),
            "dialog": dialog,
            "title": dialog["title"],
            "description": dialog.get("description") or "",
            "form": form,
            "form_submit_text": dialog.get("form_submit_text") or "",
            "variant": variant,
            "action_url": request.path,
            "is_htmx": request.headers.get("HX-Request") == "true",
        },
    )


def _post_redirect_get(request: HttpRequest, response: object) -> object:
    """Plain browser POSTs finish with a 302 instead of the HTMX-only header."""
    if (
        isinstance(response, HttpResponse)
        and request.headers.get("HX-Request") != "true"
        and (target := response.headers.get("HX-Redirect"))
    ):
        return HttpResponseRedirect(target)
    return response


def action(
    function: Callable[..., Any] | None = None,
    *,
    permissions: Iterable[str] | None = None,
    description: StrOrPromise | None = None,
    url_path: str | None = None,
    attrs: dict[str, Any] | None = None,
    icon: str | None = None,
    variant: ActionVariant | None = ActionVariant.DEFAULT,
    dialog: ActionDialog | None = None,
    extra_options: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    """See the module docstring."""
    required = tuple(permissions) if permissions is not None else None
    if dialog is not None and not required:
        raise ImproperlyConfigured(
            "An action with a dialog writes data and must declare permissions=[...]."
        )
    chosen_variant = variant or ActionVariant.DEFAULT

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def inner(
            model_admin: BaseModelAdmin, request: HttpRequest, *args: Any, **kwargs: Any
        ) -> Any:
            if required:
                object_id = kwargs.get("object_id")
                pass_object = (
                    isinstance(model_admin, ConsoleActionsMixin)
                    and func.__name__ in model_admin.action_names_with_object()
                )
                if not all(
                    _permission_allowed(
                        model_admin,
                        request,
                        permission,
                        object_id=object_id,
                        pass_object=pass_object,
                    )
                    for permission in required
                ):
                    raise PermissionDenied
            if dialog is not None:
                form_class = dialog.get("form_class") or BaseDialogForm
                is_post = request.method == "POST"
                form = form_class(
                    data=request.POST if is_post else None,
                    request=request,
                    object_id=kwargs.get("object_id"),
                )
                if is_post and form.is_valid():
                    return _post_redirect_get(
                        request, func(model_admin, request, form, *args, **kwargs)
                    )
                return _dialog_response(request, dialog, form, chosen_variant)
            return func(model_admin, request, *args, **kwargs)

        metadata: dict[str, Any] = {
            "variant": chosen_variant,
            "extra_options": dict(extra_options or {}),
            "attrs": dict(attrs or {}),
            "original_function_name": func.__name__,
        }
        if required is not None:
            metadata["allowed_permissions"] = required
        if description is not None:
            metadata["short_description"] = description
        if url_path is not None:
            metadata["url_path"] = url_path
        if icon is not None:
            metadata["icon"] = lucide_icon(icon)
        if dialog is not None:
            metadata["dialog"] = dialog
        inner.__dict__.update(metadata)
        return inner

    if function is None:
        return decorator
    return decorator(function)


# ----------------------------------------------------------------------- the mixin
@dataclass
class ConsoleAction:
    """One resolved ``actions_*`` entry (also what ``actions_submit_line`` holds)."""

    action_name: str  # "<app>_<model>_<method>"; also the URL name and button name
    method: Callable[..., Any]
    description: str
    path: str
    attrs: dict[str, Any] | None = None
    object_id: int | str | None = None
    icon: str | None = None
    variant: ActionVariant | None = ActionVariant.DEFAULT
    dialog: ActionDialog | None = None
    extra_options: dict[str, Any] = field(default_factory=dict)


ActionLayout = Sequence[str | Mapping[str, Any]]


class ConsoleActionsMixin(admin.ModelAdmin):
    """``actions_list`` / ``actions_row`` / ``actions_detail`` / ``actions_submit_line``.

    Each attribute lists method names decorated with :func:`action`. ``actions_list``
    and ``actions_detail`` entries may also be ``{"title", "icon", "variant", "items":
    [names]}`` dicts, which render as a dropdown. Every action gets a URL named
    ``admin:<app>_<model>_<method>`` (``url_path`` overrides the path segment),
    registered *before* the stock URLs so the object catch-all cannot shadow it.
    Entries the user lacks permission for are neither rendered nor reachable.
    """

    actions_list: ActionLayout = ()
    actions_list_hide_default = False
    actions_row: ActionLayout = ()
    actions_detail: ActionLayout = ()
    actions_detail_hide_default = False
    actions_submit_line: ActionLayout = ()

    # -- name helpers ---------------------------------------------------------
    @staticmethod
    def extract_action_names(layout: ActionLayout) -> list[str]:
        names: list[str] = []
        for entry in layout or ():
            if isinstance(entry, Mapping):
                names.extend(entry.get("items", ()))
            else:
                names.append(entry)
        return names

    def action_names_with_object(self) -> list[str]:
        """Actions whose custom permission rules receive the object id."""
        return [
            *self.extract_action_names(self.actions_detail),
            *self.extract_action_names(self.actions_submit_line),
        ]

    def get_console_action(self, name: str) -> ConsoleAction:
        method = getattr(self, name, None)
        if not callable(method):
            raise ImproperlyConfigured(
                f"{type(self).__name__}: action {name!r} is not a callable method."
            )
        opts = self.model._meta
        return ConsoleAction(
            action_name=f"{opts.app_label}_{opts.model_name}_{name}",
            method=method,
            description=str(
                getattr(method, "short_description", name.replace("_", " ").title())
            ),
            path=getattr(method, "url_path", name),
            attrs=getattr(method, "attrs", None),
            icon=getattr(method, "icon", None),
            variant=getattr(method, "variant", None),
            dialog=getattr(method, "dialog", None),
            extra_options=getattr(method, "extra_options", {}),
        )

    def _console_resolve(self, layout: ActionLayout) -> list[ConsoleAction]:
        return [self.get_console_action(n) for n in self.extract_action_names(layout)]

    def _console_allowed(
        self,
        request: HttpRequest,
        actions: list[ConsoleAction],
        object_id: int | str | None = None,
    ) -> list[ConsoleAction]:
        """Drop actions the user may not run (same rules the decorator enforces)."""
        pass_object = object_id is not None
        allowed: list[ConsoleAction] = []
        for item in actions:
            permissions = getattr(item.method, "allowed_permissions", ())
            if all(
                _permission_allowed(
                    self,
                    request,
                    permission,
                    object_id=object_id,
                    pass_object=pass_object,
                )
                for permission in permissions
            ):
                allowed.append(item)
        return allowed

    def get_actions_list(self, request: HttpRequest) -> list[ConsoleAction]:
        return self._console_allowed(request, self._console_resolve(self.actions_list))

    def get_actions_row(self, request: HttpRequest) -> list[ConsoleAction]:
        return self._console_allowed(request, self._console_resolve(self.actions_row))

    def get_actions_detail(
        self, request: HttpRequest, object_id: int | str
    ) -> list[ConsoleAction]:
        return self._console_allowed(
            request, self._console_resolve(self.actions_detail), object_id
        )

    def get_actions_submit_line(
        self, request: HttpRequest, object_id: int | str | None
    ) -> list[ConsoleAction]:
        return self._console_allowed(
            request, self._console_resolve(self.actions_submit_line), object_id
        )

    # -- navigation structures for the templates --------------------------------
    def _console_navigation(
        self,
        layout: ActionLayout,
        allowed: list[ConsoleAction],
        object_id: int | str | None = None,
    ) -> list[dict[str, Any]]:
        opts = self.model._meta
        by_name = {a.action_name: a for a in allowed}

        def lookup(name: str) -> ConsoleAction | None:
            return by_name.get(f"{opts.app_label}_{opts.model_name}_{name}")

        def describe(item: ConsoleAction) -> dict[str, Any]:
            route = f"{self.admin_site.name}:{item.action_name}"
            return {
                "title": item.description,
                "icon": item.icon,
                "variant": item.variant,
                "attrs": item.attrs or {},
                "dialog": item.dialog,
                "path": reverse(route, args=(object_id,) if object_id else ()),
            }

        navigation: list[dict[str, Any]] = []
        for entry in layout or ():
            if isinstance(entry, str):
                if (item := lookup(entry)) is not None:
                    navigation.append(describe(item))
                continue
            children = [
                describe(item)
                for name in entry.get("items", ())
                if (item := lookup(name)) is not None
            ]
            if children:
                navigation.append(
                    {
                        "title": entry["title"],
                        "icon": lucide_icon(entry.get("icon")),
                        "variant": entry.get("variant", ActionVariant.DEFAULT),
                        "items": children,
                    }
                )
        return navigation

    # -- views --------------------------------------------------------------------
    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        extra_context = dict(extra_context or {})
        rows = [
            {
                "title": item.description,
                "icon": item.icon,
                "variant": item.variant,
                "attrs": deepcopy(item.attrs or {}),
                "dialog": item.dialog,
                "display_in_dropdown": item.extra_options.get(
                    "display_in_dropdown", True
                ),
                "raw_path": f"{self.admin_site.name}:{item.action_name}",
            }
            for item in self.get_actions_row(request)
        ]
        extra_context.update(
            actions_list_hide_default=self.actions_list_hide_default,
            actions_list=self._console_navigation(
                self.actions_list, self.get_actions_list(request)
            ),
            actions_row=rows,
        )
        return super().changelist_view(request, extra_context)

    def changeform_view(
        self,
        request: HttpRequest,
        object_id: str | None = None,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        extra_context = dict(extra_context or {})
        if object_id:
            extra_context.update(
                actions_detail_hide_default=self.actions_detail_hide_default,
                actions_submit_line=self.get_actions_submit_line(request, object_id),
                actions_detail=self._console_navigation(
                    self.actions_detail,
                    self.get_actions_detail(request, object_id),
                    object_id,
                ),
            )
        return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(
        self, request: HttpRequest, obj: Any, form: Any, change: bool
    ) -> None:
        """Save, then run the submit-line action whose button was pressed."""
        super().save_model(request, obj, form, change)
        for item in self.get_actions_submit_line(request, obj.pk):
            if item.action_name in request.POST:
                item.method(request, obj)

    # -- URLs -----------------------------------------------------------------------
    def get_urls(self) -> list[URLPattern]:
        def wrap(view: Callable[..., Any]) -> Callable[..., Any]:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return self.admin_site.admin_view(view)(*args, **kwargs)

            wrapper.__dict__["model_admin"] = self
            return update_wrapper(wrapper, view)

        def route(prefix: str, item: ConsoleAction) -> URLPattern:
            segment = item.path.removesuffix("/")
            return path(f"{prefix}{segment}/", wrap(item.method), name=item.action_name)

        rows = [
            route("<path:object_id>/", a)
            for a in self._console_resolve(self.actions_row)
        ]
        lists = [route("", a) for a in self._console_resolve(self.actions_list)]
        details = [
            route("<path:object_id>/", a)
            for a in self._console_resolve(self.actions_detail)
        ]
        return [*rows, *lists, *details, *super().get_urls()]

    # -- system checks ----------------------------------------------------------------
    def check(self, **kwargs: Any) -> list[Any]:
        errors = list(super().check(**kwargs))
        for attr in (
            "actions_list",
            "actions_row",
            "actions_detail",
            "actions_submit_line",
        ):
            for name in self.extract_action_names(getattr(self, attr)):
                method = getattr(self, name, None)
                if not callable(method):
                    errors.append(
                        checks.Error(
                            f"{attr} refers to {name!r}, which is not a method.",
                            obj=type(self),
                            id="console.E001",
                        )
                    )
                    continue
                for permission in getattr(method, "allowed_permissions", ()):
                    if "." not in permission and not hasattr(
                        self, f"has_{permission}_permission"
                    ):
                        errors.append(
                            checks.Error(
                                f"Action {name!r} needs has_{permission}_permission().",
                                obj=type(self),
                                id="console.E002",
                            )
                        )
        return errors

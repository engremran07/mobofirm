from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib.admin import AdminSite
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, URLResolver, reverse
from django.utils.decorators import method_decorator
from django.utils.functional import SimpleLazyObject
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache


class MobofirmAdminSite(AdminSite):
    """Django admin whose sign-in goes through the project's own login flow.

    The stock admin login form would bypass the login rate limiter, reCAPTCHA,
    device policy and MFA enforcement implemented by ``users:account_login``. The
    admin therefore never authenticates anyone itself: anonymous visitors are sent
    to the project login (keeping a safe ``next``), and signed-in users who are not
    staff get a 403 instead of a redirect loop.

    Every admin template also receives the shell data of ``admin_shell.shell_context``
    (``console_brand``, ``sidebar_navigation``, ``console_palette_items``,
    ``console_account_links``; computed lazily, only when the layout renders them),
    ``console_branding`` (see ``admin_branding``), ``console_tabs`` and
    ``console_layout`` (the full shell, or the content-only fragment for HTMX requests).
    The index view merges the dashboard page data from
    ``admin_dashboard.dashboard_callback`` into its context.
    """

    enable_nav_sidebar = False

    @method_decorator(never_cache)
    def login(
        self, request: HttpRequest, extra_context: dict[str, object] | None = None
    ) -> HttpResponse:
        destination = self._safe_next(request)
        if self.has_permission(request):
            return HttpResponseRedirect(destination)
        if request.user.is_authenticated:
            raise PermissionDenied
        query = urlencode({"next": destination})
        return HttpResponseRedirect(f"{reverse('users:account_login')}?{query}")

    def _safe_next(self, request: HttpRequest) -> str:
        candidate = request.POST.get("next") or request.GET.get("next") or ""
        if candidate and url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return candidate
        return reverse("admin:index", current_app=self.name)

    def get_urls(self) -> list[URLPattern | URLResolver]:
        # Imported here: these modules import models, which are not ready while the
        # site class itself is being loaded.
        from apps.core import admin_dashboard, admin_shell

        return [
            *admin_dashboard.dashboard_urls(self),
            *admin_shell.shell_urls(self),
            *super().get_urls(),
        ]

    def each_context(self, request: HttpRequest) -> dict[str, Any]:
        from apps.core import admin_branding, admin_navigation, admin_shell
        from apps.core.templatetags.console_ui import console_layout

        context = super().each_context(request)
        # Full shell, or the content-only fragment for HTMX requests (see admin/base.html).
        context["console_layout"] = console_layout(request)
        # One lazily computed bundle feeds the layout's brand, sidebar and palette.
        shell = SimpleLazyObject(lambda: admin_shell.shell_context(request))

        def lazy_part(key: str) -> SimpleLazyObject:
            return SimpleLazyObject(lambda: shell[key])

        for key in (
            "console_brand",
            "sidebar_navigation",
            "console_palette_items",
            "console_account_links",
        ):
            context[key] = lazy_part(key)
        branding = admin_branding.console_branding(request)
        context.update(
            console_branding=branding,
            console_tabs=SimpleLazyObject(lambda: admin_navigation.build_tabs(request)),
            # Branding is editable site settings, not static class attributes.
            site_title=branding["site_title"],
            site_header=branding["site_header"],
            site_url=branding["site_url"],
        )
        return context

    def index(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> TemplateResponse:
        from apps.core.admin_dashboard import dashboard_callback

        context = dashboard_callback(request, dict(extra_context or {}))
        return super().index(request, context)

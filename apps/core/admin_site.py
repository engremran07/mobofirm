from __future__ import annotations

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from unfold.sites import UnfoldAdminSite


class MobofirmAdminSite(UnfoldAdminSite):
    """Unfold admin whose sign-in goes through the project's own login flow.

    The stock admin login form would bypass the login rate limiter, reCAPTCHA,
    device policy and MFA enforcement implemented by ``users:account_login``. The
    admin therefore never authenticates anyone itself: anonymous visitors are sent
    to the project login (keeping a safe ``next``), and signed-in users who are not
    staff get a 403 instead of a redirect loop.
    """

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

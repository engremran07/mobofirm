"""Dismiss a site banner for this browser (a short-lived first-party cookie)."""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.users.models import Announcement
from apps.users.services.announcements import remember_dismissal


@require_POST
def dismiss_announcement(request: HttpRequest, pk: int) -> HttpResponse:
    """Hide one live banner for this browser; HTMX gets an empty fragment."""
    if not Announcement.objects.filter(pk=pk, is_active=True, is_global=True).exists():
        raise Http404("No such banner.")
    target = request.POST.get("next", "")
    safe = url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse("")
    else:
        response = HttpResponseRedirect(target if safe else "/")
    remember_dismissal(request, response, pk)
    return response

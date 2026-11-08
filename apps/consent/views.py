from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from apps.consent.constants import SOURCE_BANNER, SOURCE_PRIVACY_CENTRE
from apps.consent.forms import ConsentChoiceForm
from apps.consent.services import (
    active_policy,
    read_state,
    record_decision,
)


def _safe_next(request: HttpRequest, candidate: str) -> str:
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse("consent:privacy_centre")


@require_GET
def privacy_centre(request: HttpRequest) -> HttpResponse:
    policy = active_policy()
    state = read_state(request)
    return render(
        request,
        "consent/privacy_centre.html",
        {
            "policy": policy,
            "state": state,
            "categories": [
                {"category": c, "granted": c.required or c.slug in state.granted}
                for c in policy.categories
            ],
        },
    )


@require_POST
def save_choice(request: HttpRequest) -> HttpResponse:
    """Banner and privacy centre post here; the browser is redirected back."""
    policy = active_policy()
    form = ConsentChoiceForm(
        request.POST, optional_slugs=[c.slug for c in policy.optional]
    )
    if not form.is_valid():
        messages.error(request, "Your cookie choice could not be saved. Try again.")
        return HttpResponseRedirect(reverse("consent:privacy_centre"))
    data = form.cleaned_data
    response = HttpResponseRedirect(_safe_next(request, data["next"]))
    record_decision(
        request,
        response,
        action=data["action"],
        selected=data["categories"],
        source=SOURCE_BANNER
        if data["source"] == SOURCE_BANNER
        else SOURCE_PRIVACY_CENTRE,
    )
    messages.success(request, "Your cookie choices were saved.")
    return response

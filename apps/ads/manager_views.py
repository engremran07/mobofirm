"""Staff placement manager, discovery queue and test profile (Unfold admin shell).

Every screen renders through ``render_admin_page``; every state change is a POST with CSRF
that redirects back (PRG). Confirmations are app-owned dialogs (``apps.ads.dialogs``).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import NoReverseMatch, reverse
from django.views.decorators.csp import csp_override
from django.views.decorators.http import require_POST

from apps.ads.models import AdPlacement, AutoAdsScanResult, ScanDiscovery
from apps.ads.services import placement_manager, scanner
from apps.ads.services import zones as zone_service
from apps.core.admin_pages import render_admin_page

MANAGE = "ads.change_adplacement"

# Static routes that render a scanned template, for the "Preview with test ads" link.
PREVIEW_ROUTES: dict[str, str] = {
    "firmwares/library_hub.html": "home",
    "firmwares/browse.html": "firmwares:browse",
    "firmwares/brand_list.html": "firmwares:brand_list",
    "firmwares/chipset_list.html": "firmwares:chipset_list",
    "firmwares/flashing_tools.html": "firmwares:flashing_tools",
    "firmwares/flashing_guides.html": "firmwares:flashing_guides",
    "blog/post_list.html": "blog:post_list",
    "shop/pricing.html": "shop:pricing",
    "search/results.html": "search:results",
    "pages/index.html": "pages:index",
}

PAGE_SIZE = 40


def admin_shell(view):
    """Serve ``view`` under the Unfold shell's CSP (its Alpine compiles expressions).

    Mirrors ``apps.core.middleware.admin_csp`` so these pages work without every new
    staff view having to be registered in that central allowlist.
    """
    policy = getattr(settings, "SECURE_CSP_ADMIN", settings.SECURE_CSP)
    return csp_override(policy)(view)


def staff_gate(permission: str):
    """Staff member holding ``permission``; others are sent to sign in or get a 403."""

    def decorator(view):
        return admin_shell(
            staff_member_required(
                permission_required(permission, raise_exception=True)(view),
                login_url="admin:login",
            )
        )

    return decorator


def preview_link(user: object, template_path: str) -> str:
    """Signed test-profile link to the real public route of a template, or ''."""
    route = PREVIEW_ROUTES.get(template_path)
    if route is None:
        return ""
    try:
        path = reverse(route)
    except NoReverseMatch:
        return ""
    return zone_service.preview_url(user, path)


def _nav(active: str) -> dict[str, object]:
    return {"ads_tab": active}


@staff_gate(MANAGE)
def placement_manager_view(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    kind = request.GET.get("type", "").strip()
    state = request.GET.get("state", "").strip()
    if kind not in {value for value, _ in AutoAdsScanResult.TemplateType.choices}:
        kind = ""
    if state not in {"placed", "open", "blocked"}:
        state = ""
    try:
        rows = placement_manager.template_rows(
            search=query, template_type=kind, state=state
        )
    except DatabaseError:
        rows = []
    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    for row in page.object_list:
        row.preview = preview_link(request.user, row.path)
    return render_admin_page(
        request,
        "ads/placement_manager.html",
        title="Placement manager",
        subtitle="Scanned templates and the ad zones they declare. Place an ad without touching template code.",
        context={
            **_nav("placements"),
            "page": page,
            "rows": page.object_list,
            "query": query,
            "type_filter": kind,
            "state_filter": state,
            "types": AutoAdsScanResult.TemplateType.choices,
            "site_wide": placement_manager.site_wide_rows(),
            "profile": zone_service.profile_enabled(request),
            "scan_count": AutoAdsScanResult.objects.count(),
        },
    )


@staff_gate(MANAGE)
@require_POST
def run_scan(request: HttpRequest) -> HttpResponse:
    result = scanner.scan_templates_for_placements()
    if result.get("status") == "success":
        messages.success(
            request,
            f"Scanned {result['scanned']} templates: {result['discoveries']} new "
            f"suggestions, {result['excluded']} excluded.",
        )
    else:
        messages.error(request, "The templates folder could not be scanned.")
    return redirect("ads:placement_manager")


@staff_gate(MANAGE)
@require_POST
def toggle_placement(request: HttpRequest, pk: int) -> HttpResponse:
    placement = get_object_or_404(AdPlacement, pk=pk, is_deleted=False)
    turn_on = request.POST.get("action") == "enable"
    placement.is_enabled = turn_on
    placement.updated_by_id = request.user.pk
    placement.save(update_fields=["is_enabled", "updated_by"])
    messages.success(
        request,
        f"“{placement.name}” is now {'on' if turn_on else 'off'}.",
    )
    return redirect("ads:placement_manager")


@staff_gate(MANAGE)
@require_POST
def set_test_profile(request: HttpRequest) -> HttpResponse:
    turn_on = request.POST.get("state") == "on"
    zone_service.set_test_profile(request, turn_on)
    messages.success(
        request,
        "Test profile on: every public page shows marked test ads for you only."
        if turn_on
        else "Test profile off.",
    )
    return redirect("ads:placement_manager")


@staff_gate(MANAGE)
def discoveries_view(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "pending")
    if status not in {"pending", "approved", "rejected", "all"}:
        status = "pending"
    query = request.GET.get("q", "").strip()
    items = ScanDiscovery.objects.select_related("scan_result", "placement").order_by(
        "scan_result__template_path", "-confidence"
    )
    if status != "all":
        items = items.filter(status=status)
    if query:
        items = items.filter(scan_result__template_path__icontains=query)
    page = Paginator(items, PAGE_SIZE).get_page(request.GET.get("page"))
    counts = {
        value: ScanDiscovery.objects.filter(status=value).count()
        for value in ("pending", "approved", "rejected")
    }
    return render_admin_page(
        request,
        "ads/discoveries.html",
        title="Scan suggestions",
        subtitle="Suggested ad spots found by the template scan. Approve to create a placement.",
        context={
            **_nav("discoveries"),
            "page": page,
            "discoveries": page.object_list,
            "status": status,
            "query": query,
            "status_tabs": [
                ("pending", "Pending", counts["pending"]),
                ("approved", "Approved", counts["approved"]),
                ("rejected", "Rejected", counts["rejected"]),
                ("all", "All", None),
            ],
        },
    )


@staff_gate(MANAGE)
@require_POST
def approve_discovery(request: HttpRequest, pk: int) -> HttpResponse:
    result = scanner.approve_discovery(pk, request.user)
    if result["status"] == "success":
        messages.success(request, "Suggestion approved; the placement is ready.")
    else:
        messages.error(request, "That suggestion is no longer pending.")
    return redirect("ads:discoveries")

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.bounty import services, targets
from apps.bounty.forms import ClaimForm, PostBountyForm, ReportForm
from apps.bounty.models import Bounty
from apps.users.request_user import current_user

STATUS_FILTERS = (
    ("open", "Open"),
    ("fulfilled", "Fulfilled"),
    ("all", "All"),
)
KIND_FILTERS = (
    ("", "Everything"),
    (targets.KIND_REQUEST, "Requests"),
    (targets.KIND_MODEL, "Models"),
    (targets.KIND_VARIANT, "Variants"),
    (targets.KIND_GUIDE, "Guides"),
)


@require_GET
def bounty_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "open")
    kind = request.GET.get("kind", "")
    query = request.GET.get("q", "").strip()[:100]
    bounties = Bounty.objects.select_related("creator").annotate(
        claim_count=Count("claims")
    )
    if status == "open":
        bounties = bounties.filter(status=Bounty.Status.OPEN)
    elif status == "fulfilled":
        bounties = bounties.filter(status=Bounty.Status.FULFILLED)
    else:
        status = "all"
        bounties = bounties.exclude(status=Bounty.Status.REVIEW)
    if kind in targets.TARGET_MODELS:
        bounties = bounties.filter(target_type=targets.content_type_for(kind))
    if query:
        bounties = bounties.filter(
            Q(target_label__icontains=query) | Q(note__icontains=query)
        )
    available, reason = services.availability()
    page = Paginator(bounties.order_by("-credits", "-created_at"), 20).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "bounty/list.html",
        {
            "page_obj": page,
            "status": status,
            "kind": kind,
            "query": query,
            "status_filters": STATUS_FILTERS,
            "kind_filters": KIND_FILTERS,
            "available": available,
            "paused_reason": reason,
        },
    )


@require_GET
def bounty_detail(request: HttpRequest, pk: int) -> HttpResponse:
    bounty = get_object_or_404(
        Bounty.objects.select_related("creator", "settled_to", "target_type"), pk=pk
    )
    user = request.user
    is_owner = user.is_authenticated and user.pk == bounty.creator_id
    claims = list(bounty.claims.select_related("user"))
    available, reason = services.availability()
    target_url = ""
    kind = targets.kind_of(bounty.target_type)
    try:
        target_url = targets.resolve(kind, bounty.target_id).url
    except targets.TargetError:
        target_url = ""
    is_guide = kind == targets.KIND_GUIDE
    upload_url = (
        f"{reverse('blog:post_create')}?solves={bounty.target_id}"
        if is_guide
        else reverse("firmwares:upload")
    )
    return render(
        request,
        "bounty/detail.html",
        {
            "bounty": bounty,
            "is_owner": is_owner,
            "claims": claims,
            "has_claimed": user.is_authenticated
            and any(claim.user_id == user.pk for claim in claims),
            "can_cancel": is_owner and bounty.status in Bounty.UNSETTLED,
            "can_report": user.is_authenticated and bounty.status in Bounty.UNSETTLED,
            "target_url": target_url,
            "is_guide": is_guide,
            "upload_url": upload_url,
            "available": available,
            "paused_reason": reason,
            "claim_form": ClaimForm(),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def bounty_create(request: HttpRequest) -> HttpResponse:
    requested = request.POST.get("kind") or next(
        (kind for kind in targets.TARGET_MODELS if request.GET.get(kind, "").isdigit()),
        targets.KIND_REQUEST,
    )
    available, reason = services.availability(
        requested if requested in targets.TARGET_MODELS else targets.KIND_REQUEST
    )
    target = None
    if request.method == "POST":
        form = PostBountyForm(request.POST)
        if not available:
            messages.error(request, reason)
        elif form.is_valid():
            data = form.cleaned_data
            try:
                bounty = services.post_bounty(
                    request.user,
                    kind=data["kind"],
                    object_id=data["object_id"],
                    amount=data["credits"],
                    days=data["days"],
                    note=data["note"],
                )
            except services.NotEnoughCredits as error:
                form.add_error("credits", str(error))
            except services.BountyError as error:
                form.add_error(None, str(error))
            else:
                messages.success(
                    request, f"Bounty posted: {bounty.credits} credits are in escrow."
                )
                return redirect("bounty:detail", pk=bounty.pk)
    else:
        initial = {}
        for kind in targets.TARGET_MODELS:
            raw = request.GET.get(kind, "")
            if raw.isdigit():
                initial = {"kind": kind, "object_id": int(raw)}
        form = PostBountyForm(initial=initial)
    kind = form["kind"].value()
    object_id = form["object_id"].value()
    if kind in targets.TARGET_MODELS and str(object_id or "").isdigit():
        try:
            target = targets.resolve(str(kind), int(object_id))
        except targets.TargetError as error:
            form.add_error(None, str(error))
    return render(
        request,
        "bounty/create.html",
        {
            "form": form,
            "target": target,
            "available": available,
            "paused_reason": reason,
        },
    )


@login_required
@require_POST
def bounty_claim(request: HttpRequest, pk: int) -> HttpResponse:
    bounty = get_object_or_404(Bounty, pk=pk)
    form = ClaimForm(request.POST)
    try:
        services.claim(bounty, request.user, form.data.get("note", ""))
    except services.BountyError as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            "You are listed as working on this. Upload the firmware; staff approval pays you.",
        )
    return redirect("bounty:detail", pk=pk)


@login_required
@require_http_methods(["GET", "POST"])
def bounty_report(request: HttpRequest, pk: int) -> HttpResponse:
    bounty = get_object_or_404(Bounty, pk=pk)
    form = ReportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.report_problem(bounty, request.user, form.cleaned_data["reason"])
        except services.BountyError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Thanks. Staff will review your report.")
            return redirect("bounty:detail", pk=pk)
    return render(request, "bounty/report.html", {"bounty": bounty, "form": form})


@login_required
@require_GET
def my_bounties(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    posted = Bounty.objects.filter(creator=user)
    claimed = Bounty.objects.filter(claims__user=user).distinct()
    won = Bounty.objects.filter(settled_to=user)
    return render(
        request,
        "bounty/mine.html",
        {"posted": posted[:50], "claimed": claimed[:50], "won": won[:50]},
    )

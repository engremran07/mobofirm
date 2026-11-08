"""Public pages for guide requests: list, request form, detail and "my requests"."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.forms import ModelChoiceField
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from apps.blog import bounty_gateway, guide_requests
from apps.blog.access import blog_enabled_for, blog_settings
from apps.blog.forms import GuideRequestForm
from apps.blog.models import GuideRequest, Post, PostStatus

PAGE_SIZE = 15
STATUS_FILTERS = (("open", "Open"), ("fulfilled", "Fulfilled"), ("all", "All"))


def _require_blog(request: HttpRequest) -> None:
    if not blog_enabled_for(request.user):
        raise Http404("The blog is disabled.")


def can_review(user: Any) -> bool:
    """Staff holding the review permission may see every request."""
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_staff", False)
        and user.has_perm("blog.change_guiderequest")
    )


@require_GET
def request_list(request: HttpRequest) -> HttpResponse:
    _require_blog(request)
    status = request.GET.get("status", "open")
    kind = request.GET.get("kind", "")
    query = request.GET.get("q", "").strip()[:100]
    rows = GuideRequest.objects.filter(status__in=GuideRequest.PUBLIC).select_related(
        "brand", "firmware_model", "chipset"
    )
    if status == "open":
        rows = rows.filter(status=GuideRequest.Status.APPROVED)
    elif status == "fulfilled":
        rows = rows.filter(status=GuideRequest.Status.FULFILLED)
    else:
        status = "all"
    if kind in GuideRequest.Kind.values:
        rows = rows.filter(kind=kind)
    if query:
        rows = rows.filter(Q(title__icontains=query) | Q(description__icontains=query))
    rows = rows.annotate(
        solution_count=Count(
            "solutions",
            filter=Q(solutions__status=PostStatus.PUBLISHED),
            distinct=True,
        )
    )
    page = Paginator(rows.order_by("-created_at", "-pk"), PAGE_SIZE).get_page(
        request.GET.get("page") or 1
    )
    return render(
        request,
        "blog/requests/list.html",
        {
            "page_obj": page,
            "status": status,
            "kind": kind,
            "query": query,
            "status_filters": STATUS_FILTERS,
            "kind_filters": GuideRequest.Kind.choices,
            "requests_enabled": blog_settings().allow_guide_requests,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def request_create(request: HttpRequest) -> HttpResponse:
    _require_blog(request)
    config = blog_settings()
    form = GuideRequestForm(request.POST or None)
    duplicates: list[guide_requests.Duplicate] = []
    if not config.allow_guide_requests:
        messages.info(request, "Guide requests are switched off right now.")
    elif request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            created = guide_requests.create_request(
                request.user,
                title=data["title"],
                description=data["description"],
                kind=data["kind"],
                brand=data["brand"],
                firmware_model=data["model"],
                chipset=data["chipset"],
            )
        except guide_requests.GuideRequestError as error:
            form.add_error(None, str(error))
            duplicates = guide_requests.find_duplicates(
                data["title"], brand=data["brand"], firmware_model=data["model"]
            )
        else:
            messages.success(
                request,
                "Request sent. Staff review every request first; you will be notified.",
            )
            return redirect("blog:request_detail", pk=created.pk)
    labels = {
        "brand_label": _label(form, "brand"),
        "model_label": _label(form, "model"),
        "chipset_label": _label(form, "chipset"),
    }
    return render(
        request,
        "blog/requests/form.html",
        {
            "form": form,
            "duplicates": duplicates,
            "requests_enabled": config.allow_guide_requests,
            **labels,
        },
    )


def _label(form: GuideRequestForm, name: str) -> str:
    field = form.fields[name]
    value = form[name].value()
    if not isinstance(field, ModelChoiceField) or field.queryset is None:
        return ""
    if not value or not str(value).isdigit():
        return ""
    chosen = field.queryset.filter(pk=int(value)).first()
    if chosen is None:
        return ""
    return str(getattr(chosen, "display_name", "") or chosen)


@require_GET
def request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    _require_blog(request)
    item = get_object_or_404(
        GuideRequest.objects.select_related(
            "requester", "brand", "firmware_model", "chipset", "fulfilled_by_post"
        ),
        pk=pk,
    )
    user = request.user
    is_owner = user.is_authenticated and user.pk == item.requester_id
    reviewer = can_review(user)
    if not (item.is_public or is_owner or reviewer):
        raise Http404
    solutions = item.solutions.select_related("author").order_by("-created_at")
    if not reviewer:
        visible = Q(status=PostStatus.PUBLISHED)
        if user.is_authenticated:
            visible |= Q(author_id=user.pk)
        solutions = solutions.filter(visible)
    can_solve = guide_requests.accepts_solution(user, item)
    offer = bounty_gateway.offer_for(item.pk) if item.is_public else bounty_gateway.NONE
    return render(
        request,
        "blog/requests/detail.html",
        {
            "item": item,
            "is_owner": is_owner,
            "can_review": reviewer,
            "solutions": list(solutions),
            "can_solve": can_solve,
            "solve_url": f"{reverse('blog:post_create')}?solves={item.pk}",
            "can_cancel": (is_owner or reviewer)
            and item.status
            in (GuideRequest.Status.PENDING, GuideRequest.Status.APPROVED),
            "offer": offer,
            "signed_in": user.is_authenticated,
        },
    )


@login_required
@require_GET
def request_mine(request: HttpRequest) -> HttpResponse:
    _require_blog(request)
    mine = GuideRequest.objects.filter(requester_id=request.user.pk).select_related(
        "brand", "firmware_model", "chipset"
    )
    submitted = Post.objects.filter(
        author_id=request.user.pk, guide_request__isnull=False
    ).select_related("guide_request")
    return render(
        request,
        "blog/requests/mine.html",
        {"requests": mine[:100], "submissions": submitted[:100]},
    )

"""Public pages for guide requests: search step, form, list, detail, "my requests"."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.forms import ModelChoiceField
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.blog import bounty_gateway, guide_requests
from apps.blog import guide_requests_search as search
from apps.blog.access import blog_enabled_for, blog_settings
from apps.blog.guide_requests_forms import GuideRequestForm, GuideSearchForm
from apps.blog.models import GuideRequest, GuideRequestFollower, Post, PostStatus
from apps.core.search.listing import search_queryset

PAGE_SIZE = 15
STATUS_FILTERS = (("open", "Open"), ("fulfilled", "Fulfilled"), ("all", "All"))
LIST_SEARCH_FIELDS = (
    "title",
    "description",
    "model_code",
    "variant_code",
    "brand__name",
    "firmware_model__name",
    "variant__name",
)


def _require_blog(request: HttpRequest) -> None:
    """404 when the blog is switched off for this visitor."""
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
    """Public requests with the shared, typo tolerant search."""
    _require_blog(request)
    status = request.GET.get("status", "open")
    kind = request.GET.get("kind", "")
    query = request.GET.get("q", "").strip()[:100]
    rows = GuideRequest.objects.filter(status__in=GuideRequest.PUBLIC).select_related(
        "brand", "firmware_model", "variant", "chipset"
    )
    if status == "open":
        rows = rows.filter(status=GuideRequest.Status.APPROVED)
    elif status == "fulfilled":
        rows = rows.filter(status=GuideRequest.Status.FULFILLED)
    else:
        status = "all"
    if kind in GuideRequest.Kind.values:
        rows = rows.filter(kind=kind)
    found = search_queryset(rows, query, LIST_SEARCH_FIELDS)
    rows = found.queryset.annotate(
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
            "search_corrected": found.corrected,
            "status_filters": STATUS_FILTERS,
            "kind_filters": GuideRequest.Kind.choices,
            "requests_enabled": blog_settings().allow_guide_requests,
        },
    )


def _user_id(request: HttpRequest) -> int:
    """Primary key of the signed-in member (views are ``login_required``)."""
    return int(request.user.pk or 0)


def _search_context(user_id: int, data: Any) -> dict[str, Any]:
    """Results of the search step; the continue link exists only after a search."""
    query = search.DeviceQuery.from_mapping(data)
    if not query.has_device:
        return {"query": query, "searched": False, "results": [], "continue_url": ""}
    results = search.find_related_posts(query)
    token = search.make_token(user_id, query, len(results))
    return {
        "query": query,
        "searched": True,
        "results": results,
        "continue_url": f"{reverse('blog:request_form')}?{urlencode({'ctx': token})}",
    }


@login_required
@require_GET
def request_create(request: HttpRequest) -> HttpResponse:
    """Step 1 of a request: search for existing articles by model/variant code."""
    _require_blog(request)
    config = blog_settings()
    has_input = any(request.GET.get(name) for name in search.DeviceQuery().payload())
    form = GuideSearchForm(request.GET if has_input else None)
    context = _search_context(_user_id(request), request.GET) if has_input else {}
    return render(
        request,
        "blog/requests/search.html",
        {
            "form": form,
            "requests_enabled": config.allow_guide_requests,
            **context,
        },
    )


@login_required
@never_cache
@require_GET
def request_search(request: HttpRequest) -> HttpResponse:
    """HTMX partial: every related article for the typed device (live search)."""
    _require_blog(request)
    context = _search_context(_user_id(request), request.GET)
    context["requests_enabled"] = blog_settings().allow_guide_requests
    return render(request, "blog/requests/_search_results.html", context)


@login_required
@require_GET
def request_from_post(request: HttpRequest, slug: str) -> HttpResponse:
    """The article's "Request a new method" button: open the form for that article."""
    _require_blog(request)
    post = get_object_or_404(Post.objects.live(), slug=slug)
    token = search.make_token(
        _user_id(request), search.DeviceQuery(), 0, post_id=post.pk
    )
    return redirect(f"{reverse('blog:request_form')}?{urlencode({'ctx': token})}")


def _form_entry(request: HttpRequest, proof: search.Proof) -> GuideRequestForm:
    """Build the (bound or prefilled) form for a valid search proof."""
    focus = proof.post_id is not None
    if request.method == "POST":
        return GuideRequestForm(request.POST, focus_tried=focus)
    initial = search.initial_for(proof.query) if proof.query.has_device else {}
    if proof.post_id is not None:
        post = Post.objects.live().filter(pk=proof.post_id).first()
        if post is not None:
            initial = {**initial, **search.initial_from_post(post)}
    return GuideRequestForm(initial=initial, focus_tried=focus)


def _submit(
    request: HttpRequest, form: GuideRequestForm
) -> tuple[HttpResponse | None, dict[str, Any]]:
    """Create the request; on a refusal return the extra context to show."""
    extra: dict[str, Any] = {}
    try:
        created = guide_requests.create_request(
            request.user,
            form.to_text(),
            form.to_context(),
            bounty_offer=form.cleaned_data.get("bounty_offer"),
            allow_duplicates="confirm_different" in request.POST,
        )
    except guide_requests.DuplicateRequestError as error:
        extra["open_duplicates"] = error.duplicates
    except guide_requests.GuideRequestError as error:
        form.add_error(None, str(error))
        data = form.cleaned_data
        extra["duplicates"] = guide_requests.find_duplicates(
            data["title"], brand=data.get("brand"), firmware_model=data.get("model")
        )
    else:
        messages.success(
            request,
            "Request sent. Staff review every request first; you will be notified.",
        )
        return redirect("blog:request_detail", pk=created.pk), extra
    return None, extra


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def request_form(request: HttpRequest) -> HttpResponse:
    """Step 2 of a request; needs the proof of a search or of an article."""
    _require_blog(request)
    config = blog_settings()
    source = request.POST if request.method == "POST" else request.GET
    token = str(source.get("ctx", ""))
    proof = search.read_token(token, _user_id(request)) if token else None
    if proof is None:
        messages.info(
            request,
            "Search for your model first: the answer may already be published.",
        )
        return redirect("blog:request_create")
    form = _form_entry(request, proof)
    extra: dict[str, Any] = {}
    if not config.allow_guide_requests:
        messages.info(request, "Guide requests are switched off right now.")
    elif request.method == "POST" and form.is_valid():
        response, extra = _submit(request, form)
        if response is not None:
            return response
    related = _related_post(form)
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
            "ctx_token": token,
            "related_post": related,
            "requests_enabled": config.allow_guide_requests,
            **extra,
            **labels,
        },
    )


def _related_post(form: GuideRequestForm) -> Post | None:
    value = form["related_post"].value()
    if not value or not str(value).isdigit():
        return None
    return Post.objects.live().filter(pk=int(value)).first()


@login_required
@require_POST
def request_follow(request: HttpRequest, pk: int) -> HttpResponse:
    """ "Me too": follow an open request instead of filing a duplicate."""
    _require_blog(request)
    item = get_object_or_404(GuideRequest, pk=pk)
    if not item.is_public:
        raise Http404
    try:
        _link, created = guide_requests.follow(item.pk, request.user)
    except guide_requests.GuideRequestError as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            "You follow this request." if created else "You already follow this.",
        )
    return redirect("blog:request_detail", pk=item.pk)


@login_required
@require_POST
def request_unfollow(request: HttpRequest, pk: int) -> HttpResponse:
    """Stop following a request."""
    _require_blog(request)
    item = get_object_or_404(GuideRequest, pk=pk)
    if guide_requests.unfollow(item.pk, request.user):
        messages.success(request, "You no longer follow this request.")
    return redirect("blog:request_detail", pk=item.pk)


def _label(form: GuideRequestForm, name: str) -> str:
    """Display text of the catalogue row a hidden combobox field holds."""
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


def _context_access(user: Any, item: GuideRequest, *, reviewer: bool) -> dict[str, Any]:
    """What the requester tried and the related article: staff, owner and hunters.

    Hunters are verified members looking at a public request; they need the context
    to write their own solution. Anonymous visitors see only the public summary.
    """
    is_owner = bool(user.is_authenticated and user.pk == item.requester_id)
    hunter = bool(
        item.is_public
        and user.is_authenticated
        and getattr(user, "email_verified_at", None) is not None
    )
    related = item.related_post
    if related is not None and not (related.is_live or reviewer or is_owner):
        related = None
    following = bool(
        user.is_authenticated
        and GuideRequestFollower.objects.filter(request=item, user=user).exists()
    )
    return {
        "can_see_context": reviewer or is_owner or hunter,
        "related_post": related,
        "follower_count": item.followers.count(),
        "is_following": following,
        "can_follow": bool(
            item.status == GuideRequest.Status.APPROVED and hunter and not is_owner
        ),
    }


@require_GET
def request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """One request with its context, bounty, solutions and follow button."""
    _require_blog(request)
    item = get_object_or_404(
        GuideRequest.objects.select_related(
            "requester",
            "related_post",
            "brand",
            "firmware_model",
            "variant",
            "chipset",
            "fulfilled_by_post",
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
            **_context_access(user, item, reviewer=reviewer),
        },
    )


@login_required
@require_GET
def request_mine(request: HttpRequest) -> HttpResponse:
    """The member's own requests and the solutions they submitted."""
    _require_blog(request)
    mine = GuideRequest.objects.filter(requester_id=request.user.pk).select_related(
        "brand", "firmware_model", "variant", "chipset"
    )
    submitted = Post.objects.filter(
        author_id=request.user.pk, guide_request__isnull=False
    ).select_related("guide_request")
    return render(
        request,
        "blog/requests/mine.html",
        {"requests": mine[:100], "submissions": submitted[:100]},
    )

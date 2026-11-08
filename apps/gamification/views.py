from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.gamification import services
from apps.gamification.models import Badge, UserBadge, XPTransaction
from apps.users.request_user import current_user

PERIOD_LABELS = (("week", "This week"), ("month", "This month"), ("all", "All time"))


@require_GET
def badges(request: HttpRequest) -> HttpResponse:
    catalogue = Badge.objects.filter(is_active=True).annotate(holders=Count("awards"))
    earned: set[int] = set()
    if request.user.is_authenticated:
        earned = set(
            UserBadge.objects.filter(user=request.user).values_list(
                "badge_id", flat=True
            )
        )
    return render(
        request,
        "gamification/badges.html",
        {"badges": catalogue, "earned_ids": earned},
    )


@require_GET
def leaderboard(request: HttpRequest) -> HttpResponse:
    period = request.GET.get("period", "all")
    if period not in dict(PERIOD_LABELS):
        period = "all"
    return render(
        request,
        "gamification/leaderboard.html",
        {
            "rows": services.leaderboard(period),
            "period": period,
            "periods": PERIOD_LABELS,
        },
    )


@login_required
@require_GET
def progress(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    summary = services.progress_for(user.pk)
    return render(
        request,
        "gamification/progress.html",
        {
            "progress": summary,
            "streak": getattr(user, "streak", None),
            "earned": UserBadge.objects.filter(user=user).select_related("badge"),
            "recent": XPTransaction.objects.filter(user=user)[:15],
        },
    )

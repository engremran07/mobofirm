"""Staff-only search over accounts (never offered to other visitors)."""

from __future__ import annotations

from django.db.models import QuerySet
from django.urls import reverse

from apps.core.search import QuerySetProvider, SearchContext, SearchHit, register

from .models import CustomUser


class UserProvider(QuerySetProvider[CustomUser]):
    kind = "user"
    label = "Account"
    label_plural = "Accounts"
    icon = "user-round"
    priority = 950
    staff_only = True
    search_fields = ("username", "email", "full_name")
    ordering = ("username",)

    def get_queryset(self, context: SearchContext) -> QuerySet[CustomUser]:
        if not context.is_staff:
            return CustomUser.objects.none()
        return CustomUser.objects.all()

    def score_fields(self, obj: CustomUser) -> tuple[str, ...]:
        return (obj.username or "", obj.email, obj.full_name)

    def build_hit(
        self, obj: CustomUser, score: float, context: SearchContext
    ) -> SearchHit:
        return SearchHit(
            kind=self.kind,
            title=obj.username or obj.email,
            subtitle=obj.email,
            url=reverse("admin:users_customuser_change", args=[obj.pk]),
            icon=self.icon,
            score=score,
            value=str(obj.pk),
            label=obj.username or obj.email,
        )


register(UserProvider())

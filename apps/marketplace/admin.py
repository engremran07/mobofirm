"""Marketplace administration (Unfold): seller verification and listing review.

Decisions go through ``apps.marketplace.services`` so each request is reviewed
exactly once, by a user holding the change permission, with the reason recorded.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.db.models import Model as DjangoModel
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
)
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminTextInputWidget

from apps.core.admin_base import (
    BADGE_APPROVAL,
    ModelAdmin,
    NoAddModelAdmin,
    ReasonForm,
    TabularInline,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)

from . import services
from .models import Listing, SellerProfile, SellerVerification

Reviewer = Callable[..., Any]


class OptionalReasonForm(BaseDialogForm):
    reason = forms.CharField(
        label=_("Reason"),
        required=False,
        max_length=255,
        help_text=_("Optional note shown to the seller."),
        widget=UnfoldAdminTextInputWidget,
    )


class SellerVerificationInline(TabularInline):
    model = SellerVerification
    fields = ("status", "evidence", "reviewer", "review_reason", "submitted_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True
    tab = True
    ordering = ("-submitted_at",)

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


class SellerListingInline(TabularInline):
    model = Listing
    fields = ("title", "price", "status", "is_active", "is_featured")
    readonly_fields = fields
    can_delete = False
    show_change_link = True
    tab = True
    ordering = ("-created_at",)

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(SellerProfile)
class SellerProfileAdmin(ModelAdmin):
    list_display = (
        "display_name",
        "user",
        "verified_badge",
        "is_active",
        "created_at",
    )
    list_filter = ("verified", "is_active", ("created_at", RangeDateTimeFilter))
    search_fields = ("display_name", "user__email", "user__username")
    readonly_fields = ("verified", "created_at")
    raw_id_fields = ("user",)
    list_select_related = ("user",)
    inlines = (SellerVerificationInline, SellerListingInline)

    @display(
        description=_("Verification"),
        ordering="verified",
        label={"verified": "success", "unverified": "warning"},
    )
    def verified_badge(self, obj: SellerProfile) -> str:
        return "verified" if obj.verified else "unverified"


class ReviewActionsMixin(ModelAdmin):
    """Shared approve/reject plumbing for pending-review models."""

    def _review_queryset(
        self,
        request: HttpRequest,
        queryset: QuerySet[Any],
        review: Reviewer,
        approve: bool,
        reason: str | None,
        noun: str,
    ) -> None:
        done = 0
        for item in queryset:
            try:
                review(
                    item.pk,
                    reviewer=request.user,
                    approve=approve,
                    reason=item.review_reason if reason is None else reason,
                )
                done += 1
            except services.MarketplaceError as exc:
                self.message_user(request, str(exc), messages.ERROR)
        self.message_user(request, f"{done} {noun}(s) reviewed.")

    def _review_one(
        self,
        request: HttpRequest,
        object_id: str,
        review: Reviewer,
        approve: bool,
        reason: str,
        noun: str,
    ) -> HttpResponse:
        item = self.get_object(request, unquote(object_id))
        if item is None:
            raise Http404
        self._review_queryset(
            request,
            self.model._default_manager.filter(pk=item.pk),
            review,
            approve,
            reason,
            noun,
        )
        return htmx_redirect(return_url(request, change_url(self, item.pk)))


@register(SellerVerification)
class SellerVerificationAdmin(NoAddModelAdmin, ReviewActionsMixin):
    list_display = ("seller", "status_badge", "reviewer", "submitted_at", "reviewed_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("submitted_at", RangeDateTimeFilter),
    )
    search_fields = ("seller__display_name", "seller__user__email")
    list_select_related = ("seller", "reviewer")
    readonly_fields = (
        "seller",
        "evidence",
        "status",
        "reviewer",
        "submitted_at",
        "reviewed_at",
    )
    fields = (*readonly_fields, "review_reason")
    actions = ("approve", "reject")
    actions_row = ("approve_verification", "reject_verification")
    actions_detail = ("approve_verification", "reject_verification")

    @display(description=_("Status"), ordering="status", label=BADGE_APPROVAL)
    def status_badge(self, obj: SellerVerification) -> str:
        return obj.status

    @action(description="Approve selected verifications", permissions=["change"])
    def approve(
        self, request: HttpRequest, queryset: QuerySet[SellerVerification]
    ) -> None:
        self._review_queryset(
            request, queryset, services.review_verification, True, None, "verification"
        )

    @action(description="Reject selected verifications", permissions=["change"])
    def reject(
        self, request: HttpRequest, queryset: QuerySet[SellerVerification]
    ) -> None:
        self._review_queryset(
            request, queryset, services.review_verification, False, None, "verification"
        )

    @action(
        description=_("Approve"),
        icon="verified",
        permissions=["change"],
        url_path="approve-verification",
        dialog=confirm_dialog(
            _("Approve this seller"),
            _("The seller becomes verified and their approved listings go public."),
            OptionalReasonForm,
            submit=_("Approve"),
        ),
    )
    def approve_verification(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._review_one(
            request,
            object_id,
            services.review_verification,
            True,
            str(form.cleaned_data["reason"]),
            "verification",
        )

    @action(
        description=_("Reject"),
        icon="block",
        permissions=["change"],
        url_path="reject-verification",
        dialog=confirm_dialog(
            _("Reject this verification"),
            _("The reason is stored on the request."),
            ReasonForm,
            submit=_("Reject"),
        ),
    )
    def reject_verification(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._review_one(
            request,
            object_id,
            services.review_verification,
            False,
            str(form.cleaned_data["reason"]),
            "verification",
        )


@register(Listing)
class ListingAdmin(ReviewActionsMixin):
    list_display = (
        "title",
        "seller",
        "price",
        "status_badge",
        "is_active",
        "is_featured",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        "is_active",
        "is_featured",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("title", "seller__display_name")
    list_select_related = ("seller",)
    readonly_fields = (
        "status",
        "reviewed_by",
        "reviewed_at",
        "view_count",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("seller", "firmware")
    actions = ("approve", "reject")
    actions_row = ("approve_listing", "reject_listing")
    actions_detail = ("approve_listing", "reject_listing")
    fieldsets = (
        (
            _("Listing"),
            {
                "fields": (
                    "seller",
                    "firmware",
                    "title",
                    "description",
                    "price",
                    "discount_price",
                    "is_active",
                    "is_featured",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Review"),
            {
                "fields": (
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "review_reason",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Statistics"),
            {
                "fields": ("view_count", "created_at", "updated_at"),
                "classes": ["tab"],
            },
        ),
    )

    @display(description=_("Status"), ordering="status", label=BADGE_APPROVAL)
    def status_badge(self, obj: Listing) -> str:
        return obj.status

    @action(description="Approve selected listings", permissions=["change"])
    def approve(self, request: HttpRequest, queryset: QuerySet[Listing]) -> None:
        self._review_queryset(
            request, queryset, services.review_listing, True, None, "listing"
        )

    @action(description="Reject selected listings", permissions=["change"])
    def reject(self, request: HttpRequest, queryset: QuerySet[Listing]) -> None:
        self._review_queryset(
            request, queryset, services.review_listing, False, None, "listing"
        )

    @action(
        description=_("Approve"),
        icon="check_circle",
        permissions=["change"],
        url_path="approve-listing",
        dialog=confirm_dialog(
            _("Approve this listing"),
            _("The listing becomes public once its seller is verified."),
            OptionalReasonForm,
            submit=_("Approve"),
        ),
    )
    def approve_listing(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._review_one(
            request,
            object_id,
            services.review_listing,
            True,
            str(form.cleaned_data["reason"]),
            "listing",
        )

    @action(
        description=_("Reject"),
        icon="block",
        permissions=["change"],
        url_path="reject-listing",
        dialog=confirm_dialog(
            _("Reject this listing"),
            _("The reason is stored on the listing."),
            ReasonForm,
            submit=_("Reject"),
        ),
    )
    def reject_listing(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._review_one(
            request,
            object_id,
            services.review_listing,
            False,
            str(form.cleaned_data["reason"]),
            "listing",
        )

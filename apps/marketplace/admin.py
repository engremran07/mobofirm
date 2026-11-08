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
from django.core.exceptions import ValidationError
from django.db.models import Model as DjangoModel
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
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
    ReadOnlyModelAdmin,
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
from .models import (
    MAX_SOCIAL_LINKS,
    Listing,
    SellerEarning,
    SellerProfile,
    SellerSocialLink,
    SellerVerification,
)
from .validators import validate_https_url

Reviewer = Callable[..., Any]


def _external_link(url: str) -> SafeString | str:
    """Anchor for a seller link; anything that is not a valid https URL is text."""
    try:
        clean = validate_https_url(url)
    except ValidationError:
        return url
    return format_html(
        '<a href="{}" target="_blank" rel="nofollow noopener noreferrer">{}</a>',
        clean,
        clean,
    )


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


class SellerEarningInline(TabularInline):
    model = SellerEarning
    fields = ("firmware_title", "gross_amount", "net_credits", "status", "created_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True
    tab = True
    ordering = ("-created_at",)
    max_num = 0

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


class SellerSocialLinkInline(TabularInline):
    model = SellerSocialLink
    fields = ("platform", "url")
    extra = 0
    max_num = MAX_SOCIAL_LINKS
    tab = True


@register(SellerProfile)
class SellerProfileAdmin(ModelAdmin):
    list_display = (
        "display_name",
        "user",
        "verified_badge",
        "contact_preference",
        "is_active",
        "created_at",
    )
    list_filter = (
        "verified",
        "is_active",
        "contact_preference",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("display_name", "user__email", "user__username")
    readonly_fields = (
        "verified",
        "created_at",
        "terms_accepted_at",
        "speciality_brands",
        "speciality_chipsets",
    )
    raw_id_fields = ("user",)
    list_select_related = ("user",)
    inlines = (
        SellerSocialLinkInline,
        SellerVerificationInline,
        SellerListingInline,
        SellerEarningInline,
    )
    fieldsets = (
        (
            _("Seller"),
            {
                "fields": (
                    "user",
                    "display_name",
                    "bio",
                    "website",
                    "contact_preference",
                    "verified",
                    "is_active",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Specialities"),
            {
                "fields": ("speciality_brands", "speciality_chipsets"),
                "classes": ["tab"],
            },
        ),
        (
            _("Terms"),
            {"fields": ("terms_accepted_at", "created_at"), "classes": ["tab"]},
        ),
    )

    @display(
        description=_("Verification"),
        ordering="verified",
        label={"verified": "success", "unverified": "warning"},
    )
    def verified_badge(self, obj: SellerProfile) -> str:
        return "verified" if obj.verified else "unverified"

    @display(description=_("Brand specialities"))
    def speciality_brands(self, obj: SellerProfile) -> str:
        return ", ".join(brand.name for brand in obj.brands.all()) or "-"

    @display(description=_("Chipset specialities"))
    def speciality_chipsets(self, obj: SellerProfile) -> str:
        return ", ".join(chip.display_name for chip in obj.chipsets.all()) or "-"


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
            self.get_queryset(request).filter(pk=item.pk),
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
        "application",
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

    @display(description=_("Application details"))
    def application(self, obj: SellerVerification) -> SafeString:
        seller = obj.seller
        rows: list[tuple[str, SafeString | str]] = [
            ("Applicant", f"{seller.display_name} ({seller.user.email})"),
            ("Contact preference", seller.get_contact_preference_display()),
            ("Brands", ", ".join(b.name for b in seller.brands.all()) or "-"),
            (
                "Chipsets",
                ", ".join(c.display_name for c in seller.chipsets.all()) or "-",
            ),
            ("Terms accepted", str(seller.terms_accepted_at or "-")),
            ("Website", _external_link(seller.website) if seller.website else "-"),
        ]
        rows.extend(
            (link.get_platform_display(), _external_link(link.url))
            for link in seller.social_links.all()
        )
        return format_html_join(
            "",
            "<p><strong>{}:</strong> {}</p>",
            rows,
        )

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


@register(SellerEarning)
class SellerEarningAdmin(ReadOnlyModelAdmin):
    list_display = (
        "firmware_title",
        "seller",
        "gross_amount",
        "net_credits",
        "status_badge",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("firmware_title", "seller__display_name", "order_ref")
    list_select_related = ("seller",)

    @display(
        description=_("Status"),
        ordering="status",
        label={
            "awaiting_wallet": "warning",
            "credited": "success",
            "reversed": "info",
            "reversal_failed": "danger",
        },
    )
    def status_badge(self, obj: SellerEarning) -> str:
        return obj.status


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

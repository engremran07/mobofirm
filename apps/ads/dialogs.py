"""App-owned staff dialogs of the placement manager (admin shell)."""

from __future__ import annotations

from typing import Any

from django import forms
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _

from apps.ads.models import AdCreative, AdPlacement, ScanDiscovery
from apps.ads.services import placement_manager, scanner
from apps.ads.zones import DEVICE_CHOICES, ZONE_BY_CODE
from apps.core.admin_dialogs import StaffDialogView
from apps.core.dialogs import ConfirmDialogForm, DialogError, DialogForm

MANAGE = "ads.change_adplacement"


def can_manage_placements(request: HttpRequest) -> bool:
    user = request.user
    return bool(user.is_staff and user.has_perm(MANAGE))


class PlaceAdForm(DialogForm):
    """Choose the zone (declared by the template), sizes, device and fallback."""

    def __init__(self, *args: Any, template_path: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.template_path = template_path
        declared = (
            placement_manager.declared_zones(template_path)
            if template_path
            else placement_manager.site_wide_zones()
        )
        taken = set(
            AdPlacement.objects.filter(
                template_reference=template_path, is_deleted=False
            ).values_list("zone", flat=True)
        )
        self.fields["zone"] = forms.ChoiceField(
            label=_("Zone"),
            choices=[
                (zone.code, f"{zone.label} ({zone.default_size})")
                for zone in ZONE_BY_CODE.values()
                if zone.code in declared and zone.code not in taken
            ],
            help_text=_("Only zones this template declares are offered."),
        )
        self.fields["sizes"] = forms.CharField(
            label=_("Sizes"),
            required=False,
            max_length=100,
            help_text=_(
                "Comma separated, e.g. 728x90,970x90. Empty uses the zone default."
            ),
        )
        self.fields["device"] = forms.ChoiceField(
            label=_("Devices"), choices=DEVICE_CHOICES, initial="all"
        )
        self.fields["fallback"] = forms.ModelChoiceField(
            label=_("Fallback creative"),
            required=False,
            queryset=AdCreative.objects.filter(
                is_deleted=False, is_enabled=True
            ).select_related("campaign"),
            help_text=_("Shown when no assigned creative is eligible. Empty = no ad."),
        )
        self.fields["existing"] = forms.ModelChoiceField(
            label=_("Link an existing placement"),
            required=False,
            queryset=AdPlacement.objects.filter(is_deleted=False, zone=""),
            help_text=_(
                "Optional: reuse a code-addressed placement (and its creatives) "
                "instead of creating a new one."
            ),
        )
        self.fields["enable"] = forms.BooleanField(
            label=_("Switch on now"),
            required=False,
            help_text=_(
                "New placements start switched off. They serve only once a creative "
                "is assigned and the placement is on."
            ),
        )


class PlaceAdDialog(StaffDialogView):
    """Place an ad on a scanned template (``?template=`` empty = site-wide)."""

    template_name = "ads/dialogs/place.html"
    form_class = PlaceAdForm
    dialog_id = "ads-place-dialog"
    dialog_title = _("Place an ad")
    confirm_label = _("Create placement")
    size = "md"
    success_url_name = "ads:placement_manager"
    placement: AdPlacement | None = None

    def has_permission(self, request: HttpRequest) -> bool:
        return can_manage_placements(request)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["template_path"] = self.request.GET.get("template", "").strip()
        zone = self.request.GET.get("zone", "")
        if zone and self.request.method == "GET":
            kwargs["initial"] = {**kwargs["initial"], "zone": zone}
        return kwargs

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        context = super().get_context_data(form)
        context["template_path"] = getattr(form, "template_path", "")
        return context

    def perform(self, form: DialogForm) -> None:
        data = form.cleaned_data
        try:
            self.placement = placement_manager.place_ad(
                user=self.request.user,
                template_path=getattr(form, "template_path", ""),
                zone_code=data["zone"],
                sizes=data["sizes"],
                device=data["device"],
                fallback=data["fallback"],
                existing=data["existing"],
                enabled=bool(data["enable"]),
            )
        except placement_manager.PlacementError as error:
            raise DialogError(str(error)) from error

    def get_success_message(self) -> str:
        return f"Placement “{self.placement}” saved." if self.placement else ""


class RemovePlacementDialog(StaffDialogView):
    """Confirm removing a placement (soft delete; its history stays)."""

    template_name = "ads/dialogs/remove_placement.html"
    form_class = ConfirmDialogForm
    dialog_id = "ads-remove-dialog"
    dialog_title = _("Remove this placement?")
    confirm_label = _("Remove placement")
    danger = True
    success_url_name = "ads:placement_manager"

    def has_permission(self, request: HttpRequest) -> bool:
        return can_manage_placements(request)

    def get_placement(self) -> AdPlacement:
        return get_object_or_404(AdPlacement, pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        context = super().get_context_data(form)
        context["placement"] = self.get_placement()
        return context

    def perform(self, form: DialogForm) -> None:
        placement = self.get_placement()
        placement.is_enabled = False
        placement.save(update_fields=["is_enabled"])
        placement.soft_delete(self.request.user)

    def get_success_message(self) -> str:
        return "The placement was removed."


class ReviewDiscoveriesDialog(StaffDialogView):
    """Approve or reject the discoveries named by ``?id=`` (one or many)."""

    template_name = "ads/dialogs/review_discoveries.html"
    form_class = ConfirmDialogForm
    dialog_id = "ads-review-dialog"
    success_url_name = "ads:discoveries"
    decision = "reject"
    done = 0

    def has_permission(self, request: HttpRequest) -> bool:
        return can_manage_placements(request)

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        super().setup(request, *args, **kwargs)
        self.decision = "approve" if kwargs.get("decision") == "approve" else "reject"
        self.danger = self.decision == "reject"
        self.confirm_label = _("Approve") if self.decision == "approve" else _("Reject")
        count = len(self.ids())
        verb = "Approve" if self.decision == "approve" else "Reject"
        self.dialog_title = f"{verb} {count} suggestion{'s' if count != 1 else ''}?"

    def ids(self) -> list[int]:
        raw = self.request.GET.getlist("id")
        return sorted({int(value) for value in raw if value.isdecimal()})

    def pending(self) -> list[ScanDiscovery]:
        return list(
            ScanDiscovery.objects.filter(pk__in=self.ids(), status="pending")
            .select_related("scan_result")
            .order_by("scan_result__template_path", "placement_code")
        )

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        context = super().get_context_data(form)
        context["discoveries"] = self.pending()
        context["decision"] = self.decision
        return context

    def perform(self, form: DialogForm) -> None:
        pending = self.pending()
        if not pending:
            raise DialogError(str(_("None of these suggestions is pending any more.")))
        decide = (
            scanner.approve_discovery
            if self.decision == "approve"
            else scanner.reject_discovery
        )
        self.done = sum(
            1 for d in pending if decide(d.pk, self.request.user)["status"] == "success"
        )

    def get_success_message(self) -> str:
        verb = "Approved" if self.decision == "approve" else "Rejected"
        return f"{verb} {self.done} suggestion(s)."

"""End-user pages: catalogue requests, "My requests", "My uploads" and the upload page.

Every page that writes requires a signed-in user with a verified email, CSRF, and
redirects after success. Brand, model and variant are chosen with the shared
type-ahead combobox fed by the central search engine.
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_GET, require_http_methods

from apps.core.app_service import app_enabled
from apps.users.decorators import email_verification_required
from apps.users.request_user import current_user

from . import catalog_requests as requests_service
from .access import is_seller
from .catalog_requests import CatalogRequestError, RequestKind
from .forms import (
    BrandRequestForm,
    ChipsetRequestForm,
    FirmwareRequestForm,
    ModelRequestForm,
    UploadForm,
    VariantRequestForm,
)
from .models import Brand, Chipset, Model, PendingFirmware, Variant
from .services import FirmwareUploadError, max_upload_bytes
from .submissions import submit_user_upload
from .variant_rules import VariantIdentity

logger = logging.getLogger(__name__)

QUERY_LIMIT = 100


def verified_user_required(view):
    """Signed in (redirect to login) and email verified (redirect to verify)."""
    return login_required(email_verification_required(view))


def _text(request: HttpRequest, name: str) -> str:
    return " ".join(request.GET.get(name, "").split())[:QUERY_LIMIT]


def _int(request: HttpRequest, name: str) -> int | None:
    raw = request.GET.get(name, "")
    return int(raw) if raw.isdigit() else None


# ---------------------------------------------------------------------------
# Combobox state shared by the request and upload forms
# ---------------------------------------------------------------------------


def _chosen(
    form: Any, field: str, model_class: type[Brand | Model | Variant | Chipset]
) -> Any:
    value = form[field].value() if field in form.fields else None
    if isinstance(value, model_class):
        return value
    if value in (None, "") or not str(value).isdigit():
        return None
    return model_class.objects.filter(pk=int(value)).first()


def _model_label(model: Model) -> str:
    return (
        f"{model.display_name} ({model.model_code})"
        if model.model_code
        else (model.display_name)
    )


def _picker_state(form: Any) -> dict[str, str]:
    brand = _chosen(form, "brand", Brand)
    model = _chosen(form, "model", Model)
    variant = _chosen(form, "variant", Variant)
    chipset = _chosen(form, "chipset", Chipset)
    return {
        "brand_label": brand.name if brand else "",
        "model_label": _model_label(model) if model else "",
        "variant_label": variant.name if variant else "",
        "chipset_label": chipset.display_name if chipset else "",
    }


def _initial_from_query(request: HttpRequest, *names: str) -> dict[str, Any]:
    return {name: request.GET[name] for name in names if request.GET.get(name)}


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

REQUEST_KINDS: dict[str, dict[str, str]] = {
    "brand": {
        "title": "Request a brand",
        "lead": "Tell us about a manufacturer that is missing from the catalogue.",
        "button": "Send brand request",
    },
    "model": {
        "title": "Request a model",
        "lead": "Ask for a device model that is not listed under its brand yet.",
        "button": "Send model request",
    },
    "variant": {
        "title": "Request a variant",
        "lead": "Ask for a regional or hardware variant of an existing model.",
        "button": "Send variant request",
    },
    "firmware": {
        "title": "Request firmware",
        "lead": "Ask for a firmware build we do not publish yet. Others asking for "
        "the same file raise its priority.",
        "button": "Send firmware request",
    },
    "chipset": {
        "title": "Request a chipset",
        "lead": "Ask for a system-on-chip that is missing from the chipset catalogue.",
        "button": "Send chipset request",
    },
}


@require_GET
def requests_hub(request: HttpRequest) -> HttpResponse:
    mine = (
        requests_service.my_requests(request.user).total
        if request.user.is_authenticated
        else 0
    )
    return render(
        request,
        "firmwares/requests/hub.html",
        {"kinds": REQUEST_KINDS, "my_total": mine},
    )


def _service_call(request: HttpRequest, form: Any, call) -> HttpResponse | None:
    """Run a submission; on refusal show it on the form and return ``None``."""
    try:
        call()
    except CatalogRequestError as error:
        form.add_error(None, str(error))
        return None
    return redirect("firmwares:my_requests")


CHECK_INCLUDES = {
    "brand": "this",
    "chipset": "[name='vendor']",
    "model": "[name='brand']",
    "variant": "[name='brand'], [name='model']",
}


def _attach_existence_check(form: Any, kind: str) -> None:
    """Make the name input ask the server, live, whether the entry already exists."""
    if kind not in {"brand", "model", "variant", "chipset"}:
        return
    form.fields["name"].widget.attrs.update(
        {
            "hx-get": reverse("firmwares:request_check"),
            "hx-trigger": "input changed delay:250ms, "
            "combobox:select from:body, combobox:clear from:body",
            "hx-target": "#request-check",
            "hx-swap": "innerHTML",
            "hx-sync": "this:replace",
            "hx-include": CHECK_INCLUDES[kind],
            "hx-vals": f'{{"kind": "{kind}"}}',
            "autocomplete": "off",
        }
    )


def _request_form_view(
    request: HttpRequest, kind: str, form_class: type, submit
) -> HttpResponse:
    if request.method == "POST":
        form = form_class(request.POST)
        _attach_existence_check(form, kind)
        if form.is_valid():
            outcome = _service_call(
                request, form, lambda: submit(request.user, form.cleaned_data)
            )
            if outcome is not None:
                return outcome
    else:
        form = form_class(
            initial=_initial_from_query(
                request, "brand", "model", "chipset", "name", "description", "vendor"
            )
        )
        _attach_existence_check(form, kind)
    return render(
        request,
        "firmwares/requests/form.html",
        {"form": form, "kind": kind, **REQUEST_KINDS[kind], **_picker_state(form)},
    )


@verified_user_required
@require_http_methods(["GET", "POST"])
def request_chipset(request: HttpRequest) -> HttpResponse:
    def submit(user: Any, data: dict[str, Any]) -> None:
        pending = requests_service.submit_chipset_request(
            user,
            vendor=data["vendor"],
            name=data["name"],
            family=data["family"],
            aliases=data["aliases"],
            details=data["details"],
        )
        messages.success(request, f"Chipset request “{pending.name}” sent for review.")

    return _request_form_view(request, "chipset", ChipsetRequestForm, submit)


@verified_user_required
@require_http_methods(["GET", "POST"])
def request_brand(request: HttpRequest) -> HttpResponse:
    def submit(user: Any, data: dict[str, Any]) -> None:
        pending = requests_service.submit_brand_request(
            user, name=data["name"], details=data["details"]
        )
        messages.success(request, f"Brand request “{pending.name}” sent for review.")

    return _request_form_view(request, "brand", BrandRequestForm, submit)


@verified_user_required
@require_http_methods(["GET", "POST"])
def request_model(request: HttpRequest) -> HttpResponse:
    def submit(user: Any, data: dict[str, Any]) -> None:
        pending = requests_service.submit_model_request(
            user,
            brand=data["brand"],
            name=data["name"],
            model_code=data["model_code"],
            chipset=data["chipset"],
            details=data["details"],
        )
        messages.success(request, f"Model request “{pending.name}” sent for review.")

    return _request_form_view(request, "model", ModelRequestForm, submit)


@verified_user_required
@require_http_methods(["GET", "POST"])
def request_variant(request: HttpRequest) -> HttpResponse:
    def submit(user: Any, data: dict[str, Any]) -> None:
        pending = requests_service.submit_variant_request(
            user,
            model=data["model"],
            name=data["name"],
            identity=VariantIdentity(
                model_code=data["model_code"],
                region=data["region"],
                memory=data["memory"],
                carrier=data["carrier"],
            ),
            details=data["details"],
        )
        messages.success(request, f"Variant request “{pending.name}” sent for review.")

    return _request_form_view(request, "variant", VariantRequestForm, submit)


@verified_user_required
@require_http_methods(["GET", "POST"])
def request_firmware(request: HttpRequest) -> HttpResponse:
    def submit(user: Any, data: dict[str, Any]) -> None:
        pending, created = requests_service.submit_firmware_request(
            user,
            brand=data["brand"],
            model=data["model"],
            variant_name=data["variant_name"],
            firmware_type=data["firmware_type"],
            urgency=data["urgency"],
            description=data["description"],
        )
        if created:
            messages.success(request, "Firmware request sent for review.")
        else:
            messages.success(
                request,
                "Someone already asked for this firmware: your request was added "
                f"to it ({pending.request_count} people now).",
            )

    return _request_form_view(request, "firmware", FirmwareRequestForm, submit)


def _check_kind(value: str) -> RequestKind | None:
    match value:
        case "brand" | "model" | "variant" | "chipset":
            return value
        case _:
            return None


@require_GET
def request_check(request: HttpRequest) -> HttpResponse:
    """Live "does it already exist?" panel under a request form."""
    if not request.user.is_authenticated:
        raise PermissionDenied
    kind = _check_kind(request.GET.get("kind", ""))
    if kind is None:
        return render(request, "firmwares/requests/_check.html", {"check": None})
    name = _text(request, "name")
    brand_id, model_id = _int(request, "brand"), _int(request, "model")
    brand = Brand.objects.filter(pk=brand_id).first() if brand_id else None
    model = Model.objects.filter(pk=model_id).first() if model_id else None
    check = requests_service.check_existing(
        kind, name, brand=brand, model=model, vendor=_text(request, "vendor")
    )
    return render(
        request,
        "firmwares/requests/_check.html",
        {"check": check, "kind": kind, "name": name},
    )


@verified_user_required
@require_GET
def my_requests(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "firmwares/requests/mine.html",
        {"mine": requests_service.my_requests(request.user)},
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@verified_user_required
@require_GET
def my_uploads(request: HttpRequest) -> HttpResponse:
    """The signed-in user's submissions with review status, tier and price state."""
    submissions = (
        PendingFirmware.objects.filter(uploader=current_user(request))
        .select_related("uploaded_brand", "uploaded_model", "uploaded_variant")
        .order_by("-created_at")[:200]
    )
    return render(
        request,
        "firmwares/my_uploads.html",
        {"submissions": submissions, "seller": is_seller(request.user)},
    )


def _variant_field_context(form: Any, model: Model | None) -> dict[str, Any]:
    """What the upload page's variant field needs to adapt to the chosen model."""
    count = model.variants.filter(is_active=True).count() if model else 0
    return {
        "form": form,
        "has_model": model is not None,
        "variant_required": count > 0,
        "variant_count": count,
    }


@verified_user_required
@require_GET
def upload_variant_field(request: HttpRequest) -> HttpResponse:
    """The variant field of the upload form for the chosen model (HTMX fragment).

    A model with active variants gets a required variant picker; a model without
    any shows "No specific variant" and the upload proceeds without one.
    """
    model_id = _int(request, "model")
    model = Model.objects.filter(pk=model_id).first() if model_id else None
    variant_id = _int(request, "variant")
    variant = (
        Variant.objects.filter(pk=variant_id, model=model).first()
        if model and variant_id
        else None
    )
    form = UploadForm(
        initial={"variant": variant.pk} if variant else None,
        seller=is_seller(request.user),
    )
    return render(
        request,
        "firmwares/fragments/upload_variant.html",
        {
            **_variant_field_context(form, model),
            "variant_label": variant.name if variant else "",
        },
    )


def _become_seller_url() -> str:
    """URL of the marketplace seller application, or ``""`` without that optional app.

    The marketplace is an optional integration: only its public URL name and its
    feature flag are consulted, never its code.
    """
    if not app_enabled("marketplace"):
        return ""
    try:
        return reverse("marketplace:become_seller")
    except NoReverseMatch:
        return ""


@verified_user_required
@require_http_methods(["GET", "POST"])
def upload_page(request: HttpRequest) -> HttpResponse:
    """The end-user upload page: device, file, release details."""
    seller = is_seller(request.user)
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES, seller=seller)
        if form.is_valid():
            try:
                pending = submit_user_upload(request.user, form.to_submission())
            except FirmwareUploadError as error:
                form.add_error(None, str(error))
            except ValueError as error:
                form.add_error(None, str(error))
            else:
                messages.success(
                    request,
                    f"“{pending.original_file_name}” was submitted for review. "
                    "You will be notified when it is published.",
                )
                return redirect("firmwares:my_uploads")
    else:
        form = UploadForm(
            initial=_initial_from_query(request, "brand", "model", "variant"),
            seller=seller,
        )
    return render(
        request,
        "firmwares/upload.html",
        {
            "form": form,
            "seller": seller,
            "become_seller_url": "" if seller else _become_seller_url(),
            "max_upload_mb": max_upload_bytes() // (1024 * 1024),
            **_picker_state(form),
            **_variant_field_context(form, _chosen(form, "model", Model)),
        },
    )

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import mixins, status, views, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import Throttled
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from apps.core.throttling import APIRateThrottle, UploadRateThrottle

from .catalog_requests import (
    CatalogRequestError,
    RequestRateLimited,
    approve_brand_request,
    approve_chipset_request,
    approve_model_request,
    approve_variant_request,
    reject_catalog_request,
    submit_brand_request,
    submit_chipset_request,
    submit_model_request,
    submit_variant_request,
)
from .models import (
    Brand,
    BrandCreationRequest,
    ChipsetCreationRequest,
    Model,
    ModelCreationRequest,
    PendingFirmware,
    Variant,
    VariantCreationRequest,
)
from .serializers import (
    BrandCreationRequestSerializer,
    ChipsetCreationRequestSerializer,
    ExternalFirmwareSubmitSerializer,
    ModelCreationRequestSerializer,
    PendingFirmwareSerializer,
    PendingFirmwareUploadSerializer,
    VariantCreationRequestSerializer,
)
from .services import (
    approve_pending_firmware,
    attempt_extraction,
    handle_upload,
    reject_pending_firmware,
    run_ai_analysis,
    submit_external_firmware,
    withdraw_firmware,
)
from .variant_rules import VariantIdentity


def _resolve_hierarchy(data) -> tuple[Brand | None, Model | None, Variant | None]:
    """Look up the optional brand/model/variant ids; return (brand, model, variant)."""

    def lookup(model_class, key):
        pk = data.get(key)
        obj = model_class.objects.filter(pk=pk).first() if pk else None
        if pk and obj is None:
            raise ValidationError(f"Unknown {key}.")
        return obj

    return (
        lookup(Brand, "brand_id"),
        lookup(Model, "model_id"),
        lookup(Variant, "variant_id"),
    )


class _SubmissionEndpoint(views.APIView):
    """Machine endpoint for submissions: JSON only, with a pointer for browsers.

    People submit firmware on the HTML page (``firmwares:upload``). A browser that
    opens this address is sent there instead of seeing a bare API form.
    """

    renderer_classes = (JSONRenderer,)
    permission_classes = (IsAuthenticated,)
    throttle_classes = (UploadRateThrottle,)

    def perform_content_negotiation(self, request, force=False):
        # A browser asks for HTML; answer it (with a redirect) instead of a 406.
        return super().perform_content_negotiation(request, force=True)

    def get(self, request):
        if "text/html" in request.headers.get("Accept", ""):
            return HttpResponseRedirect(reverse("firmwares:upload"))
        return Response(
            {"detail": "Send a POST request to submit firmware."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )


class FirmwareUploadView(_SubmissionEndpoint):
    def post(self, request):
        ser = PendingFirmwareUploadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            brand, model, variant = _resolve_hierarchy(data)
            pf = handle_upload(
                uploader=request.user,
                uploaded_brand=brand,
                uploaded_model=model,
                uploaded_variant=variant,
                file_obj=data["file"],
                is_password_protected=data["is_password_protected"],
                password=data.get("password"),
                extra_info=data.get("extra_info"),
                package_format=data["package_format"],
                access_tier=data["access_tier"],
                proposed_price=data.get("proposed_price"),
            )
        except ValidationError as exc:
            return Response(
                {"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        attempt_extraction(pf)
        run_ai_analysis(pf)
        return Response(
            PendingFirmwareSerializer(pf).data, status=status.HTTP_201_CREATED
        )


class ExternalFirmwareSubmitView(_SubmissionEndpoint):
    """Submit firmware hosted on an allowlisted external host for review."""

    def post(self, request):
        ser = ExternalFirmwareSubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            brand, model, variant = _resolve_hierarchy(data)
            pf = submit_external_firmware(
                uploader=request.user,
                url=data["url"],
                original_file_name=data["file_name"],
                uploaded_brand=brand,
                uploaded_model=model,
                uploaded_variant=variant,
                package_format=data["package_format"],
                extra_info=data.get("extra_info"),
                file_size=data.get("file_size"),
                file_hash=data.get("file_hash", ""),
                access_tier=data["access_tier"],
                proposed_price=data.get("proposed_price"),
            )
        except ValidationError as exc:
            return Response(
                {"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            PendingFirmwareSerializer(pf).data, status=status.HTTP_201_CREATED
        )


class PendingFirmwareViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = (IsAdminUser,)
    throttle_classes = (APIRateThrottle,)
    queryset = PendingFirmware.objects.all().order_by("-created_at")
    serializer_class = PendingFirmwareSerializer


class ModerationView(views.APIView):
    """Staff decision on a pending upload: approved, rejected or withdrawn."""

    permission_classes = (IsAdminUser,)

    def post(self, request, pk: str):
        pf = get_object_or_404(PendingFirmware, pk=pk)
        decision = request.data.get("decision", "")
        notes = request.data.get("notes", "")
        try:
            if decision == "approved":
                brand, model, variant = _resolve_hierarchy(request.data)
                record = approve_pending_firmware(
                    pf,
                    request.user,
                    category=request.data.get("category") or None,
                    subtype=request.data.get("subtype"),
                    brand=brand,
                    model=model,
                    variant=variant,
                    notes=notes,
                    unclassified_reason=request.data.get("unclassified_reason", ""),
                    package_format=request.data.get("package_format") or None,
                    access_tier=request.data.get("access_tier") or None,
                    approved_price=request.data.get("approved_price"),
                )
                return Response({"status": "approved", "firmware_id": str(record.pk)})
            if decision == "rejected":
                reject_pending_firmware(pf, request.user, notes=notes)
                return Response({"status": "rejected"})
            if decision == "withdrawn":
                withdraw_firmware(pf, request.user, reason=notes)
                return Response({"status": "pending"})
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return Response(
                {"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            {"detail": "Decision must be approved, rejected or withdrawn."},
            status=status.HTTP_400_BAD_REQUEST,
        )


_CreationRequestModel = (
    type[BrandCreationRequest]
    | type[ModelCreationRequest]
    | type[VariantCreationRequest]
    | type[ChipsetCreationRequest]
)


class _CreationRequestViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """End users create and read their own requests; staff review them.

    Creation and the decisions go through the same services as the pages and the
    admin queues, so dedupe, rate limits and notifications cannot diverge.
    """

    permission_classes = (IsAuthenticated,)
    request_model: _CreationRequestModel

    def submit(self, user, data):
        raise NotImplementedError

    def approve_request(self, pending, user, reason):
        raise NotImplementedError

    def get_queryset(self):
        queryset = self.request_model.objects.all().order_by("-created_at")
        user = self.request.user
        if getattr(user, "is_staff", False):
            return queryset
        if isinstance(user, AnonymousUser):
            return queryset.none()
        return queryset.filter(requested_by=user)

    def perform_create(self, serializer):
        try:
            serializer.instance = self.submit(
                self.request.user, serializer.validated_data
            )
        except RequestRateLimited as error:
            raise Throttled(detail=str(error)) from error
        except CatalogRequestError as error:
            raise DRFValidationError({"detail": [str(error)]}) from error

    @action(detail=True, methods=["post"], permission_classes=(IsAdminUser,))
    def approve(self, request, *_args, **_kwargs):
        pending = self.get_object()
        try:
            self.approve_request(pending, request.user, request.data.get("reason", ""))
        except PermissionDenied as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as error:
            return Response(
                {"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(self.get_serializer(pending).data)

    @action(detail=True, methods=["post"], permission_classes=(IsAdminUser,))
    def reject(self, request, *_args, **_kwargs):
        pending = self.get_object()
        try:
            reject_catalog_request(
                pending, request.user, reason=request.data.get("reason", "")
            )
        except PermissionDenied as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as error:
            return Response(
                {"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(self.get_serializer(pending).data)


class BrandCreationRequestViewSet(_CreationRequestViewSet):
    request_model = BrandCreationRequest
    serializer_class = BrandCreationRequestSerializer

    def approve_request(self, pending, user, reason):
        return approve_brand_request(pending, user, reason=reason)

    def submit(self, user, data):
        return submit_brand_request(
            user, name=data["name"], details=data.get("details", "")
        )


class ModelCreationRequestViewSet(_CreationRequestViewSet):
    request_model = ModelCreationRequest
    serializer_class = ModelCreationRequestSerializer

    def approve_request(self, pending, user, reason):
        return approve_model_request(pending, user, reason=reason)

    def submit(self, user, data):
        return submit_model_request(
            user,
            brand=data["brand"],
            name=data["name"],
            model_code=data.get("model_code", ""),
            chipset=data.get("chipset"),
            details=data.get("details", ""),
        )


class ChipsetCreationRequestViewSet(_CreationRequestViewSet):
    request_model = ChipsetCreationRequest
    serializer_class = ChipsetCreationRequestSerializer

    def approve_request(self, pending, user, reason):
        return approve_chipset_request(pending, user, reason=reason)

    def submit(self, user, data):
        return submit_chipset_request(
            user,
            vendor=data["vendor"],
            name=data["name"],
            family=data.get("family", ""),
            aliases=data.get("aliases", []),
            details=data.get("details", ""),
        )


class VariantCreationRequestViewSet(_CreationRequestViewSet):
    request_model = VariantCreationRequest
    serializer_class = VariantCreationRequestSerializer

    def approve_request(self, pending, user, reason):
        return approve_variant_request(pending, user, reason=reason)

    def submit(self, user, data):
        return submit_variant_request(
            user,
            model=data["model"],
            name=data.get("name", ""),
            identity=VariantIdentity(
                model_code=data.get("model_code", ""),
                region=data.get("region", ""),
                memory=data.get("memory", ""),
                carrier=data.get("carrier", ""),
            ),
            details=data.get("details", ""),
        )

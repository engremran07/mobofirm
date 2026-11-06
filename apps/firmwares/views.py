from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status, views, viewsets
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from apps.core.throttling import APIRateThrottle, UploadRateThrottle

from .models import (
    Brand,
    BrandCreationRequest,
    Model,
    ModelCreationRequest,
    PendingFirmware,
    SchemaUpdateProposal,
    Variant,
    VariantCreationRequest,
)
from .serializers import (
    BrandCreationRequestSerializer,
    ExternalFirmwareSubmitSerializer,
    ModelCreationRequestSerializer,
    PendingFirmwareSerializer,
    PendingFirmwareUploadSerializer,
    SchemaUpdateProposalSerializer,
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


def _resolve_hierarchy(data):
    """Look up the optional brand/model/variant ids; return (brand, model, variant)."""
    found = []
    for model_class, key in (
        (Brand, "brand_id"),
        (Model, "model_id"),
        (Variant, "variant_id"),
    ):
        pk = data.get(key)
        obj = model_class.objects.filter(pk=pk).first() if pk else None
        if pk and obj is None:
            raise ValidationError(f"Unknown {key}.")
        found.append(obj)
    return tuple(found)


class FirmwareUploadView(views.APIView):
    permission_classes = (IsAuthenticated,)
    throttle_classes = (UploadRateThrottle,)

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


class ExternalFirmwareSubmitView(views.APIView):
    """Submit firmware hosted on an allowlisted external host for review."""

    permission_classes = (IsAuthenticated,)
    throttle_classes = (UploadRateThrottle,)

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


class SchemaUpdateProposalViewSet(viewsets.ModelViewSet):
    permission_classes = (IsAdminUser,)
    queryset = SchemaUpdateProposal.objects.all().order_by("-created_at")
    serializer_class = SchemaUpdateProposalSerializer


_CreationRequestModel = (
    type[BrandCreationRequest]
    | type[ModelCreationRequest]
    | type[VariantCreationRequest]
)


class _CreationRequestViewSet(viewsets.ModelViewSet):
    """Owner-scoped creation requests: users create and read their own; staff manage."""

    permission_classes = (IsAuthenticated,)
    request_model: _CreationRequestModel

    def get_permissions(self):
        if self.action in {"update", "partial_update", "destroy"}:
            return [IsAdminUser()]
        return super().get_permissions()

    def get_queryset(self):
        queryset = self.request_model.objects.all().order_by("-created_at")
        user = self.request.user
        if getattr(user, "is_staff", False):
            return queryset
        if isinstance(user, AnonymousUser):
            return queryset.none()
        return queryset.filter(requested_by=user)

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)


class BrandCreationRequestViewSet(_CreationRequestViewSet):
    request_model = BrandCreationRequest
    serializer_class = BrandCreationRequestSerializer


class ModelCreationRequestViewSet(_CreationRequestViewSet):
    request_model = ModelCreationRequest
    serializer_class = ModelCreationRequestSerializer


class VariantCreationRequestViewSet(_CreationRequestViewSet):
    request_model = VariantCreationRequest
    serializer_class = VariantCreationRequestSerializer

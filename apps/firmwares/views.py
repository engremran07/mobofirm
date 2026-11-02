from django.shortcuts import get_object_or_404
from rest_framework import status, views, viewsets
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from apps.core.throttling import APIRateThrottle, UploadRateThrottle
from apps.governance.models import ReviewCase
from apps.governance.services import GovernanceError, decide_case, start_review
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError

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
    ModelCreationRequestSerializer,
    PendingFirmwareSerializer,
    PendingFirmwareUploadSerializer,
    SchemaUpdateProposalSerializer,
    VariantCreationRequestSerializer,
)
from .services import (
    attempt_extraction,
    handle_upload,
    run_ai_analysis,
)


class FirmwareUploadView(views.APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadRateThrottle]

    def post(self, request):
        ser = PendingFirmwareUploadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        brand = (
            Brand.objects.filter(id=data.get("brand_id")).first()
            if data.get("brand_id")
            else None
        )
        model = (
            Model.objects.filter(id=data.get("model_id")).first()
            if data.get("model_id")
            else None
        )
        variant = (
            Variant.objects.filter(id=data.get("variant_id")).first()
            if data.get("variant_id")
            else None
        )
        try:
            pf = handle_upload(
                uploader=request.user,
                uploaded_brand=brand,
                uploaded_model=model,
                uploaded_variant=variant,
                file_obj=data["file"],
                is_password_protected=data["is_password_protected"],
                password=data.get("password"),
                extra_info=data.get("extra_info"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        attempt_extraction(pf)
        run_ai_analysis(pf)
        return Response(
            PendingFirmwareSerializer(pf).data, status=status.HTTP_201_CREATED
        )


class PendingFirmwareViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminUser]
    throttle_classes = [APIRateThrottle]
    queryset = PendingFirmware.objects.all().order_by("-created_at")
    serializer_class = PendingFirmwareSerializer


class ModerationView(views.APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk: str):
        pf = get_object_or_404(PendingFirmware, pk=pk)
        decision = request.data.get("decision", "")
        rationale = request.data.get("notes", "")
        content_type = ContentType.objects.get_for_model(pf, for_concrete_model=False)
        case = get_object_or_404(
            ReviewCase,
            content_type=content_type,
            object_id=str(pf.pk),
            is_open=True,
        )
        if decision not in {"approved", "rejected", "request_changes"}:
            return Response(
                {"detail": "Unsupported governance decision."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        service_decision = {
            "approved": "approve",
            "rejected": "reject",
            "request_changes": "request_changes",
        }[decision]
        try:
            if case.status == ReviewCase.Status.SUBMITTED:
                case = start_review(case.pk, request.user, rationale=rationale)
            case = decide_case(
                case.pk,
                request.user,
                service_decision,
                rationale,
                decision_data={
                    "category": request.data.get("category"),
                    "subtype": request.data.get("subtype"),
                    "brand_id": request.data.get("brand_id"),
                    "model_id": request.data.get("model_id"),
                    "variant_id": request.data.get("variant_id"),
                    "unclassified_reason": request.data.get("unclassified_reason", ""),
                }
                if decision == "approved"
                else None,
            )
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        except GovernanceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"case_id": case.pk, "status": case.status})


class SchemaUpdateProposalViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
    queryset = SchemaUpdateProposal.objects.all().order_by("-created_at")
    serializer_class = SchemaUpdateProposalSerializer


class BrandCreationRequestViewSet(viewsets.ModelViewSet):
    # FINDING-0009: scope queryset to requesting user to prevent IDOR.
    # Admin users see all records; regular users only see their own submissions.
    permission_classes = [IsAuthenticated]
    serializer_class = BrandCreationRequestSerializer

    def get_queryset(self):  # type: ignore[override]
        user = self.request.user
        if getattr(user, "is_staff", False):
            return BrandCreationRequest.objects.all().order_by("-created_at")
        return BrandCreationRequest.objects.filter(submitted_by=user).order_by(
            "-created_at"
        )


class ModelCreationRequestViewSet(viewsets.ModelViewSet):
    # FINDING-0009: scope queryset to requesting user to prevent IDOR.
    permission_classes = [IsAuthenticated]
    serializer_class = ModelCreationRequestSerializer

    def get_queryset(self):  # type: ignore[override]
        user = self.request.user
        if getattr(user, "is_staff", False):
            return ModelCreationRequest.objects.all().order_by("-created_at")
        return ModelCreationRequest.objects.filter(submitted_by=user).order_by(
            "-created_at"
        )


class VariantCreationRequestViewSet(viewsets.ModelViewSet):
    # FINDING-0009: scope queryset to requesting user to prevent IDOR.
    permission_classes = [IsAuthenticated]
    serializer_class = VariantCreationRequestSerializer

    def get_queryset(self):  # type: ignore[override]
        user = self.request.user
        if getattr(user, "is_staff", False):
            return VariantCreationRequest.objects.all().order_by("-created_at")
        return VariantCreationRequest.objects.filter(submitted_by=user).order_by(
            "-created_at"
        )

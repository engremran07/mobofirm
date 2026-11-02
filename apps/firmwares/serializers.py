from django.core.validators import MinLengthValidator
from rest_framework import serializers

from .models import (
    BrandCreationRequest,
    ModelCreationRequest,
    PendingFirmware,
    SchemaUpdateProposal,
    VariantCreationRequest,
)


class PendingFirmwareUploadSerializer(serializers.Serializer):
    brand_id = serializers.IntegerField(required=False, allow_null=True)
    model_id = serializers.IntegerField(required=False, allow_null=True)
    variant_id = serializers.IntegerField(required=False, allow_null=True)
    is_password_protected = serializers.BooleanField()
    password = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        validators=[MinLengthValidator(1)],
    )
    extra_info = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    file = serializers.FileField()


class PendingFirmwareSerializer(serializers.ModelSerializer):
    class Meta:
        model = PendingFirmware
        fields = [
            "id",
            "original_file_name",
            "uploader",
            "uploaded_brand",
            "uploaded_model",
            "uploaded_variant",
            "ai_brand",
            "ai_model",
            "ai_variant",
            "status",
            "extra_info",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "uploader", "created_at", "updated_at"]


class SchemaUpdateProposalSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchemaUpdateProposal
        fields = [
            "id",
            "brand",
            "proposed_by",
            "schema_json",
            "status",
            "reviewed_by",
            "reviewed_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "proposed_by",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]


class BrandCreationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandCreationRequest
        fields = [
            "id",
            "name",
            "requested_by",
            "ai_suggestion",
            "status",
            "reviewed_by",
            "reviewed_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "requested_by",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]


class ModelCreationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelCreationRequest
        fields = [
            "id",
            "brand",
            "name",
            "requested_by",
            "ai_suggestion",
            "status",
            "reviewed_by",
            "reviewed_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "requested_by",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]


class VariantCreationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = VariantCreationRequest
        fields = [
            "id",
            "model",
            "name",
            "requested_by",
            "ai_suggestion",
            "status",
            "reviewed_by",
            "reviewed_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "requested_by",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]

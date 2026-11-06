from typing import ClassVar

from django.core.validators import MinLengthValidator
from rest_framework import serializers

from .models import (
    BrandCreationRequest,
    FirmwarePackageFormat,
    ModelCreationRequest,
    PendingFirmware,
    SchemaUpdateProposal,
    VariantCreationRequest,
)


class _HierarchyFields(serializers.Serializer):
    brand_id = serializers.IntegerField(required=False, allow_null=True)
    model_id = serializers.IntegerField(required=False, allow_null=True)
    variant_id = serializers.IntegerField(required=False, allow_null=True)
    package_format = serializers.ChoiceField(
        choices=FirmwarePackageFormat.choices,
        required=False,
        default=FirmwarePackageFormat.UNKNOWN,
    )
    extra_info = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


class PendingFirmwareUploadSerializer(_HierarchyFields):
    is_password_protected = serializers.BooleanField()
    password = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        validators=[MinLengthValidator(1)],
    )
    file = serializers.FileField()


class ExternalFirmwareSubmitSerializer(_HierarchyFields):
    url = serializers.URLField(max_length=500)
    file_name = serializers.CharField(max_length=255)
    file_size = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    file_hash = serializers.CharField(required=False, allow_blank=True, max_length=64)


class PendingFirmwareSerializer(serializers.ModelSerializer):
    status = serializers.CharField(source="admin_decision", read_only=True)
    extra_info = serializers.SerializerMethodField()

    class Meta:
        model = PendingFirmware
        fields: ClassVar[list[str]] = [
            "id",
            "original_file_name",
            "source_type",
            "external_url",
            "file_size",
            "file_hash",
            "uploader",
            "uploaded_brand",
            "uploaded_model",
            "uploaded_variant",
            "package_format",
            "ai_brand",
            "ai_model",
            "ai_variant",
            "status",
            "extra_info",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_extra_info(self, obj: PendingFirmware) -> str:
        return str((obj.metadata or {}).get("extra_info", ""))


class SchemaUpdateProposalSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchemaUpdateProposal
        fields: ClassVar[list[str]] = [
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
        read_only_fields: ClassVar[list[str]] = [
            "id",
            "proposed_by",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]


_REQUEST_READ_ONLY = [
    "id",
    "requested_by",
    "ai_suggestion",
    "status",
    "reviewed_by",
    "reviewed_at",
    "notes",
    "created_at",
    "updated_at",
]


class BrandCreationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandCreationRequest
        fields: ClassVar[list[str]] = [
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
        read_only_fields = _REQUEST_READ_ONLY


class ModelCreationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelCreationRequest
        fields: ClassVar[list[str]] = [
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
        read_only_fields = _REQUEST_READ_ONLY


class VariantCreationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = VariantCreationRequest
        fields: ClassVar[list[str]] = [
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
        read_only_fields = _REQUEST_READ_ONLY

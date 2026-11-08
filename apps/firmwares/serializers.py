from typing import Any, ClassVar

from django.core.validators import MinLengthValidator
from rest_framework import serializers

from .models import (
    AccessTier,
    Brand,
    BrandCreationRequest,
    ChipsetCreationRequest,
    FirmwarePackageFormat,
    ModelCreationRequest,
    PendingFirmware,
    VariantCreationRequest,
)


class _HierarchyFields(serializers.Serializer):
    """Input-only serializer base: validates a payload and is never saved."""

    def create(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError(f"{type(self).__name__} validates input only; it cannot save.")

    def update(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError(f"{type(self).__name__} validates input only; it cannot save.")

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
    access_tier = serializers.ChoiceField(
        choices=AccessTier.choices, required=False, default=AccessTier.FREE
    )
    proposed_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
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
            "access_tier",
            "proposed_price",
            "approved_price",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_extra_info(self, obj: PendingFirmware) -> str:
        return str((obj.metadata or {}).get("extra_info", ""))


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
            "details",
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
            "chipset",
            "name",
            "model_code",
            "details",
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


class ChipsetCreationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChipsetCreationRequest
        fields: ClassVar[list[str]] = [
            "id",
            "vendor",
            "name",
            "family",
            "aliases",
            "details",
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
    brand = serializers.PrimaryKeyRelatedField(
        queryset=Brand.objects.all(),
        write_only=True,
        help_text="Brand of the model; the model must belong to it",
    )

    class Meta:
        model = VariantCreationRequest
        fields: ClassVar[list[str]] = [
            "id",
            "brand",
            "model",
            "name",
            "model_code",
            "region",
            "details",
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

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["model"].brand_id != attrs["brand"].pk:
            raise serializers.ValidationError(
                {"model": "This model does not belong to the chosen brand."}
            )
        return attrs

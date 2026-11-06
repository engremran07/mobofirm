from __future__ import annotations

import uuid
from importlib import import_module
from typing import ClassVar

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q

from .constants import MAIN_CATEGORIES, RESERVED_BRAND_SLUGS


class FirmwarePackageFormat(models.TextChoices):
    UNKNOWN = "unknown", "Not specified"
    RECOVERY_ROM = "recovery_rom", "Recovery ROM"
    FASTBOOT_ROM = "fastboot_rom", "Fastboot ROM"
    FULL_OTA = "full_ota", "Full OTA"
    INCREMENTAL_OTA = "incremental_ota", "Incremental OTA"
    FACTORY_IMAGE = "factory_image", "Factory image"
    SERVICE_PACKAGE = "service_package", "Service package"
    PARTITION_IMAGE = "partition_image", "Partition image"
    OTHER = "other", "Other package format"


class FirmwareSourceType(models.TextChoices):
    STORED = "stored", "Stored file"
    EXTERNAL = "external", "External download"


def source_constraint(name: str) -> models.CheckConstraint:
    """Exactly one of a stored file path or an external URL describes the file."""
    return models.CheckConstraint(
        condition=(
            Q(source_type=FirmwareSourceType.STORED)
            & ~Q(stored_file_path="")
            & Q(external_url="")
        )
        | (
            Q(source_type=FirmwareSourceType.EXTERNAL)
            & Q(stored_file_path="")
            & ~Q(external_url="")
        ),
        name=name,
    )


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def _validate_source_fields(instance) -> None:
    """Admin/form validation of the stored-vs-external source fields."""
    from django.core.exceptions import ValidationError

    from .sources import validate_external_url

    if instance.source_type == FirmwareSourceType.EXTERNAL:
        if instance.stored_file_path:
            raise ValidationError("External firmware must not have a stored file path.")
        instance.external_url = validate_external_url(instance.external_url)
    else:
        if instance.external_url:
            raise ValidationError("Stored firmware must not have an external URL.")
        if not instance.stored_file_path:
            raise ValidationError("Stored firmware requires a stored file path.")


class Brand(Timestamped):
    firmware_count: int  # public firmware total, set by catalogue listing views

    name = models.CharField(max_length=128, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    logo_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Brand logo URL (SVG preferred)",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Brief description about the brand and its firmware",
    )
    website_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Official brand website URL",
    )
    is_featured = models.BooleanField(
        default=False, help_text="Show prominently on device catalog page"
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]
        verbose_name = "Brand"
        verbose_name_plural = "Brands"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=~Q(slug__in=RESERVED_BRAND_SLUGS),
                name="brand_slug_not_reserved",
            )
        ]

    def __str__(self):
        return self.name


class Chipset(Timestamped):
    """A system-on-chip that models and variants reference and tools support."""

    vendor = models.CharField(max_length=64, help_text="e.g. MediaTek, Qualcomm")
    family = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Product family, e.g. Helio, Snapdragon, Dimensity",
    )
    name = models.CharField(max_length=128, help_text="e.g. Helio G100 Ultra")
    slug = models.SlugField(max_length=160, unique=True)
    aliases = models.JSONField(
        default=list,
        blank=True,
        help_text="Alternate spellings that resolve to this chipset",
    )
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering: ClassVar[list[str]] = ["vendor", "name"]
        verbose_name = "Chipset"
        verbose_name_plural = "Chipsets"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["vendor", "name"], name="chipset_vendor_name_unique"
            )
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["vendor", "family"], name="chipset_vendor_idx")
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return f"{self.vendor} {self.name}".strip()


class Model(Timestamped):
    firmware_count: int  # public firmware total, set by catalogue listing views

    MODEL_STATUS_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("available", "Available"),
        ("discontinued", "Discontinued"),
        ("upcoming", "Upcoming"),
        ("rumored", "Rumored"),
    ]

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="models")
    name = models.CharField(
        max_length=128, help_text="Device model name (e.g., Galaxy S23)"
    )
    slug = models.SlugField(max_length=160)

    # Marketing & identification
    marketing_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Official marketing name (e.g., Samsung Galaxy S23 Ultra)",
    )
    codename = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Internal codename (e.g., spinel, shiba, SM-S911B)",
    )
    model_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Primary model code (e.g., SM-S911B)",
    )
    model_codes = models.JSONField(
        default=list,
        blank=True,
        help_text='All variant model codes (e.g., ["2510DRA23G", "2510DRA23E"])',
    )
    also_known_as = models.TextField(
        blank=True,
        default="",
        help_text="Alternate names, comma-separated (e.g., POCO C81 Pro, Redmi A7 Pro)",
    )
    description = models.TextField(
        blank=True, default="", help_text="Model description for SEO"
    )

    # Device image
    image_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Device photo URL",
    )

    # Core specifications
    chipset = models.CharField(
        max_length=256,
        blank=True,
        default="",
        help_text="Primary chipset (e.g., Mediatek Helio G100 Ultra (6nm))",
    )
    chipset_ref = models.ForeignKey(
        Chipset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="models",
        help_text="Normalised chipset record resolved from the chipset text",
    )
    cpu = models.CharField(
        max_length=256,
        blank=True,
        default="",
        help_text="CPU description (e.g., Octa-core 2x2.2 GHz Cortex-A76)",
    )
    gpu = models.CharField(
        max_length=256,
        blank=True,
        default="",
        help_text="GPU description (e.g., Mali-G57 MC2)",
    )
    network_technology = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Network type (e.g., GSM / HSPA / LTE / 5G)",
    )
    os_version = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Launch OS version (e.g., Android 15, HyperOS 2)",
    )

    # Hardware specs
    ram_options = models.JSONField(
        default=list,
        blank=True,
        help_text='RAM options in GB (e.g., ["4GB", "6GB", "8GB", "12GB"])',
    )
    storage_options = models.JSONField(
        default=list,
        blank=True,
        help_text='Storage options (e.g., ["64GB", "128GB", "256GB", "512GB"])',
    )
    colors = models.JSONField(
        default=list,
        blank=True,
        help_text='Available colors (e.g., ["Black", "Forest Green", "Glacier Blue"])',
    )
    display_size = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text='Display size (e.g., 6.67")',
    )
    battery = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Battery capacity (e.g., 5160 mAh)",
    )
    dimensions = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Dimensions (e.g., 164 x 75.7 x 7.8 mm)",
    )
    weight = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Weight (e.g., 190 g)",
    )

    # Availability
    release_date = models.DateField(
        null=True, blank=True, help_text="Official release date"
    )
    announced_date = models.DateField(
        null=True, blank=True, help_text="Announcement date"
    )
    status = models.CharField(
        max_length=20,
        choices=MODEL_STATUS_CHOICES,
        default="available",
        help_text="Market availability status",
    )
    regions = models.JSONField(
        default=list,
        blank=True,
        help_text='Regions with firmware availability (e.g., ["China", "Global", "EEA"])',
    )
    is_active = models.BooleanField(
        default=True, help_text="Is this model still supported?"
    )

    class Meta:
        unique_together = ("brand", "slug")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["brand", "is_active"], name="model_brand_active_idx"),
            models.Index(fields=["model_code"], name="model_code_idx"),
            models.Index(fields=["codename"], name="model_codename_idx"),
            models.Index(fields=["status"], name="model_status_idx"),
            models.Index(fields=["chipset"], name="model_chipset_idx"),
        ]

    def __str__(self):
        if self.codename:
            return f"{self.brand}/{self.name} ({self.codename})"
        return f"{self.brand}/{self.name}"

    def save(self, *args, **kwargs):
        from .chipsets import sync_chipset_reference

        kwargs = sync_chipset_reference(self, kwargs)
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        """Marketing name if available, else model name."""
        return self.marketing_name or self.name

    @property
    def all_model_codes_display(self) -> str:
        """Comma-separated model codes for display."""
        codes = list(self.model_codes) if self.model_codes else []
        if self.model_code and self.model_code not in codes:
            codes.insert(0, self.model_code)
        return ", ".join(codes)


class Variant(Timestamped):
    model = models.ForeignKey(Model, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField(
        max_length=128, help_text="Variant name (e.g., Global, US, EU)"
    )
    slug = models.SlugField(max_length=160)
    model_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Manufacturer model code for this regional variant",
    )

    # Identification
    region = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Region code (e.g., SM, EU, US, CN)",
    )
    board_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Board ID for hardware identification",
    )

    # Chipset information
    chipset = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Primary chipset (e.g., Snapdragon 8 Gen 2)",
    )
    chipset_ref = models.ForeignKey(
        Chipset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="variants",
        help_text="Normalised chipset record resolved from the chipset text",
    )

    # Specifications
    ram_options = models.JSONField(
        default=list,
        blank=True,
        help_text="Available RAM options (e.g., [4, 6, 8, 12])",
    )
    storage_options = models.JSONField(
        default=list,
        blank=True,
        help_text="Available storage options (e.g., [64, 128, 256, 512])",
    )

    # Status
    is_active = models.BooleanField(
        default=True, help_text="Is this variant still supported?"
    )

    class Meta:
        unique_together = ("model", "slug")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["model", "is_active"], name="variant_model_active_idx"
            ),
            models.Index(fields=["chipset"], name="variant_chipset_idx"),
            models.Index(fields=["region"], name="variant_region_idx"),
        ]

    def __str__(self):
        return f"{self.model}/{self.name}"

    def save(self, *args, **kwargs):
        from .chipsets import sync_chipset_reference

        kwargs = sync_chipset_reference(self, kwargs)
        super().save(*args, **kwargs)


class BrandSchema(Timestamped):
    brand = models.OneToOneField(Brand, on_delete=models.CASCADE, related_name="schema")
    schema_json = models.JSONField(default=dict)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_brand_schemas",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Brand Schema"
        verbose_name_plural = "Brand Schemas"

    def __str__(self) -> str:
        return f"Schema for {self.brand}"


class SchemaUpdateProposal(Timestamped):
    brand = models.ForeignKey(
        Brand, on_delete=models.CASCADE, related_name="schema_proposals"
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="schema_proposals_created",
    )
    schema_json = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "pending"),
            ("approved", "approved"),
            ("rejected", "rejected"),
        ],
        default="pending",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="schema_proposal_reviews",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Schema Update Proposal"
        verbose_name_plural = "Schema Update Proposals"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"Proposal #{self.pk} for {self.brand}"


class BrandCreationRequest(Timestamped):
    name = models.CharField(max_length=128)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="brand_creation_requests",
    )
    ai_suggestion = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "pending"),
            ("approved", "approved"),
            ("rejected", "rejected"),
        ],
        default="pending",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="brand_creation_reviews",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Brand Creation Request"
        verbose_name_plural = "Brand Creation Requests"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"Brand Request: {self.name}"


class ModelCreationRequest(Timestamped):
    brand = models.ForeignKey(
        Brand, on_delete=models.CASCADE, related_name="model_creation_requests"
    )
    name = models.CharField(max_length=128)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="model_creation_requests",
    )
    ai_suggestion = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "pending"),
            ("approved", "approved"),
            ("rejected", "rejected"),
        ],
        default="pending",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="model_creation_reviews",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Model Creation Request"
        verbose_name_plural = "Model Creation Requests"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"Model Request: {self.name}"


class VariantCreationRequest(Timestamped):
    model = models.ForeignKey(
        Model, on_delete=models.CASCADE, related_name="variant_creation_requests"
    )
    name = models.CharField(max_length=128)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="variant_creation_requests",
    )
    ai_suggestion = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "pending"),
            ("approved", "approved"),
            ("rejected", "rejected"),
        ],
        default="pending",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="variant_creation_reviews",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Variant Creation Request"
        verbose_name_plural = "Variant Creation Requests"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"Variant Request: {self.name}"


class PendingFirmware(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_file_name = models.CharField(max_length=255)
    source_type = models.CharField(
        max_length=16,
        choices=FirmwareSourceType.choices,
        default=FirmwareSourceType.STORED,
    )
    stored_file_path = models.CharField(max_length=500, blank=True, default="")
    external_url = models.URLField(max_length=500, blank=True, default="")
    file_size = models.BigIntegerField(null=True, blank=True)
    file_hash = models.CharField(max_length=256, blank=True, default="")
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="pending_firmwares_uploaded",
    )
    uploaded_brand = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_firmwares_brand",
    )
    uploaded_model = models.ForeignKey(
        Model,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_firmwares_model",
    )
    uploaded_variant = models.ForeignKey(
        Variant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_firmwares_variant",
    )
    ai_brand = models.CharField(max_length=128, blank=True, default="")
    ai_model = models.CharField(max_length=128, blank=True, default="")
    ai_variant = models.CharField(max_length=128, blank=True, default="")
    ai_category = models.CharField(
        max_length=32, choices=MAIN_CATEGORIES, blank=True, null=True
    )
    ai_subtype = models.CharField(max_length=64, blank=True, null=True)
    package_format = models.CharField(
        max_length=32,
        choices=FirmwarePackageFormat.choices,
        default=FirmwarePackageFormat.UNKNOWN,
    )
    chipset = models.CharField(max_length=128, blank=True, default="")
    partitions = models.JSONField(default=list, blank=True)
    is_password_protected = models.BooleanField(default=False)
    encrypted_password = models.CharField(max_length=512, blank=True, default="")
    password_validation_status = models.CharField(
        max_length=32,
        choices=[("unknown", "unknown"), ("valid", "valid"), ("invalid", "invalid")],
        default="unknown",
    )
    extraction_status = models.CharField(
        max_length=32,
        choices=[("pending", "pending"), ("success", "success"), ("failed", "failed")],
        default="pending",
    )
    metadata = models.JSONField(default=dict, blank=True)
    admin_decision = models.CharField(
        max_length=32,
        choices=[
            ("pending", "pending"),
            ("approved", "approved"),
            ("rejected", "rejected"),
        ],
        default="pending",
    )
    admin_notes = models.TextField(blank=True, default="")
    category_locked = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pending_firmwares_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    routed_category = models.CharField(max_length=32, blank=True, default="")
    routed_object_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            source_constraint("pending_fw_exactly_one_source"),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["admin_decision", "-created_at"], name="pending_fw_decision_idx"
            )
        ]

    def __str__(self):
        return f"{self.original_file_name} ({self.id})"

    def clean(self):
        super().clean()
        _validate_source_fields(self)


class BaseFirmware(Timestamped):
    objects = models.Manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_file_name = models.CharField(max_length=255, help_text="Original filename")
    source_type = models.CharField(
        max_length=16,
        choices=FirmwareSourceType.choices,
        default=FirmwareSourceType.STORED,
        help_text="Whether the file is stored locally or hosted externally",
    )
    stored_file_path = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Storage path in system (stored files only)",
    )
    external_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="HTTPS download URL on an allowed host (external files only)",
    )
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="%(class)s_uploaded",
    )

    # Hierarchy
    brand = models.ForeignKey(
        Brand, on_delete=models.SET_NULL, null=True, related_name="%(class)s_firmware"
    )
    model = models.ForeignKey(
        Model, on_delete=models.SET_NULL, null=True, related_name="%(class)s_firmware"
    )
    variant = models.ForeignKey(
        Variant, on_delete=models.SET_NULL, null=True, related_name="%(class)s_firmware"
    )

    # Technical details
    chipset = models.CharField(
        max_length=128, blank=True, default="", help_text="Chipset/Processor"
    )
    android_version = models.CharField(
        max_length=32, blank=True, default="", help_text="Android version (e.g., 14.0)"
    )
    security_patch = models.DateField(
        null=True, blank=True, help_text="Security patch date"
    )
    build_date = models.DateField(null=True, blank=True, help_text="Build date")
    build_number = models.CharField(
        max_length=128, blank=True, default="", help_text="Build number/version"
    )

    # File information
    file_size = models.BigIntegerField(
        null=True, blank=True, help_text="File size in bytes"
    )
    file_hash = models.CharField(
        max_length=256, blank=True, default="", help_text="SHA256 hash"
    )
    package_format = models.CharField(
        max_length=32,
        choices=FirmwarePackageFormat.choices,
        default=FirmwarePackageFormat.UNKNOWN,
        help_text="Package layout, distinct from firmware review/classification type",
    )

    # Structure
    partitions = models.JSONField(default=list, blank=True, help_text="Partition list")

    # Security
    is_password_protected = models.BooleanField(default=False)
    encrypted_password = models.CharField(max_length=512, blank=True, default="")

    # Metadata & tracking
    metadata = models.JSONField(
        default=dict, blank=True, help_text="Additional metadata"
    )
    download_count = models.PositiveIntegerField(default=0, help_text="Total downloads")
    view_count = models.PositiveIntegerField(default=0, help_text="Total views")

    # Status
    is_verified = models.BooleanField(
        default=False, help_text="Reviewed and approved for public listing"
    )
    is_active = models.BooleanField(default=True, help_text="Available for download")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        brand = self.brand.name if self.brand else "Unknown"
        model = self.model.name if self.model else "Unknown"
        return f"{brand}/{model} ({self.original_file_name})"

    @property
    def is_external(self) -> bool:
        return self.source_type == FirmwareSourceType.EXTERNAL

    def clean(self):
        super().clean()
        _validate_source_fields(self)

    @property
    def download_tokens(self):
        return DownloadToken.objects.for_firmware(self)


def _firmware_meta_constraints(prefix: str) -> list:
    return [
        source_constraint(f"{prefix}_exactly_one_source"),
        models.UniqueConstraint(
            fields=["variant", "original_file_name", "build_number"],
            condition=Q(variant__isnull=False),
            name=f"{prefix}_variant_file_build_unique",
        ),
    ]


class OfficialFirmware(BaseFirmware):
    class Meta:
        constraints = _firmware_meta_constraints("off")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["brand", "model", "variant"], name="off_hierarchy_idx"
            ),
            models.Index(fields=["chipset"], name="off_chipset_idx"),
            models.Index(fields=["android_version"], name="off_android_idx"),
            models.Index(fields=["is_verified", "is_active"], name="off_status_idx"),
            models.Index(fields=["-created_at"], name="off_created_idx"),
        ]


class EngineeringFirmware(BaseFirmware):
    subtype = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        constraints = _firmware_meta_constraints("eng")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["brand", "model", "variant"], name="eng_hierarchy_idx"
            ),
            models.Index(fields=["chipset"], name="eng_chipset_idx"),
            models.Index(fields=["android_version"], name="eng_android_idx"),
            models.Index(fields=["is_verified", "is_active"], name="eng_status_idx"),
            models.Index(fields=["-created_at"], name="eng_created_idx"),
        ]


class ReadbackFirmware(BaseFirmware):
    class Meta:
        constraints = _firmware_meta_constraints("rb")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["brand", "model", "variant"], name="rb_hierarchy_idx"),
            models.Index(fields=["chipset"], name="rb_chipset_idx"),
            models.Index(fields=["android_version"], name="rb_android_idx"),
            models.Index(fields=["is_verified", "is_active"], name="rb_status_idx"),
            models.Index(fields=["-created_at"], name="rb_created_idx"),
        ]


class ModifiedFirmware(BaseFirmware):
    subtype = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = _firmware_meta_constraints("mod")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["brand", "model", "variant"], name="mod_hierarchy_idx"
            ),
            models.Index(fields=["chipset"], name="mod_chipset_idx"),
            models.Index(fields=["android_version"], name="mod_android_idx"),
            models.Index(fields=["is_verified", "is_active"], name="mod_status_idx"),
            models.Index(fields=["-created_at"], name="mod_created_idx"),
        ]


class OtherFirmware(BaseFirmware):
    subtype = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        constraints = _firmware_meta_constraints("oth")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["brand", "model", "variant"], name="oth_hierarchy_idx"
            ),
            models.Index(fields=["chipset"], name="oth_chipset_idx"),
            models.Index(fields=["android_version"], name="oth_android_idx"),
            models.Index(fields=["is_verified", "is_active"], name="oth_status_idx"),
            models.Index(fields=["-created_at"], name="oth_created_idx"),
        ]


class UnclassifiedFirmware(BaseFirmware):
    reason = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        constraints = _firmware_meta_constraints("unc")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["brand", "model", "variant"], name="unc_hierarchy_idx"
            ),
            models.Index(fields=["chipset"], name="unc_chipset_idx"),
            models.Index(fields=["android_version"], name="unc_android_idx"),
            models.Index(fields=["is_verified", "is_active"], name="unc_status_idx"),
            models.Index(fields=["-created_at"], name="unc_created_idx"),
        ]


# Import tracking models so Django registers them in app registry
import_module(".tracking_models", __package__)

# =============================================================================
# GSMArena device synchronization
# =============================================================================


class GSMArenaDevice(models.Model):
    """Cached GSMArena device spec record — links specs to local firmware models."""

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    gsmarena_id = models.CharField(max_length=100, unique=True, db_index=True)
    brand = models.CharField(max_length=100, db_index=True)
    model_name = models.CharField(max_length=200)
    url = models.URLField(max_length=500, blank=True, default="")
    specs = models.JSONField(default=dict, blank=True)
    image_url = models.URLField(max_length=500, blank=True, default="")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    local_device = models.ForeignKey(
        "devices.Device",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gsmarena_records",
    )

    # Review workflow
    review_status = models.CharField(
        max_length=10,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_gsmarena_devices",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # Enriched spec fields — extracted from specs JSON for quick access
    marketed_as = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Marketed/alternate names",
    )
    model_codes = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Variant model codes (comma-separated)",
    )
    chipset = models.CharField(max_length=300, blank=True, default="")
    os_version = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Android version at launch",
    )
    os_upgradeable_to = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Latest upgradeable OS version",
    )

    class Meta:
        db_table = "gsmarena_sync_gsmarenadevice"
        verbose_name = "GSMArena Device"
        verbose_name_plural = "GSMArena Devices"
        ordering: ClassVar[list[str]] = ["brand", "model_name"]

    def __str__(self) -> str:
        return f"{self.brand} {self.model_name}"


# =============================================================================
# Firmware verification models
# =============================================================================


class TrustedTester(models.Model):
    """Community member authorized to submit firmware verification reports."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trusted_tester",
    )
    fw_count = models.PositiveIntegerField(default=0, help_text="Firmwares verified")
    avg_rating = models.FloatField(default=0.0)
    is_active = models.BooleanField(default=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "fw_verification_trustedtester"
        verbose_name = "Trusted Tester"
        verbose_name_plural = "Trusted Testers"

    def __str__(self) -> str:
        return f"TrustedTester({self.user})"


class VerificationReport(models.Model):
    """Full flash-test report for a firmware+device combination."""

    class Verdict(models.TextChoices):
        PASS = "pass", "Pass — Works"
        PARTIAL = "partial", "Partial — Issues Found"
        FAIL = "fail", "Fail — Bricked / Non-functional"
        SKIP = "skip", "Skip — Could Not Test"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        PUBLISHED = "published", "Published"
        REJECTED = "rejected", "Rejected"

    firmware = models.ForeignKey(
        OfficialFirmware,
        on_delete=models.CASCADE,
        related_name="verification_reports",
    )
    device = models.ForeignKey(
        "devices.Device",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verification_reports",
    )
    tester = models.ForeignKey(
        TrustedTester, on_delete=models.CASCADE, related_name="reports"
    )
    verdict = models.CharField(max_length=10, choices=Verdict.choices)
    test_details = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.SUBMITTED, db_index=True
    )
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fw_verification_verificationreport"
        verbose_name = "Verification Report"
        verbose_name_plural = "Verification Reports"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"Report#{self.pk} {self.verdict} for FW#{self.firmware_id}"


class TestResult(models.Model):
    """Individual sub-test result within a verification report."""

    class Result(models.TextChoices):
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"
        SKIP = "skip", "Skip"

    report = models.ForeignKey(
        VerificationReport, on_delete=models.CASCADE, related_name="test_results"
    )
    test_name = models.CharField(max_length=100)
    result = models.CharField(max_length=6, choices=Result.choices)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "fw_verification_testresult"
        verbose_name = "Test Result"
        verbose_name_plural = "Test Results"

    def __str__(self) -> str:
        return f"{self.test_name}: {self.result}"


class VerificationCredit(models.Model):
    """XP/credit awarded to a user for verification contribution."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="verification_credits",
    )
    firmware = models.ForeignKey(
        OfficialFirmware,
        on_delete=models.CASCADE,
        related_name="verification_credits",
    )
    report = models.ForeignKey(
        VerificationReport,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credits",
    )
    credits_earned = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fw_verification_verificationcredit"
        verbose_name = "Verification Credit"
        verbose_name_plural = "Verification Credits"

    def __str__(self) -> str:
        return f"{self.user} +{self.credits_earned} for FW#{self.firmware_id}"


# =============================================================================
# Download links
# =============================================================================


def _gen_token() -> str:
    import secrets

    return secrets.token_urlsafe(32)


class DownloadTokenQuerySet(models.QuerySet):
    def for_firmware(self, firmware):
        content_type = ContentType.objects.get_for_model(
            firmware, for_concrete_model=False
        )
        return self.filter(content_type=content_type, object_id=firmware.pk)


class DownloadToken(models.Model):
    """Single-use signed token that grants access to one firmware download."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        USED = "used", "Used"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="+"
    )
    object_id = models.UUIDField()
    firmware = GenericForeignKey("content_type", "object_id")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="download_tokens",
    )
    token = models.CharField(
        max_length=64, unique=True, default=_gen_token, db_index=True
    )
    ad_gate_required = models.BooleanField(default=True)
    ad_gate_completed = models.BooleanField(default=False)
    ip = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    objects = DownloadTokenQuerySet.as_manager()

    class Meta:
        db_table = "download_links_downloadtoken"
        verbose_name = "Download Token"
        verbose_name_plural = "Download Tokens"
        ordering: ClassVar[list[str]] = ["-created_at"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["token", "status"], name="dl_token_status_idx"),
            models.Index(
                fields=["content_type", "object_id", "status"],
                name="dl_token_firmware_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Token {self.token[:10]}… [{self.status}]"


class DownloadSession(models.Model):
    """Tracks the full lifecycle of a download session."""

    class Status(models.TextChoices):
        STARTED = "started", "Started"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        ABORTED = "aborted", "Aborted"

    token = models.ForeignKey(
        DownloadToken, on_delete=models.CASCADE, related_name="sessions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="download_sessions",
    )
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    bytes_delivered = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.STARTED
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "download_links_downloadsession"
        verbose_name = "Download Session"
        verbose_name_plural = "Download Sessions"
        ordering: ClassVar[list[str]] = ["-started_at"]

    def __str__(self) -> str:
        return f"Session #{self.pk} [{self.status}]"


class AdGateLog(models.Model):
    """Records ad-gate completion for a download token."""

    token = models.ForeignKey(
        DownloadToken, on_delete=models.CASCADE, related_name="ad_logs"
    )
    session = models.ForeignKey(
        DownloadSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ad_logs",
    )
    ad_type = models.CharField(
        max_length=20,
        choices=[
            ("video", "Video"),
            ("banner", "Banner"),
            ("interstitial", "Interstitial"),
        ],
        default="video",
    )
    ad_network = models.CharField(max_length=50, blank=True, default="")
    watched_seconds = models.PositiveSmallIntegerField(default=0)
    required_seconds = models.PositiveSmallIntegerField(default=30)
    credits_earned = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "download_links_adgatelog"
        verbose_name = "Ad Gate Log"
        verbose_name_plural = "Ad Gate Logs"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"AdGate {self.ad_type} token #{self.token_id}"


class HotlinkBlock(models.Model):
    """Domain-level hotlinking protection entries."""

    domain = models.CharField(max_length=253, unique=True, db_index=True)
    reason = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    blocked_count = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "download_links_hotlinkblock"
        verbose_name = "Hotlink Block"
        verbose_name_plural = "Hotlink Blocks"
        ordering: ClassVar[list[str]] = ["-blocked_count"]

    def __str__(self) -> str:
        return f"{self.domain} [{'active' if self.is_active else 'inactive'}]"


# ---------------------------------------------------------------------------
# Changelog models
# ---------------------------------------------------------------------------


class ChangelogEntry(models.Model):
    """Platform-level changelog entry (public releases feed)."""

    version = models.CharField(max_length=30, unique=True)
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True, default="")
    changes = models.JSONField(
        default=list,
        blank=True,
        help_text="Structured list of change objects {type, description}",
    )
    release_date = models.DateField(db_index=True)
    is_published = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "changelog_changelogentry"
        verbose_name = "Changelog Entry"
        verbose_name_plural = "Changelog Entries"
        ordering: ClassVar[list[str]] = ["-release_date"]

    def __str__(self) -> str:
        return f"v{self.version}: {self.title}"


class ReleaseNote(models.Model):
    """Individual release note line item within a ChangelogEntry."""

    class Category(models.TextChoices):
        FEATURE = "feature", "New Feature"
        IMPROVEMENT = "improvement", "Improvement"
        BUG_FIX = "bug_fix", "Bug Fix"
        SECURITY = "security", "Security"
        BREAKING = "breaking", "Breaking Change"
        DEPRECATED = "deprecated", "Deprecation"

    entry = models.ForeignKey(
        ChangelogEntry, on_delete=models.CASCADE, related_name="release_notes"
    )
    category = models.CharField(max_length=15, choices=Category.choices)
    description = models.TextField()
    is_breaking = models.BooleanField(default=False)

    class Meta:
        db_table = "changelog_releasenote"
        verbose_name = "Release Note"
        verbose_name_plural = "Release Notes"
        ordering: ClassVar[list[str]] = ["-id"]

    def __str__(self) -> str:
        return f"[{self.category}] {self.description[:60]}"


class FirmwareDiff(models.Model):
    """Computed diff between two firmware versions."""

    old_firmware = models.ForeignKey(
        OfficialFirmware,
        on_delete=models.CASCADE,
        related_name="diffs_as_old",
    )
    new_firmware = models.ForeignKey(
        OfficialFirmware,
        on_delete=models.CASCADE,
        related_name="diffs_as_new",
    )
    diff_html = models.TextField(blank=True, default="")
    diff_text = models.TextField(blank=True, default="")
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "changelog_firmwarediff"
        verbose_name = "Firmware Diff"
        verbose_name_plural = "Firmware Diffs"
        unique_together: ClassVar[list[tuple[str, str]]] = [
            ("old_firmware", "new_firmware")
        ]

    def __str__(self) -> str:
        return f"Diff FW#{self.old_firmware_id} → FW#{self.new_firmware_id}"


# ══════════════════════════════════════════════════════════════════════════════
# Flashing Tools & Guides
# ══════════════════════════════════════════════════════════════════════════════


class FlashingToolCategory(Timestamped):
    """Categorize flashing tools (OEM, Crack/Patched, Local Market, Open Source)."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=50, blank=True, default="wrench")
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "firmwares_flashingtoolcategory"
        verbose_name = "Flashing Tool Category"
        verbose_name_plural = "Flashing Tool Categories"
        ordering: ClassVar[list[str]] = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class FlashingTool(Timestamped):
    """Catalog of flashing/unlocking/rooting tools for various devices."""

    class ToolType(models.TextChoices):
        OEM = "oem", "OEM Official"
        CRACK = "crack", "Crack / Patched"
        LOCAL_MARKET = "local_market", "Local Market"
        FREE = "free", "Free / Freeware"
        OPEN_SOURCE = "open_source", "Open Source"

    class Platform(models.TextChoices):
        WINDOWS = "windows", "Windows"
        MAC = "macos", "macOS"
        LINUX = "linux", "Linux"
        ANDROID = "android", "Android"
        MULTI = "multi", "Multi-Platform"

    class RiskLevel(models.TextChoices):
        SAFE = "safe", "Safe"
        MODERATE = "moderate", "Moderate"
        ADVANCED = "advanced", "Advanced"
        RISKY = "risky", "Risky"

    category = models.ForeignKey(
        FlashingToolCategory,
        on_delete=models.CASCADE,
        related_name="tools",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    description = models.TextField(blank=True, default="")
    tool_type = models.CharField(
        max_length=20, choices=ToolType.choices, default=ToolType.FREE
    )
    platform = models.CharField(
        max_length=20, choices=Platform.choices, default=Platform.WINDOWS
    )
    risk_level = models.CharField(
        max_length=20, choices=RiskLevel.choices, default=RiskLevel.SAFE
    )
    supported_brands = models.ManyToManyField(
        Brand, blank=True, related_name="flashing_tools"
    )
    chipsets = models.ManyToManyField(
        Chipset,
        blank=True,
        related_name="flashing_tools",
        help_text="Chipsets this tool can flash",
    )
    download_url = models.URLField(max_length=500, blank=True, default="")
    official_url = models.URLField(max_length=500, blank=True, default="")
    version = models.CharField(max_length=50, blank=True, default="")
    last_version_date = models.DateField(null=True, blank=True)
    is_free = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    hero_image = models.ImageField(upload_to="flashing_tools/", blank=True, null=True)
    instructions = models.TextField(
        blank=True, default="", help_text="Usage instructions (Markdown supported)"
    )
    features = models.JSONField(
        default=list, blank=True, help_text="Feature bullet points"
    )
    requirements = models.JSONField(
        default=list, blank=True, help_text="System requirements"
    )
    is_active = models.BooleanField(default=True)
    views_count = models.PositiveIntegerField(default=0)
    downloads_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "firmwares_flashingtool"
        verbose_name = "Flashing Tool"
        verbose_name_plural = "Flashing Tools"
        ordering: ClassVar[list[str]] = ["-is_featured", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_tool_type_display()})"


class FlashingGuideTemplate(Timestamped):
    """
    Templates for auto-generating blog posts about flashing guides.
    Can be per-brand, per-chipset, or per-category (e.g. 'How to flash Samsung').
    """

    class GuideType(models.TextChoices):
        STOCK_FLASH = "stock_flash", "Stock Firmware Flash"
        CUSTOM_ROM = "custom_rom", "Custom ROM Installation"
        UNLOCK_BOOTLOADER = "unlock_bootloader", "Bootloader Unlock"
        ROOT = "root", "Root Access"
        RECOVERY = "recovery", "Recovery Installation"
        FRP_BYPASS = "frp_bypass", "FRP Bypass"
        DOWNGRADE = "downgrade", "Firmware Downgrade"
        REPAIR = "repair", "Brick Repair"
        GENERAL = "general", "General Guide"

    title_template = models.CharField(
        max_length=300,
        help_text="Use {brand}, {model}, {chipset}, {guide_type} placeholders",
    )
    body_template = models.TextField(
        help_text="Markdown. Use {brand}, {model}, {chipset}, {tools}, {steps} placeholders"
    )
    summary_template = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="SEO summary template with same placeholders",
    )
    guide_type = models.CharField(
        max_length=30, choices=GuideType.choices, default=GuideType.STOCK_FLASH
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="guide_templates",
        help_text="Generate guides for this brand. Leave empty for generic.",
    )
    chipset_filter = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Only generate for models with this chipset (partial match)",
    )
    recommended_tools = models.ManyToManyField(
        FlashingTool,
        blank=True,
        related_name="guide_templates",
    )
    auto_generate = models.BooleanField(
        default=False,
        help_text="Automatically generate blog posts for matching brand/models",
    )
    submit_for_review = models.BooleanField(
        default=False,
        help_text="Automatically submit generated guides for editorial review; never publish directly.",
    )
    is_active = models.BooleanField(default=True)
    generated_count = models.PositiveIntegerField(
        default=0, help_text="Number of posts generated from this template"
    )

    class Meta:
        db_table = "firmwares_flashingguidetemplate"
        verbose_name = "Flashing Guide Template"
        verbose_name_plural = "Flashing Guide Templates"
        ordering: ClassVar[list[str]] = ["guide_type", "brand__name"]

    def __str__(self) -> str:
        brand_str = self.brand.name if self.brand else "Generic"
        return f"{self.get_guide_type_display()} — {brand_str}"


class GeneratedFlashingGuide(Timestamped):
    """Track which blog posts were auto-generated from guide templates."""

    template = models.ForeignKey(
        FlashingGuideTemplate,
        on_delete=models.CASCADE,
        related_name="generated_guides",
    )
    post = models.OneToOneField(
        "blog.Post",
        on_delete=models.CASCADE,
        related_name="flashing_guide_source",
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        related_name="generated_guides",
    )
    model = models.ForeignKey(
        "firmwares.Model",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="generated_guides",
    )
    chipset = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        db_table = "firmwares_generatedflashingguide"
        verbose_name = "Generated Flashing Guide"
        verbose_name_plural = "Generated Flashing Guides"
        ordering: ClassVar[list[str]] = ["-created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["template", "brand", "model"],
                condition=Q(model__isnull=False),
                name="generated_guide_unique_model",
            ),
            models.UniqueConstraint(
                fields=["template", "brand"],
                condition=Q(model__isnull=True),
                name="generated_guide_unique_brand",
            ),
        ]

    def __str__(self) -> str:
        model_str = self.model.name if self.model else "All"
        return f"Guide: {self.brand.name} {model_str}"


class AutomatedBlogLink(Timestamped):
    """Maps a brand, model or chipset to its single automated blog post."""

    class Kind(models.TextChoices):
        BRAND = "brand", "Brand"
        MODEL = "model", "Model"
        CHIPSET = "chipset", "Chipset"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    brand = models.ForeignKey(
        Brand,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="blog_links",
    )
    model = models.ForeignKey(
        Model,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="blog_links",
    )
    chipset = models.ForeignKey(
        Chipset,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="blog_links",
    )
    post = models.OneToOneField(
        "blog.Post",
        on_delete=models.CASCADE,
        related_name="firmware_automation_link",
    )
    content_hash = models.CharField(max_length=64, blank=True, default="")
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Automated Blog Link"
        verbose_name_plural = "Automated Blog Links"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["brand"], condition=Q(kind="brand"), name="auto_blog_one_brand"
            ),
            models.UniqueConstraint(
                fields=["model"], condition=Q(kind="model"), name="auto_blog_one_model"
            ),
            models.UniqueConstraint(
                fields=["chipset"],
                condition=Q(kind="chipset"),
                name="auto_blog_one_chipset",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        kind="brand",
                        brand__isnull=False,
                        model__isnull=True,
                        chipset__isnull=True,
                    )
                    | Q(
                        kind="model",
                        model__isnull=False,
                        brand__isnull=True,
                        chipset__isnull=True,
                    )
                    | Q(
                        kind="chipset",
                        chipset__isnull=False,
                        brand__isnull=True,
                        model__isnull=True,
                    )
                ),
                name="auto_blog_target_matches_kind",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} blog link → post #{self.post_id}"

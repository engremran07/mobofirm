"""Admin-editable rich-text editor profiles and the images uploaded through them."""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.editor.features import normalize_features
from apps.core.editor.spec import (
    CONTEXT_LABELS,
    MAX_HEIGHT,
    MAX_UPLOAD_KB,
    MIN_HEIGHT,
    Layout,
    ProfileSpec,
    UploadRules,
    default_profile,
)
from apps.core.editor.video import normalize_providers

UPLOAD_PREFIX = "editor"


def editor_image_path(instance: models.Model, filename: str) -> str:
    """Random, extension-preserving name under the dedicated ``editor/`` prefix."""
    del instance
    suffix = PurePosixPath(filename).suffix.lower()[:6]
    now = timezone.now()
    return f"{UPLOAD_PREFIX}/{now:%Y/%m}/{uuid.uuid4().hex}{suffix}"


class EditorProfile(models.Model):
    """The rich-text editor configuration of one content context."""

    context = models.CharField(
        max_length=40,
        unique=True,
        choices=tuple(CONTEXT_LABELS.items()),
        help_text="Where this editor is used.",
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Off uses a plain textarea; content stays valid.",
    )
    features = models.JSONField(default=list, blank=True)
    video_providers = models.JSONField(default=list, blank=True)
    upload_enabled = models.BooleanField(
        default=False,
        help_text="Let editors upload images (needs Images).",
    )
    upload_max_kb = models.PositiveIntegerField(
        default=2048,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_UPLOAD_KB)],
        help_text="Largest accepted image, in KiB.",
    )
    upload_daily_limit = models.PositiveIntegerField(
        default=20,
        help_text="Images per non-staff user per day (0 = none).",
    )
    max_length = models.PositiveIntegerField(
        default=0, help_text="Maximum visible characters; 0 means unlimited."
    )
    spellcheck = models.BooleanField(default=True)
    placeholder = models.CharField(max_length=200, blank=True, default="")
    height = models.PositiveSmallIntegerField(
        default=280,
        validators=[MinValueValidator(MIN_HEIGHT), MaxValueValidator(MAX_HEIGHT)],
        help_text="Editing area height in pixels.",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ("context",)
        verbose_name = "editor profile"
        verbose_name_plural = "editor profiles"

    def __str__(self) -> str:
        return CONTEXT_LABELS.get(self.context, self.context)

    def clean(self) -> None:
        """Uploads and embeds are meaningless without their parent feature."""
        features = normalize_features(self.features)
        errors: dict[str, str] = {}
        if self.upload_enabled and "images" not in features:
            errors["upload_enabled"] = "Image uploads need the Images feature."
        if "video" in features and not normalize_providers(self.video_providers):
            errors["video_providers"] = "Choose at least one provider for video."
        if errors:
            raise ValidationError(errors)

    def to_spec(self) -> ProfileSpec:
        """The immutable configuration consumed by widgets and the sanitiser."""
        return ProfileSpec(
            key=self.context,
            enabled=self.enabled,
            features=normalize_features(self.features),
            video_providers=normalize_providers(self.video_providers),
            upload=UploadRules(
                self.upload_enabled, self.upload_max_kb, self.upload_daily_limit
            ),
            layout=Layout(
                self.max_length, self.spellcheck, self.placeholder, self.height
            ),
        )

    def apply_spec(self, spec: ProfileSpec) -> None:
        """Copy every setting from ``spec`` onto this row (not saved)."""
        for name, value in spec.field_values().items():
            setattr(self, name, value)

    def reset_to_defaults(self) -> None:
        """Restore the code-owned baseline for this context (not saved)."""
        self.apply_spec(default_profile(self.context))

    @classmethod
    def seed(cls, *, reset: bool = False) -> dict[str, str]:
        """Create the missing rows (and reset existing ones when ``reset``).

        Idempotent: a second run without ``reset`` changes nothing.
        """
        outcome: dict[str, str] = {}
        for context in CONTEXT_LABELS:
            row, created = cls.objects.get_or_create(
                context=context, defaults=default_profile(context).field_values()
            )
            if created:
                outcome[context] = "created"
            elif reset:
                row.reset_to_defaults()
                row.save()
                outcome[context] = "reset"
            else:
                outcome[context] = "kept"
        return outcome


class EditorImage(models.Model):
    """An image uploaded through an editor; embedded in content, never a library."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="editor_images",
    )
    context = models.CharField(max_length=40, db_index=True)
    file = models.ImageField(upload_to=editor_image_path, max_length=200)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "editor image"
        verbose_name_plural = "editor images"

    def __str__(self) -> str:
        return str(self.file.name)

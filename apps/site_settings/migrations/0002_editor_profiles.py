"""Editor profiles and editor images, with one seeded profile per context."""

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.site_settings.editor_models
from apps.core.editor.spec import CONTEXT_LABELS, default_profile


def seed_profiles(app_registry, _schema_editor):
    """Create one row per context from the code defaults (idempotent)."""
    profile_model = app_registry.get_model("site_settings", "EditorProfile")
    for context in CONTEXT_LABELS:
        profile_model.objects.get_or_create(
            context=context, defaults=default_profile(context).field_values()
        )


class Migration(migrations.Migration):
    """Create the editor models and seed the default profiles."""

    dependencies = (
        ("site_settings", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    )

    operations = (
        migrations.CreateModel(
            name="EditorImage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("context", models.CharField(db_index=True, max_length=40)),
                (
                    "file",
                    models.ImageField(
                        max_length=200,
                        upload_to=apps.site_settings.editor_models.editor_image_path,
                    ),
                ),
                ("width", models.PositiveIntegerField()),
                ("height", models.PositiveIntegerField()),
                ("size_bytes", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="editor_images",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "editor image",
                "verbose_name_plural": "editor images",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="EditorProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "context",
                    models.CharField(
                        choices=[
                            ("blog_post", "Blog posts"),
                            ("guide_request", "Guide requests and solutions"),
                            ("comment", "Comments"),
                            ("page", "CMS pages"),
                            ("listing", "Marketplace listings"),
                            ("firmware_notes", "Firmware release notes"),
                            ("announcement", "Announcements and promotions"),
                            ("request", "Catalogue and firmware requests"),
                            ("admin_default", "Other admin text fields"),
                        ],
                        help_text="Where this editor is used.",
                        max_length=40,
                        unique=True,
                    ),
                ),
                (
                    "enabled",
                    models.BooleanField(
                        default=True,
                        help_text="Off uses a plain textarea; content stays valid.",
                    ),
                ),
                ("features", models.JSONField(blank=True, default=list)),
                ("video_providers", models.JSONField(blank=True, default=list)),
                (
                    "upload_enabled",
                    models.BooleanField(
                        default=False,
                        help_text="Let editors upload images (needs Images).",
                    ),
                ),
                (
                    "upload_max_kb",
                    models.PositiveIntegerField(
                        default=2048,
                        help_text="Largest accepted image, in KiB.",
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(10240),
                        ],
                    ),
                ),
                (
                    "upload_daily_limit",
                    models.PositiveIntegerField(
                        default=20,
                        help_text="Images per non-staff user per day (0 = none).",
                    ),
                ),
                (
                    "max_length",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Maximum visible characters; 0 means unlimited.",
                    ),
                ),
                ("spellcheck", models.BooleanField(default=True)),
                (
                    "placeholder",
                    models.CharField(blank=True, default="", max_length=200),
                ),
                (
                    "height",
                    models.PositiveSmallIntegerField(
                        default=280,
                        help_text="Editing area height in pixels.",
                        validators=[
                            django.core.validators.MinValueValidator(120),
                            django.core.validators.MaxValueValidator(900),
                        ],
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "editor profile",
                "verbose_name_plural": "editor profiles",
                "ordering": ("context",),
            },
        ),
        migrations.RunPython(seed_profiles, migrations.RunPython.noop),
    )

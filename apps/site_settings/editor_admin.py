"""Admin for editor profiles and the images uploaded through the editors."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from unfold.widgets import UnfoldAdminCheckboxSelectMultipleWidget

from apps.core.admin_base import ModelAdmin, action, display
from apps.core.editor.features import feature_choices, normalize_features
from apps.core.editor.spec import CONTEXT_LABELS
from apps.core.editor.video import normalize_providers, provider_choices
from apps.core.editor.widgets import RichTextWidget

from .editor_models import EditorImage, EditorProfile
from .editor_store import clear_profile_cache

audit_logger = logging.getLogger("apps.site_settings.audit")

PREVIEW_TEXT = (
    "<p>Try the <strong>toolbar</strong> of this profile. Features switched off "
    "in the form above are missing here after you save.</p>"
)


class EditorProfileAdminForm(forms.ModelForm):
    """Feature checkboxes grouped like the toolbar, plus provider choices."""

    features = forms.MultipleChoiceField(
        choices=feature_choices(),
        required=False,
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        help_text="Each checked feature appears in the toolbar and is allowed by "
        "the server-side sanitiser; unchecked features are removed from saved "
        "content.",
    )
    video_providers = forms.MultipleChoiceField(
        choices=provider_choices(),
        required=False,
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        help_text="Only audited providers can be embedded, always in their "
        "privacy-friendly player.",
    )

    class Meta:
        model = EditorProfile
        fields = "__all__"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        instance = self.instance
        if instance.pk:
            self.initial["features"] = sorted(normalize_features(instance.features))
            self.initial["video_providers"] = list(
                normalize_providers(instance.video_providers)
            )
        elif isinstance(self.fields.get("context"), forms.ChoiceField):
            used = set(EditorProfile.objects.values_list("context", flat=True))
            field = self.fields["context"]
            if isinstance(field, forms.ChoiceField):
                field.choices = [
                    (key, label)
                    for key, label in CONTEXT_LABELS.items()
                    if key not in used
                ]

    def clean_features(self) -> list[str]:
        """Store the checked features as a sorted list of known keys."""
        return sorted(normalize_features(self.cleaned_data.get("features")))

    def clean_video_providers(self) -> list[str]:
        """Store the checked providers in catalogue order."""
        return list(normalize_providers(self.cleaned_data.get("video_providers")))


@admin.register(EditorProfile)
class EditorProfileAdmin(ModelAdmin):
    """Per-context editor profiles: features, uploads, limits and the master switch."""

    form = EditorProfileAdminForm
    list_display = (
        "context_label",
        "enabled_badge",
        "feature_count",
        "uploads",
        "max_length",
        "updated_at",
    )
    list_display_links = ("context_label",)
    ordering = ("context",)
    actions = ("reset_to_defaults", "enable_editors", "disable_editors")

    def get_fieldsets(
        self, request: HttpRequest, obj: EditorProfile | None = None
    ) -> list[tuple[str | None, dict[str, Any]]]:
        """Show the context selector when adding and the live preview afterwards."""
        head = ("context_label" if obj else "context", "enabled")
        sets: list[tuple[str | None, dict[str, Any]]] = [
            (None, {"fields": head}),
            (
                "Toolbar and formatting features",
                {"fields": ("features", "video_providers")},
            ),
            (
                "Image uploads",
                {"fields": ("upload_enabled", "upload_max_kb", "upload_daily_limit")},
            ),
            (
                "Behaviour",
                {"fields": ("max_length", "spellcheck", "placeholder", "height")},
            ),
        ]
        if obj:
            sets.append(("Preview (saved settings)", {"fields": ("preview",)}))
        return sets

    def get_readonly_fields(
        self, request: HttpRequest, obj: EditorProfile | None = None
    ) -> Sequence[str]:
        """The context cannot change after creation."""
        return ("context_label", "preview") if obj else ()

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Only contexts without a profile can still be added."""
        return super().has_add_permission(request) and (
            EditorProfile.objects.count() < len(CONTEXT_LABELS)
        )

    def has_delete_permission(
        self, request: HttpRequest, obj: EditorProfile | None = None
    ) -> bool:
        """Profiles are reset, never deleted, so every context stays configurable."""
        return False

    @display(description=_("Context"), ordering="context")
    def context_label(self, obj: EditorProfile) -> str:
        """Readable context name."""
        return str(obj)

    @display(
        description=_("Editor"),
        ordering="enabled",
        label={True: "success", False: "danger"},
    )
    def enabled_badge(self, obj: EditorProfile) -> tuple[bool, str]:
        """On/off badge for the master switch."""
        return obj.enabled, "Rich editor" if obj.enabled else "Plain textarea"

    @display(description=_("Features"))
    def feature_count(self, obj: EditorProfile) -> str:
        """How many features are switched on."""
        return str(len(normalize_features(obj.features)))

    @display(description=_("Uploads"), boolean=True)
    def uploads(self, obj: EditorProfile) -> bool:
        """Whether image uploads are effective."""
        return obj.to_spec().upload_available

    @display(description=_("Preview"))
    def preview(self, obj: EditorProfile) -> SafeString:
        """A live editor built from the saved profile."""
        widget = RichTextWidget(obj.context, attrs={"form": "mf-editor-preview-none"})
        markup = widget.render(f"preview-{obj.context}", PREVIEW_TEXT)
        return format_html('<div class="mf-editor-preview">{}</div>', markup)

    def save_model(
        self,
        request: HttpRequest,
        obj: EditorProfile,
        form: forms.ModelForm,
        change: bool,
    ) -> None:
        """Record who changed the profile and audit-log it."""
        user = request.user
        if isinstance(user, get_user_model()):
            obj.updated_by = user
        super().save_model(request, obj, form, change)
        audit_logger.info(
            "editor_profile_saved",
            extra={"context": obj.context, "user_id": request.user.pk},
        )

    @action(
        description=_("Reset selected to built-in defaults"), permissions=["change"]
    )
    def reset_to_defaults(
        self, request: HttpRequest, queryset: QuerySet[EditorProfile]
    ) -> None:
        """Restore the code-owned baseline for each selected context."""
        count = 0
        for profile in queryset:
            profile.reset_to_defaults()
            profile.save()
            audit_logger.info(
                "editor_profile_reset",
                extra={"context": profile.context, "user_id": request.user.pk},
            )
            count += 1
        self.message_user(
            request, _("Reset %(count)d editor profile(s).") % {"count": count}
        )

    @action(description=_("Enable the rich editor"), permissions=["change"])
    def enable_editors(
        self, request: HttpRequest, queryset: QuerySet[EditorProfile]
    ) -> None:
        """Switch the master toggle on for the selection."""
        count = queryset.update(enabled=True)
        clear_profile_cache()
        self.message_user(request, _("Enabled %(count)d editor(s).") % {"count": count})

    @action(description=_("Fall back to a plain textarea"), permissions=["change"])
    def disable_editors(
        self, request: HttpRequest, queryset: QuerySet[EditorProfile]
    ) -> None:
        """Switch the master toggle off; content stays valid and sanitised."""
        count = queryset.update(enabled=False)
        clear_profile_cache()
        self.message_user(
            request,
            _("Disabled %(count)d editor(s).") % {"count": count},
            level=messages.WARNING,
        )


@admin.register(EditorImage)
class EditorImageAdmin(ModelAdmin):
    """Images embedded through the editors (not a personal file library)."""

    list_display = ("thumbnail", "owner", "context", "size_kb", "created_at")
    list_filter = ("context",)
    search_fields = ("owner__username", "owner__email", "file")
    readonly_fields = (
        "owner",
        "context",
        "file",
        "width",
        "height",
        "size_bytes",
        "created_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Images only arrive through the editors' upload button."""
        return False

    @display(description=_("Image"))
    def thumbnail(self, obj: EditorImage) -> SafeString | str:
        """Small preview of the stored image."""
        if not obj.file:
            return "-"
        return format_html(
            '<img src="{}" alt="" width="48" height="48" loading="lazy">',
            obj.file.url,
        )

    @display(description=_("Size (KiB)"), ordering="size_bytes")
    def size_kb(self, obj: EditorImage) -> str:
        """Stored size in KiB."""
        return f"{obj.size_bytes / 1024:.0f}"

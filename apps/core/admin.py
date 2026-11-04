"""
admin.py
--------
Enterprise-ready admin bootstrap for the app.

Features:
- Admin site branding (centralized)
- Safe JSON/CSV export admin action helper
- Automatic registration of unregistered models with a default ModelAdmin
- Clear defaults for list_display/search_fields to avoid heavy table scans
- No deprecated APIs (Django 5.2+ compatible)
"""

from __future__ import annotations

import json
import logging

from django.contrib import admin
from solo.admin import SingletonModelAdmin

from apps.core.models import AppRegistry


# App registry admin
@admin.register(AppRegistry)
class AppRegistryAdmin(SingletonModelAdmin):
    list_display = (
        "ads_enabled",
        "blog_enabled",
        "comments_enabled",
        "devices_enabled",
        "device_identity_enabled",
        "firmwares_enabled",
        "governance_enabled",
        "tags_enabled",
        "users_enabled",
    )
    fieldsets = (
        (
            "Applications",
            {
                "fields": (
                    "ads_enabled",
                    "blog_enabled",
                    "comments_enabled",
                    "devices_enabled",
                    "firmwares_enabled",
                    "governance_enabled",
                    "tags_enabled",
                    "users_enabled",
                )
            },
        ),
        (
            "Device identity",
            {"fields": ("device_identity_enabled",)},
        ),
    )

    def has_add_permission(self, request):
        return False


from django.core.exceptions import ImproperlyConfigured  # noqa: E402
from django.db import models  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.utils.encoding import smart_str  # noqa: E402
from django.utils.text import capfirst  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------
# Admin site branding (change as needed)
# ---------------------------
admin.site.site_header = "Platform Administration"
admin.site.site_title = "GSM Admin"
admin.site.index_title = "System Administration"


# ---------------------------
# Utility admin actions
# ---------------------------
def export_as_json_action(description: str = "Export selected objects as JSON"):
    """
    Returns an admin action that exports selected queryset to JSON.
    Usage:
        actions = [export_as_json_action("Export selected users as JSON")]
    """

    def action(modeladmin, request, queryset):
        model = modeladmin.model
        # Use values() to avoid serializing complex relations; admins can override for custom output
        try:
            fields = [f.name for f in model._meta.concrete_fields]
        except Exception:
            fields = None

        data = list(queryset.values(*fields)) if fields else list(queryset.values())
        resp = HttpResponse(
            json.dumps(data, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
        )
        resp["Content-Disposition"] = (
            f"attachment; filename={model._meta.model_name}_export.json"
        )
        return resp

    action.short_description = description
    return action


def export_as_csv_action(description: str = "Export selected objects as CSV"):
    """
    Returns an admin action that exports selected queryset to CSV.
    Basic, safe implementation — override in specific ModelAdmin for customized exports.
    """

    def action(modeladmin, request, queryset):
        import csv

        model = modeladmin.model
        try:
            fields = [f.name for f in model._meta.concrete_fields]
        except Exception:
            fields = None

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f"attachment; filename={model._meta.model_name}_export.csv"
        )
        response.write("\ufeff")  # UTF-8 BOM for Excel

        writer = csv.writer(response)
        if fields:
            writer.writerow([smart_str(capfirst(f)) for f in fields])
            for obj in queryset.values_list(*fields):
                writer.writerow([smart_str(v) for v in obj])
        else:
            # fallback to keys of values()
            first = queryset.values().first()
            if not first:
                return response
            keys = list(first.keys())
            writer.writerow([smart_str(capfirst(k)) for k in keys])
            for obj in queryset.values(*keys):
                writer.writerow([smart_str(obj.get(k, "")) for k in keys])

        return response

    action.short_description = description
    return action


# ---------------------------
# Default ModelAdmin used for auto-registration
# ---------------------------
class DefaultModelAdmin(admin.ModelAdmin):
    """
    Sensible defaults for auto-registered models:
    - show first 6 concrete fields in list_display
    - search on TextField/CharField if present (first 3)
    - readonly auto timestamp fields if present
    - includes JSON/CSV export actions
    """

    actions = [
        export_as_json_action(),
        export_as_csv_action(),
    ]

    def __init__(self, model, admin_site):
        # attempt to build light-weight list_display and search_fields to avoid heavy DB scans
        try:
            concrete_fields = [
                f
                for f in model._meta.concrete_fields
                if not isinstance(f, models.ManyToManyField)
            ]
            # prefer id/email/username/date fields if present
            preferred = []
            for name in (
                "email",
                "username",
                "name",
                "title",
                "id",
                "created_at",
                "created",
                "date_joined",
            ):
                if any(f.name == name for f in concrete_fields):
                    preferred.append(name)
            # fallback to first N concrete fields
            fallback = [f.name for f in concrete_fields][:6]
            list_display = preferred + [f for f in fallback if f not in preferred]
            self.list_display = tuple(list_display) if list_display else ("__str__",)
            # search on first few textual fields
            text_fields = [
                f.name
                for f in concrete_fields
                if isinstance(f, (models.CharField, models.TextField))
            ][:3]
            self.search_fields = tuple(text_fields) if text_fields else ()  # type: ignore[misc]
            # readonly timestamp-like fields
            ro = [
                f.name
                for f in concrete_fields
                if f.name in ("created_at", "updated_at", "date_joined", "created")
            ]
            self.readonly_fields = tuple(ro)  # type: ignore[misc]
        except Exception as exc:
            logger.debug("DefaultModelAdmin init fallback: %s", exc)
        super().__init__(model, admin_site)


# ---------------------------
# Auto-register any models from this app that are not already registered
# ---------------------------
def auto_register_models(app_label: str):
    """
    Automatically register models belonging to `app_label` that are not yet registered.
    Use with caution — it's convenient during development and safe in production because
    ModelAdmin defaults are conservative.
    """
    from django.apps import apps as django_apps

    try:
        app_config = django_apps.get_app_config(app_label)
    except LookupError as exc:
        raise ImproperlyConfigured(
            f"Cannot auto-register models: unknown app_label '{app_label}'"
        ) from exc

    for model in app_config.get_models():
        model_name = model._meta.model_name
        if model in admin.site._registry:
            continue
        try:
            admin.site.register(model, DefaultModelAdmin)
            logger.info("Auto-registered model %s.%s in admin", app_label, model_name)
        except admin.sites.AlreadyRegistered:  # type: ignore[attr-defined]
            continue
        except Exception as exc:
            logger.exception(
                "Failed to auto-register %s.%s : %s", app_label, model_name, exc
            )


# ---------------------------
# If you want automatic registration for the current app, un-comment and set the app label:
# e.g. for apps.core use app_label = "apps.core"
# ---------------------------
# auto_register_models("apps.core")

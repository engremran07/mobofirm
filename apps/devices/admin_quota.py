from django.contrib import admin

from apps.devices.models_quota import UserDeviceQuota


@admin.register(UserDeviceQuota)
class UserDeviceQuotaAdmin(admin.ModelAdmin):
    list_display = ("user", "max_devices", "window", "last_reset_at")
    search_fields = ("user__email", "user__username")
    list_filter = ("window", "last_reset_at")

    def has_add_permission(self, request):
        # Only superusers can add quotas
        return getattr(request.user, "is_superuser", False)

    def has_change_permission(self, request, obj=None):
        # Only superusers can change quotas
        return getattr(request.user, "is_superuser", False)

    def has_delete_permission(self, request, obj=None):
        # Only superusers can delete quotas
        return getattr(request.user, "is_superuser", False)

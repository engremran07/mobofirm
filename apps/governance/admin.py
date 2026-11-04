from django.contrib import admin

from .models import ReviewCase, ReviewEvent


@admin.register(ReviewCase)
class ReviewCaseAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "content_type",
        "object_id",
        "status",
        "revision",
        "requested_by",
        "reviewer",
        "publisher",
        "created_at",
    )
    list_filter = ("status", "content_type", "created_at")
    search_fields = ("object_id", "requested_by__username", "requested_by__email")
    readonly_fields = (
        "content_type",
        "object_id",
        "requested_by",
        "reviewer",
        "publisher",
        "result_content_type",
        "result_object_id",
        "status",
        "revision",
        "decision_data",
        "is_open",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReviewEvent)
class ReviewEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "case",
        "action",
        "actor",
        "from_status",
        "to_status",
        "occurred_at",
    )
    list_filter = ("action", "occurred_at")
    readonly_fields = (
        "case",
        "actor",
        "action",
        "from_status",
        "to_status",
        "rationale",
        "metadata",
        "idempotency_key",
        "occurred_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from django.contrib import admin
from solo.admin import SingletonModelAdmin

from .models import Comment, CommentSettings


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("post", "user", "status", "toxicity_score", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("body", "user__email", "post__title")
    raw_id_fields = ("post", "user")
    readonly_fields = ("status", "is_approved")
    actions = ("soft_delete",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(is_deleted=False)

    @admin.action(description="Soft delete selected comments")
    def soft_delete(self, request, queryset):
        queryset.update(is_deleted=True)


@admin.register(CommentSettings)
class CommentSettingsAdmin(SingletonModelAdmin):
    list_display = ("enable_comments", "allow_anonymous", "enable_ai_moderation")
    fieldsets = (
        (None, {"fields": ("enable_comments", "allow_anonymous")}),
        ("AI moderation", {"fields": ("enable_ai_moderation",)}),
    )

    def has_add_permission(self, request):
        return False

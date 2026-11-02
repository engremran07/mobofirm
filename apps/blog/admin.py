from django.contrib import admin
from django.core.exceptions import ValidationError
from solo.admin import SingletonModelAdmin

from apps.governance.services import GovernanceError, submit_for_review

from .models import (
    AutoTopic,
    BlogSettings,
    Category,
    CategoryTranslation,
    Post,
    PostTranslation,
    TagTranslation,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "slug")
    search_fields = ("name", "slug")
    list_filter = ("parent",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "author",
        "status",
        "is_published",
        "is_ai_generated",
        "allow_comments",
        "noindex",
        "published_at",
    )
    list_filter = (
        "status",
        "is_published",
        "is_ai_generated",
        "allow_comments",
        "noindex",
        "category",
    )
    search_fields = ("title", "summary", "body")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("author", "category", "tags")
    raw_id_fields = ("author",)
    date_hierarchy = "published_at"
    readonly_fields = ("status", "is_published", "published_at")
    actions = ("submit_selected_for_review",)

    @admin.action(description="Submit selected posts for governed review")
    def submit_selected_for_review(self, request, queryset):
        submitted = 0
        skipped = 0
        for post in queryset:
            try:
                submit_for_review(post, actor=request.user)
            except (GovernanceError, ValidationError):
                skipped += 1
            else:
                submitted += 1
        self.message_user(
            request,
            f"Submitted {submitted} posts; {skipped} were not eligible for review.",
        )


@admin.register(BlogSettings)
class BlogSettingsAdmin(SingletonModelAdmin):
    list_display = ("enable_blog", "enable_blog_comments", "allow_user_blog_posts")
    fieldsets = (
        (None, {"fields": ("enable_blog", "enable_blog_comments")}),
        ("User posting", {"fields": ("allow_user_blog_posts",)}),
    )

    def has_add_permission(self, request):
        return False


@admin.register(AutoTopic)
class AutoTopicAdmin(admin.ModelAdmin):
    list_display = (
        "topic",
        "status",
        "post",
        "retry_count",
        "created_by",
        "created_at",
        "last_attempt_at",
    )
    list_filter = ("status", "retry_count")
    search_fields = ("topic", "post__title")
    raw_id_fields = ("post", "created_by")


@admin.register(PostTranslation)
class PostTranslationAdmin(admin.ModelAdmin):
    list_display = ("post", "language", "title")
    list_filter = ("language",)
    search_fields = ("title", "summary", "post__title")
    raw_id_fields = ("post",)


@admin.register(CategoryTranslation)
class CategoryTranslationAdmin(admin.ModelAdmin):
    list_display = ("category", "language", "name")
    list_filter = ("language",)
    search_fields = ("name", "category__name")
    raw_id_fields = ("category",)


@admin.register(TagTranslation)
class TagTranslationAdmin(admin.ModelAdmin):
    list_display = ("tag", "language", "name")
    list_filter = ("language",)
    search_fields = ("name", "tag__name")
    raw_id_fields = ("tag",)

from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import (
    AdGateLog,
    Brand,
    BrandCreationRequest,
    BrandSchema,
    ChangelogEntry,
    DownloadSession,
    DownloadToken,
    EngineeringFirmware,
    FirmwareDiff,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
    GSMArenaDevice,
    HotlinkBlock,
    Model,
    ModelCreationRequest,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    PendingFirmware,
    ReadbackFirmware,
    ReleaseNote,
    SchemaUpdateProposal,
    TestResult,
    TrustedTester,
    UnclassifiedFirmware,
    Variant,
    VariantCreationRequest,
    VerificationCredit,
    VerificationReport,
)
from .tracking_models import (
    FirmwareDownloadAttempt,
    FirmwareRequest,
    FirmwareStats,
    FirmwareView,
)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "model_count", "autofill_status", "created_at")
    list_filter = ("created_at",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "autofill_help")
    ordering = ("name",)
    fieldsets = (
        ("Basic Information", {"fields": ("name", "slug")}),
        (
            "Auto-Fill Help",
            {
                "fields": ("autofill_help",),
                "classes": ("collapse",),
                "description": "Click the button below to auto-fill missing fields from internet sources",
            },
        ),
        (
            "Metadata",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def model_count(self, obj):
        count = obj.models.count()
        return format_html('<span style="font-weight: bold;">{}</span>', count)

    model_count.short_description = "Models"

    def autofill_status(self, obj):
        """Show autofill status"""
        if obj.description:
            return format_html('<span style="color: green;">✓ Complete</span>')
        return format_html('<span style="color: orange;">⚠ Incomplete</span>')

    autofill_status.short_description = "Status"

    def autofill_help(self, obj):
        """Show autofill button and instructions"""
        if not obj.pk:
            return "Save the brand first to enable auto-fill"

        return mark_safe(f"""
            <div style="background-color: #f0f7ff; padding: 15px; border-radius: 5px; border-left: 4px solid #3b82f6;">
                <h4 style="margin-top: 0;">Auto-Fill Missing Fields</h4>
                <p>Click the button below to automatically fill missing fields from internet sources and AI:</p>
                <button type="button" onclick="autofillBrand({obj.pk})" style="
                    background-color: #3b82f6;
                    color: white;
                    padding: 8px 16px;
                    border: none;
                    border-radius: 4px;
                    cursor: pointer;
                    font-weight: bold;
                ">🔄 Auto-Fill Now</button>
                <p style="margin-top: 10px; font-size: 12px; color: #666;">
                    This will fetch data from GSMArena and AI sources to complete missing information.
                </p>
            </div>
            <div id="autofill-msg-{obj.pk}" style="display:none;margin-top:10px;padding:10px 14px;border-radius:4px;font-size:13px;"></div>
            <script>
            function autofillBrand(brandId) {{
                var msgEl = document.getElementById('autofill-msg-' + brandId);
                function showMsg(text, ok) {{
                    msgEl.textContent = text;
                    msgEl.style.display = 'block';
                    msgEl.style.backgroundColor = ok ? '#ecfdf5' : '#fef2f2';
                    msgEl.style.color = ok ? '#065f46' : '#991b1b';
                    msgEl.style.border = '1px solid ' + (ok ? '#a7f3d0' : '#fecaca');
                }}
                fetch(`/firmwares/autofill/brand/${{brandId}}/`, {{
                    method: 'POST',
                    headers: {{'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value}}
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        showMsg('Auto-fill completed! The page will reload.', true);
                        setTimeout(() => location.reload(), 1500);
                    }} else {{
                        showMsg('Auto-fill failed: ' + (data.error || 'Unknown error occurred.'), false);
                    }}
                }})
                .catch(e => showMsg('Network error: ' + e.message, false));
            }}
            </script>
        """)  # noqa: S308

    autofill_help.short_description = "Auto-Fill Assistant"

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(Count("models"))


@admin.register(Model)
class DeviceModelAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "slug", "variant_count", "created_at")
    list_filter = ("brand", "created_at")
    search_fields = ("name", "slug", "brand__name")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("brand",)
    ordering = ("brand__name", "name")

    def variant_count(self, obj):
        count = obj.variants.count()
        return format_html('<span style="font-weight: bold;">{}</span>', count)

    variant_count.short_description = "Variants"

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("brand")
            .annotate(Count("variants"))
        )


@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin):
    list_display = ("name", "model", "get_brand", "slug", "region", "created_at")
    list_filter = ("model__brand", "created_at")
    search_fields = (
        "name",
        "slug",
        "model__name",
        "model__brand__name",
        "region",
        "board_id",
    )
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("model",)
    ordering = ("model__brand__name", "model__name", "name")

    def get_brand(self, obj):
        return obj.model.brand.name

    get_brand.short_description = "Brand"
    get_brand.admin_order_field = "model__brand__name"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("model__brand")


@admin.register(BrandSchema)
class BrandSchemaAdmin(admin.ModelAdmin):
    list_display = ("brand", "approved_by", "approved_at", "created_at")
    list_filter = ("created_at", "approved_at")
    search_fields = ("brand__name",)
    autocomplete_fields = ("brand",)


@admin.register(SchemaUpdateProposal)
class SchemaUpdateProposalAdmin(admin.ModelAdmin):
    list_display = ("brand", "status_badge", "proposed_by", "reviewed_by", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("brand__name", "proposed_by__email")
    readonly_fields = ("created_at", "updated_at")

    def status_badge(self, obj):
        colors = {"pending": "orange", "approved": "green", "rejected": "red"}
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.status.upper(),
        )

    status_badge.short_description = "Status"


@admin.register(BrandCreationRequest)
class BrandCreationRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "status_badge", "requested_by", "reviewed_by", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "requested_by__email")
    readonly_fields = ("created_at", "reviewed_at")
    actions = ["approve_requests", "reject_requests"]

    def status_badge(self, obj):
        colors = {"pending": "orange", "approved": "green", "rejected": "red"}
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.status.upper(),
        )

    status_badge.short_description = "Status"

    def approve_requests(self, request, queryset):
        from django.utils.text import slugify

        count = 0
        for req in queryset.filter(status="pending"):
            Brand.objects.create(name=req.name, slug=slugify(req.name))
            req.status = "approved"
            req.reviewed_by = request.user
            req.save()
            count += 1
        self.message_user(request, f"{count} brand(s) approved and created.")

    approve_requests.short_description = "Approve selected requests"

    def reject_requests(self, request, queryset):
        count = queryset.filter(status="pending").update(
            status="rejected", reviewed_by=request.user
        )
        self.message_user(request, f"{count} request(s) rejected.")

    reject_requests.short_description = "Reject selected requests"


@admin.register(ModelCreationRequest)
class ModelCreationRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "status_badge", "requested_by", "created_at")
    list_filter = ("status", "brand", "created_at")
    search_fields = ("name", "brand__name", "requested_by__email")
    readonly_fields = ("created_at", "reviewed_at")
    autocomplete_fields = ("brand",)

    def status_badge(self, obj):
        colors = {"pending": "orange", "approved": "green", "rejected": "red"}
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.status.upper(),
        )

    status_badge.short_description = "Status"


@admin.register(VariantCreationRequest)
class VariantCreationRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "model", "status_badge", "requested_by", "created_at")
    list_filter = ("status", "model__brand", "created_at")
    search_fields = ("name", "model__name", "model__brand__name", "requested_by__email")
    readonly_fields = ("created_at", "reviewed_at")
    autocomplete_fields = ("model",)

    def status_badge(self, obj):
        colors = {"pending": "orange", "approved": "green", "rejected": "red"}
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.status.upper(),
        )

    status_badge.short_description = "Status"


@admin.register(PendingFirmware)
class PendingFirmwareAdmin(admin.ModelAdmin):
    list_display = (
        "original_file_name",
        "uploader",
        "admin_decision_badge",
        "ai_brand",
        "ai_model",
        "ai_category",
        "created_at",
    )
    list_filter = (
        "admin_decision",
        "extraction_status",
        "ai_category",
        "is_password_protected",
        "created_at",
    )
    search_fields = (
        "original_file_name",
        "ai_brand",
        "ai_model",
        "ai_variant",
        "chipset",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "id",
        "admin_decision",
        "admin_notes",
        "category_locked",
    )
    autocomplete_fields = ("uploaded_brand", "uploaded_model", "uploaded_variant")

    fieldsets = (
        (
            "File Info",
            {"fields": ("id", "original_file_name", "stored_file_path", "uploader")},
        ),
        (
            "User-provided Info",
            {"fields": ("uploaded_brand", "uploaded_model", "uploaded_variant")},
        ),
        (
            "AI Suggestions",
            {
                "fields": (
                    "ai_brand",
                    "ai_model",
                    "ai_variant",
                    "ai_category",
                    "ai_subtype",
                )
            },
        ),
        (
            "Metadata",
            {"fields": ("chipset", "partitions", "metadata"), "classes": ("collapse",)},
        ),
        (
            "Password",
            {
                "fields": (
                    "is_password_protected",
                    "encrypted_password",
                    "password_validation_status",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Admin Review",
            {
                "fields": (
                    "admin_decision",
                    "admin_notes",
                    "category_locked",
                    "extraction_status",
                )
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def admin_decision_badge(self, obj):
        colors = {"pending": "orange", "approved": "green", "rejected": "red"}
        color = colors.get(obj.admin_decision, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.admin_decision.upper(),
        )

    admin_decision_badge.short_description = "Decision"


# Firmware type admin classes
class BaseFirmwareAdmin(admin.ModelAdmin):
    list_display = (
        "original_file_name",
        "brand",
        "model",
        "variant",
        "chipset",
        "uploader",
        "created_at",
    )
    list_filter = ("brand", "is_password_protected", "created_at")
    search_fields = (
        "original_file_name",
        "brand__name",
        "model__name",
        "variant__name",
        "chipset",
    )
    readonly_fields = ("created_at", "updated_at", "id")
    autocomplete_fields = ("brand", "model", "variant")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("brand", "model", "variant", "uploader")
        )


@admin.register(OfficialFirmware)
class OfficialFirmwareAdmin(BaseFirmwareAdmin):
    pass


@admin.register(EngineeringFirmware)
class EngineeringFirmwareAdmin(BaseFirmwareAdmin):
    pass


@admin.register(ReadbackFirmware)
class ReadbackFirmwareAdmin(BaseFirmwareAdmin):
    pass


@admin.register(ModifiedFirmware)
class ModifiedFirmwareAdmin(BaseFirmwareAdmin):
    pass


@admin.register(OtherFirmware)
class OtherFirmwareAdmin(BaseFirmwareAdmin):
    pass


@admin.register(UnclassifiedFirmware)
class UnclassifiedFirmwareAdmin(BaseFirmwareAdmin):
    pass


# ---------------------------------------------------------------------------
# Changelog — absorbed from apps.changelog
# ---------------------------------------------------------------------------


class ReleaseNoteInline(admin.TabularInline):
    model = ReleaseNote
    extra = 1


@admin.register(ChangelogEntry)
class ChangelogEntryAdmin(admin.ModelAdmin):
    list_display = ["version", "title", "release_date", "is_published"]
    list_filter = ["is_published"]
    inlines = [ReleaseNoteInline]


@admin.register(FirmwareDiff)
class FirmwareDiffAdmin(admin.ModelAdmin):
    list_display = ["old_firmware", "new_firmware", "generated_at"]
    readonly_fields = ["generated_at"]


# ---------------------------------------------------------------------------
# GSMArena Sync — absorbed from apps.gsmarena_sync
# ---------------------------------------------------------------------------


@admin.register(GSMArenaDevice)
class GSMArenaDeviceAdmin(admin.ModelAdmin):
    list_display = [
        "gsmarena_id",
        "brand",
        "model_name",
        "review_status",
        "last_synced_at",
    ]
    list_filter = ["review_status", "brand"]
    search_fields = ["gsmarena_id", "brand", "model_name"]
    readonly_fields = ["last_synced_at", "reviewed_at"]


# ---------------------------------------------------------------------------
# FW Verification — absorbed from apps.fw_verification
# ---------------------------------------------------------------------------


@admin.register(TrustedTester)
class TrustedTesterAdmin(admin.ModelAdmin):
    list_display = ["user", "fw_count", "avg_rating", "is_active", "approved_at"]
    list_filter = ["is_active"]
    search_fields = ["user__email", "user__username"]


@admin.register(VerificationReport)
class VerificationReportAdmin(admin.ModelAdmin):
    list_display = ["pk", "firmware", "tester", "verdict", "status", "created_at"]
    list_filter = ["verdict", "status"]
    search_fields = ["tester__user__email"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    list_display = ["report", "test_name", "result"]
    list_filter = ["result"]
    search_fields = ["test_name"]


@admin.register(VerificationCredit)
class VerificationCreditAdmin(admin.ModelAdmin):
    list_display = ["user", "firmware", "credits_earned", "created_at"]
    readonly_fields = ["created_at"]


# ---------------------------------------------------------------------------
# Download Links — absorbed from apps.download_links
# ---------------------------------------------------------------------------


@admin.register(DownloadToken)
class DownloadTokenAdmin(admin.ModelAdmin):
    list_display = [
        "token_short",
        "firmware",
        "user",
        "status",
        "ad_gate_completed",
        "expires_at",
    ]
    list_filter = ["status", "ad_gate_required"]
    search_fields = ["token", "user__email"]
    readonly_fields = ["created_at", "used_at"]

    def token_short(self, obj: DownloadToken) -> str:
        return f"{obj.token[:12]}…"

    token_short.short_description = "Token"  # type: ignore[attr-defined]


@admin.register(DownloadSession)
class DownloadSessionAdmin(admin.ModelAdmin):
    list_display = ["pk", "token", "user", "status", "bytes_delivered", "started_at"]
    list_filter = ["status"]
    readonly_fields = ["started_at", "completed_at"]

    def has_add_permission(self, request):  # type: ignore[override]
        return False


@admin.register(AdGateLog)
class AdGateLogAdmin(admin.ModelAdmin):
    list_display = [
        "session",
        "ad_type",
        "watched_seconds",
        "completed",
        "credits_earned",
    ]
    list_filter = ["ad_type", "completed"]
    readonly_fields = ["created_at"]


@admin.register(HotlinkBlock)
class HotlinkBlockAdmin(admin.ModelAdmin):
    list_display = ["domain", "is_active", "blocked_count", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["domain"]


# ---------------------------------------------------------------------------
# Flashing Tools & Guides
# ---------------------------------------------------------------------------


@admin.register(FlashingToolCategory)
class FlashingToolCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "sort_order", "is_active"]
    list_filter = ["is_active"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(FlashingTool)
class FlashingToolAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "category",
        "tool_type",
        "platform",
        "risk_level",
        "is_active",
        "is_featured",
    ]
    list_filter = ["tool_type", "platform", "risk_level", "is_active", "is_featured"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(FlashingGuideTemplate)
class FlashingGuideTemplateAdmin(admin.ModelAdmin):
    list_display = [
        "title_template",
        "guide_type",
        "brand",
        "auto_generate",
        "is_active",
        "generated_count",
    ]
    list_filter = ["guide_type", "auto_generate", "is_active"]


@admin.register(GeneratedFlashingGuide)
class GeneratedFlashingGuideAdmin(admin.ModelAdmin):
    list_display = ["template", "brand", "model", "created_at"]
    list_filter = ["brand"]
    readonly_fields = ["created_at"]


# ---------------------------------------------------------------------------
# Tracking Models (FirmwareView, etc.)
# ---------------------------------------------------------------------------


@admin.register(FirmwareView)
class FirmwareViewAdmin(admin.ModelAdmin):
    list_display = ["content_type", "object_id", "user", "viewed_at"]
    list_filter = ["content_type"]
    readonly_fields = ["viewed_at"]

    def has_add_permission(self, request):  # type: ignore[override]
        return False


@admin.register(FirmwareDownloadAttempt)
class FirmwareDownloadAttemptAdmin(admin.ModelAdmin):
    list_display = ["content_type", "object_id", "user", "status", "initiated_at"]
    list_filter = ["status"]
    readonly_fields = ["initiated_at", "completed_at", "failed_at"]

    def has_add_permission(self, request):  # type: ignore[override]
        return False


@admin.register(FirmwareRequest)
class FirmwareRequestAdmin(admin.ModelAdmin):
    list_display = [
        "brand",
        "model",
        "firmware_type",
        "status",
        "request_count",
        "created_at",
    ]
    list_filter = ["status", "firmware_type", "urgency"]
    search_fields = ["brand__name", "model__name", "variant_name"]


@admin.register(FirmwareStats)
class FirmwareStatsAdmin(admin.ModelAdmin):
    list_display = [
        "content_type",
        "object_id",
        "date",
        "view_count",
        "successful_downloads",
    ]
    list_filter = ["date"]
    readonly_fields = ["date"]

    def has_add_permission(self, request):  # type: ignore[override]
        return False

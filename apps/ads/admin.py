from django.contrib import admin
from django.utils.html import format_html
from solo.admin import SingletonModelAdmin

from .models import (
    AdCreative,
    AdEvent,
    AdNetwork,
    AdPlacement,
    AdsSettings,
    AdUnit,
    AffiliateClick,
    AffiliateLink,
    AffiliateProduct,
    AffiliateProductCategory,
    AffiliateProductMatch,
    # Affiliate Products
    AffiliateProvider,
    AffiliateSource,
    AutoAdsScanResult,
    Campaign,
    PlacementAssignment,
    RewardedAdConfig,
    RewardedAdView,
    ScanDiscovery,
    TemplateAdExclusion,
)


@admin.register(AdPlacement)
class AdPlacementAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "context", "is_active", "locked", "updated_at")
    list_filter = ("is_active", "locked", "context")
    search_fields = ("name", "slug", "code", "context", "page_context")
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = ("created_by", "updated_by")


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "type",
        "is_active",
        "priority",
        "weight",
        "start_at",
        "end_at",
    )
    list_filter = ("type", "is_active", "ad_network")
    search_fields = ("name",)
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = ("created_by", "updated_by")


@admin.register(AdCreative)
class AdCreativeAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "campaign",
        "creative_type",
        "is_active",
        "locked",
        "weight",
    )
    list_filter = ("creative_type", "is_active", "locked", "campaign")
    search_fields = ("name", "campaign__name")
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = ("created_by", "updated_by")


@admin.register(PlacementAssignment)
class PlacementAssignmentAdmin(admin.ModelAdmin):
    list_display = ("placement", "creative", "weight", "is_active", "locked")
    list_filter = ("placement", "creative", "is_active", "locked")
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = ("created_by", "updated_by")


@admin.register(AffiliateSource)
class AffiliateSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "network", "base_url", "is_enabled", "locked", "updated_at")
    list_filter = ("network", "is_enabled", "locked")
    search_fields = ("name", "network", "base_url")
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = ("created_by", "updated_by")


@admin.register(AffiliateLink)
class AffiliateLinkAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "source",
        "affiliate_url",
        "is_active",
        "locked",
        "usage_count",
    )
    list_filter = ("source", "is_active", "locked")
    search_fields = ("name", "affiliate_url", "target_url", "source__name")
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = ("created_by", "updated_by")


@admin.register(AdEvent)
class AdEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_type",
        "campaign",
        "placement",
        "creative",
        "page_url",
        "created_at",
    )
    list_filter = ("event_type", "campaign")
    readonly_fields = (
        "event_type",
        "campaign",
        "placement",
        "creative",
        "user",
        "request_meta",
        "page_url",
        "referrer_url",
        "user_agent",
        "session_id",
        "created_at",
    )


# ==================== NEW AD NETWORK ADMIN ====================


class AdUnitInline(admin.TabularInline):
    model = AdUnit
    extra = 0
    fields = (
        "name",
        "unit_id",
        "ad_format",
        "size_label",
        "is_enabled",
        "estimated_cpm",
    )


@admin.register(AdNetwork)
class AdNetworkAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "network_type",
        "is_enabled",
        "priority",
        "supports_display",
        "supports_video",
        "supports_rewarded",
        "last_sync_at",
    )
    list_filter = (
        "network_type",
        "is_enabled",
        "supports_display",
        "supports_video",
        "supports_rewarded",
    )
    search_fields = ("name", "publisher_id")
    inlines = [AdUnitInline]
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = ("created_by", "updated_by", "last_sync_at", "sync_status")

    fieldsets = (
        ("Basic Info", {"fields": ("name", "network_type", "is_enabled", "priority")}),
        (
            "Authentication",
            {
                "fields": ("publisher_id", "api_key", "api_secret"),
                "classes": ("collapse",),
            },
        ),
        (
            "Scripts",
            {
                "fields": ("header_script", "body_script"),
                "classes": ("collapse",),
            },
        ),
        (
            "Capabilities",
            {
                "fields": (
                    "supports_display",
                    "supports_native",
                    "supports_video",
                    "supports_rewarded",
                    "supports_auto_ads",
                ),
            },
        ),
        (
            "Revenue",
            {
                "fields": ("revenue_share_percent",),
            },
        ),
        (
            "Advanced",
            {
                "fields": ("config", "last_sync_at", "sync_status"),
                "classes": ("collapse",),
            },
        ),
        (
            "Audit",
            {
                "fields": ("created_by", "updated_by"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(AdUnit)
class AdUnitAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "network",
        "unit_id",
        "ad_format",
        "size_label",
        "is_enabled",
        "estimated_cpm",
        "fill_rate",
    )
    list_filter = ("network", "ad_format", "is_enabled")
    search_fields = ("name", "unit_id", "network__name")
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = ("created_by", "updated_by")


# ==================== REWARDED ADS ADMIN ====================


@admin.register(RewardedAdConfig)
class RewardedAdConfigAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "reward_type",
        "reward_amount",
        "is_enabled",
        "cooldown_minutes",
        "daily_limit_per_user",
    )
    list_filter = ("reward_type", "is_enabled", "network")
    search_fields = ("name", "reward_description")
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = ("created_by", "updated_by")

    fieldsets = (
        (
            "Basic Info",
            {"fields": ("name", "placement", "network", "ad_unit", "is_enabled")},
        ),
        (
            "Reward Configuration",
            {"fields": ("reward_type", "reward_amount", "reward_description")},
        ),
        (
            "Limits",
            {
                "fields": (
                    "cooldown_minutes",
                    "daily_limit_per_user",
                    "min_watch_seconds",
                )
            },
        ),
        (
            "Audit",
            {
                "fields": ("created_by", "updated_by"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(RewardedAdView)
class RewardedAdViewAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "config",
        "status",
        "reward_granted",
        "reward_amount",
        "watch_duration_seconds",
        "created_at",
    )
    list_filter = ("status", "reward_granted", "config")
    search_fields = ("user__email", "user__username")
    readonly_fields = (
        "user",
        "config",
        "started_at",
        "completed_at",
        "watch_duration_seconds",
        "status",
        "reward_granted",
        "reward_type",
        "reward_amount",
        "ip_address",
        "user_agent",
        "page_url",
        "created_at",
    )

    def has_add_permission(self, request):
        return False  # Views are created programmatically


# ==================== AUTO ADS SCAN ADMIN ====================


@admin.register(AutoAdsScanResult)
class AutoAdsScanResultAdmin(admin.ModelAdmin):
    list_display = ("template_path", "score", "applied", "created_at")
    list_filter = ("applied",)
    search_fields = ("template_path", "ai_recommendation")
    readonly_fields = (
        "template_path",
        "suggested_placements",
        "content_analysis",
        "ai_recommendation",
        "score",
        "created_at",
    )

    def has_add_permission(self, request):
        return False  # Scans are created by management command


# ==================== AFFILIATE PRODUCTS ADMIN ====================


@admin.register(AffiliateProvider)
class AffiliateProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "provider_type", "is_enabled", "priority", "products_count")
    list_filter = ("provider_type", "is_enabled")
    search_fields = ("name", "associate_tag")
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = (
        "created_by",
        "updated_by",
        "last_sync_at",
        "sync_status",
        "products_count",
    )

    fieldsets = (
        ("Basic Info", {"fields": ("name", "provider_type", "is_enabled", "priority")}),
        (
            "API Credentials",
            {
                "fields": ("api_key", "api_secret", "associate_tag", "partner_id"),
                "classes": ("collapse",),
            },
        ),
        (
            "Configuration",
            {
                "fields": (
                    "api_endpoint",
                    "api_region",
                    "marketplace",
                    "default_tracking_params",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Revenue",
            {
                "fields": ("commission_rate", "cookie_duration_days"),
                "classes": ("collapse",),
            },
        ),
        (
            "Sync Status",
            {
                "fields": ("last_sync_at", "sync_status", "products_count"),
                "classes": ("collapse",),
            },
        ),
        (
            "Audit",
            {
                "fields": ("created_by", "updated_by"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(AffiliateProductCategory)
class AffiliateProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "parent", "is_active")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "slug", "device_keywords")
    prepopulated_fields = {"slug": ("name",)}

    fieldsets = (
        ("Basic Info", {"fields": ("name", "slug", "parent", "is_active")}),
        (
            "Targeting",
            {
                "fields": ("device_keywords", "brand_match", "icon"),
            },
        ),
    )


class AffiliateProductMatchInline(admin.TabularInline):
    model = AffiliateProductMatch
    extra = 0
    fields = (
        "brand",
        "model",
        "variant",
        "blog_post",
        "match_type",
        "relevance_score",
        "is_pinned",
        "is_hidden",
    )
    raw_id_fields = ("brand", "model", "variant", "blog_post")


@admin.register(AffiliateProduct)
class AffiliateProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "provider",
        "product_type",
        "display_price",
        "rating_stars",
        "is_enabled",
        "is_in_stock",
        "clicks",
        "conversions",
    )
    list_filter = (
        "provider",
        "product_type",
        "category",
        "is_enabled",
        "is_in_stock",
        "is_universal",
    )
    search_fields = ("name", "external_id", "asin", "target_keywords")
    exclude = ("is_deleted", "deleted_at", "deleted_by")
    readonly_fields = (
        "created_by",
        "updated_by",
        "price_updated_at",
        "impressions",
        "clicks",
        "conversions",
        "revenue",
    )
    inlines = [AffiliateProductMatchInline]
    filter_horizontal = ("target_brands", "target_models")
    prepopulated_fields = {"slug": ("name",)}

    fieldsets = (
        (
            "Basic Info",
            {
                "fields": (
                    "provider",
                    "category",
                    "name",
                    "slug",
                    "description",
                    "product_type",
                )
            },
        ),
        (
            "External IDs",
            {
                "fields": ("external_id", "asin", "upc", "ean"),
                "classes": ("collapse",),
            },
        ),
        (
            "Pricing",
            {
                "fields": ("price", "sale_price", "currency", "price_updated_at"),
            },
        ),
        (
            "Images",
            {
                "fields": ("image_url", "thumbnail_url", "additional_images"),
                "classes": ("collapse",),
            },
        ),
        (
            "Links",
            {
                "fields": ("product_url", "affiliate_url"),
            },
        ),
        (
            "Ratings",
            {
                "fields": ("rating", "review_count"),
            },
        ),
        (
            "Targeting",
            {
                "fields": (
                    "target_brands",
                    "target_models",
                    "target_keywords",
                    "is_universal",
                ),
            },
        ),
        (
            "Status",
            {
                "fields": ("is_enabled", "is_in_stock", "availability_message"),
            },
        ),
        (
            "Performance",
            {
                "fields": (
                    "impressions",
                    "clicks",
                    "conversions",
                    "revenue",
                    "ai_relevance_score",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "API Data",
            {
                "fields": ("api_data",),
                "classes": ("collapse",),
            },
        ),
        (
            "Audit",
            {
                "fields": ("created_by", "updated_by"),
                "classes": ("collapse",),
            },
        ),
    )

    def display_price(self, obj):
        price = obj.get_display_price()
        if obj.sale_price and obj.sale_price < obj.price:
            return format_html(
                '<span style="text-decoration: line-through; color: #888;">{} {}</span> '
                '<span style="color: #22c55e; font-weight: bold;">{} {}</span>',
                obj.currency,
                obj.price,
                obj.currency,
                obj.sale_price,
            )
        return f"{obj.currency} {price}"

    display_price.short_description = "Price"

    def rating_stars(self, obj):
        filled = int(obj.rating)
        empty = 5 - filled
        return format_html(
            '<span style="color: #fbbf24;">{}</span><span style="color: #ccc;">{}</span> ({})',
            "★" * filled,
            "☆" * empty,
            obj.review_count,
        )

    rating_stars.short_description = "Rating"


@admin.register(AffiliateProductMatch)
class AffiliateProductMatchAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "target_display",
        "match_type",
        "relevance_score",
        "is_pinned",
        "is_hidden",
    )
    list_filter = ("match_type", "is_pinned", "is_hidden")
    search_fields = ("product__name", "brand__name", "model__name")
    raw_id_fields = ("product", "brand", "model", "variant", "blog_post")

    def target_display(self, obj):
        target = obj.brand or obj.model or obj.variant or obj.blog_post
        return str(target) if target else "—"

    target_display.short_description = "Target"


@admin.register(AffiliateClick)
class AffiliateClickAdmin(admin.ModelAdmin):
    list_display = ("product", "provider", "user", "country", "created_at")
    list_filter = ("provider", "country", "created_at")
    search_fields = ("product__name", "user__email", "page_url")
    readonly_fields = (
        "product",
        "provider",
        "user",
        "session_id",
        "ip_address",
        "user_agent",
        "page_url",
        "referrer",
        "brand_slug",
        "model_slug",
        "firmware_id",
        "blog_slug",
        "country",
        "region",
        "created_at",
    )

    def has_add_permission(self, request):
        return False  # Clicks are tracked programmatically


# ==================== ADS SETTINGS ADMIN ====================


@admin.register(AdsSettings)
class AdsSettingsAdmin(SingletonModelAdmin):
    list_display = (
        "ads_enabled",
        "auto_ads_enabled",
        "rewarded_ads_enabled",
        "affiliate_products_enabled",
        "ad_aggressiveness_level",
    )

    fieldsets = (
        (
            "Master Controls",
            {
                "fields": (
                    "ads_enabled",
                    "affiliate_enabled",
                    "ad_networks_enabled",
                    "ad_aggressiveness_level",
                ),
            },
        ),
        (
            "Auto Ads",
            {
                "fields": (
                    "auto_ads_enabled",
                    "auto_ads_scan_interval",
                    "auto_ads_in_article",
                    "auto_ads_in_feed",
                    "auto_ads_anchor",
                    "auto_ads_vignette",
                    "auto_ads_sidebar",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Rewarded Ads",
            {
                "fields": (
                    "rewarded_ads_enabled",
                    "rewarded_ads_reward_type",
                    "rewarded_ads_reward_value",
                    "rewarded_ads_cooldown_minutes",
                    "rewarded_ads_daily_limit",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Affiliate Products",
            {
                "fields": (
                    "affiliate_products_enabled",
                    "affiliate_products_max_per_page",
                    "affiliate_products_cache_hours",
                    "affiliate_products_auto_fetch",
                    "affiliate_products_show_on_firmware",
                    "affiliate_products_show_on_model",
                    "affiliate_products_show_on_brand",
                    "affiliate_products_show_on_blog",
                    "affiliate_products_fallback_keywords",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Amazon Product API",
            {
                "fields": (
                    "amazon_paapi_enabled",
                    "amazon_paapi_access_key",
                    "amazon_paapi_secret_key",
                    "amazon_paapi_partner_tag",
                    "amazon_paapi_region",
                    "amazon_paapi_marketplace",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Google AdSense",
            {
                "fields": (
                    "adsense_enabled",
                    "adsense_publisher_id",
                    "adsense_auto_ads",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Media.net",
            {
                "fields": ("medianet_enabled", "medianet_customer_id"),
                "classes": ("collapse",),
            },
        ),
        (
            "Premium Networks",
            {
                "fields": (
                    "ezoic_enabled",
                    "ezoic_site_id",
                    "adthrive_enabled",
                    "adthrive_site_id",
                    "mediavine_enabled",
                    "mediavine_site_id",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Other Networks",
            {
                "fields": (
                    "propellerads_enabled",
                    "propellerads_zone_id",
                    "amazon_ads_enabled",
                    "amazon_ads_publisher_id",
                    "infolinks_enabled",
                    "infolinks_pid",
                    "carbon_enabled",
                    "carbon_serve_id",
                    "buysellads_enabled",
                    "buysellads_zone_id",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Header Bidding",
            {
                "fields": (
                    "header_bidding_enabled",
                    "prebid_timeout_ms",
                    "prebid_bidders",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Native Ads",
            {
                "fields": ("native_ads_enabled", "native_ads_style"),
                "classes": ("collapse",),
            },
        ),
        (
            "AI Optimization",
            {
                "fields": (
                    "ai_optimization_enabled",
                    "ai_model_provider",
                    "ai_optimize_placements",
                    "ai_optimize_creatives",
                    "ai_optimize_bidding",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Consent & Privacy",
            {
                "fields": (
                    "require_consent",
                    "consent_mode_default",
                    "show_ads_without_consent",
                    "tcf_enabled",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Frequency & Limits",
            {
                "fields": (
                    "max_ads_per_page",
                    "min_content_between_ads",
                    "refresh_interval_seconds",
                    "lazy_load_ads",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Exclusions",
            {
                "fields": (
                    "excluded_pages",
                    "excluded_categories",
                    "excluded_user_roles",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Custom Configuration",
            {
                "fields": ("custom_config",),
                "classes": ("collapse",),
            },
        ),
    )

    def has_add_permission(self, request):
        # Singleton – always edit existing row
        return False


# ==================== SCAN DISCOVERY ADMIN ====================


@admin.register(ScanDiscovery)
class ScanDiscoveryAdmin(admin.ModelAdmin):
    list_display = (
        "placement_code",
        "placement_name",
        "zone",
        "confidence",
        "status",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("status", "zone")
    search_fields = ("placement_code", "placement_name", "description")
    readonly_fields = ("scan_result", "reviewed_by", "reviewed_at", "placement")
    list_editable = ("status",)


# ==================== TEMPLATE AD EXCLUSION ADMIN ====================


@admin.register(TemplateAdExclusion)
class TemplateAdExclusionAdmin(admin.ModelAdmin):
    list_display = ("template_path", "reason", "excluded_by", "created_at")
    search_fields = ("template_path", "reason")
    readonly_fields = ("excluded_by",)

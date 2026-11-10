"""Permission-aware sidebar, tabs and account links for the Unfold admin.

The navigation is declared once below and rendered per request: an entry appears
only when its model is registered in the admin and the user may view or change it
(or, for staff tools outside the admin, holds the stated permission). Registered
models that no group lists are appended to an "Other" group so nothing is ever
unreachable; ``tests/backend/admin/test_admin_all_models.py`` fails if that group
is ever non-empty.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from django.apps import apps as django_apps
from django.contrib import admin
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext as _

from apps.core.admin_dashboard import metric_badge


@dataclass(frozen=True, slots=True)
class ModelEntry:
    label: str  # "app_label.modelname"
    icon: str
    title: str = ""
    badge: str = ""
    badge_variant: str = "warning"


@dataclass(frozen=True, slots=True)
class UrlEntry:
    title: str
    icon: str
    url_name: str
    permission: str = ""


@dataclass(frozen=True, slots=True)
class NavGroup:
    title: str
    entries: tuple[ModelEntry | UrlEntry, ...]
    collapsible: bool = True


def m(label: str, icon: str, title: str = "", badge: str = "") -> ModelEntry:
    return ModelEntry(label, icon, title, badge)


def u(title: str, icon: str, url_name: str, permission: str = "") -> UrlEntry:
    return UrlEntry(title, icon, url_name, permission)


NAV_GROUPS: tuple[NavGroup, ...] = (
    NavGroup(
        "Catalog",
        (
            m("firmwares.brand", "business", "Brands"),
            m("firmwares.model", "smartphone", "Models"),
            m("firmwares.variant", "devices", "Variants"),
            m("firmwares.chipset", "memory", "Chipsets"),
        ),
    ),
    NavGroup(
        "Review queues",
        (
            m(
                "firmwares.brandcreationrequest",
                "add_business",
                "Brand requests (review)",
                badge="apps.core.admin_navigation.badge_brand_requests",
            ),
            m(
                "firmwares.modelcreationrequest",
                "add_circle",
                "Model requests (review)",
                badge="apps.core.admin_navigation.badge_model_requests",
            ),
            m(
                "firmwares.variantcreationrequest",
                "add_box",
                "Variant requests (review)",
                badge="apps.core.admin_navigation.badge_variant_requests",
            ),
            m(
                "firmwares.chipsetcreationrequest",
                "memory",
                "Chipset requests (review)",
                badge="apps.core.admin_navigation.badge_chipset_requests",
            ),
            m(
                "firmwares.firmwarerequest",
                "help",
                "Firmware requests (review)",
                badge="apps.core.admin_navigation.badge_firmware_requests",
            ),
            m(
                "firmwares.accesschangerequest",
                "swap_vert",
                "Access change requests (review)",
            ),
        ),
    ),
    NavGroup(
        "Firmware",
        (
            m(
                "firmwares.pendingfirmware",
                "pending_actions",
                "Pending review",
                badge="apps.core.admin_navigation.badge_pending_firmware",
            ),
            m("firmwares.officialfirmware", "verified", "Official"),
            m("firmwares.engineeringfirmware", "engineering", "Engineering"),
            m("firmwares.readbackfirmware", "settings_backup_restore", "Readback"),
            m("firmwares.modifiedfirmware", "build", "Modified"),
            m("firmwares.otherfirmware", "folder_zip", "Other"),
            m("firmwares.unclassifiedfirmware", "help_center", "Unclassified"),
            m("firmwares.firmwarediff", "difference", "Firmware diffs"),
            m("firmwares.changelogentry", "history_edu", "Changelog"),
            m("firmwares.automatedbloglink", "link", "Automated blog links"),
            m("firmwares.accesschangelog", "history", "Access change history"),
            m("firmwares.autofeaturesettings", "auto_awesome", "Auto-feature settings"),
        ),
    ),
    NavGroup(
        "Tools & guides",
        (
            m("firmwares.flashingtool", "construction", "Flashing tools"),
            m("firmwares.flashingtoolcategory", "category", "Tool categories"),
            m("firmwares.flashingguidetemplate", "article", "Guide templates"),
            m("firmwares.generatedflashingguide", "auto_stories", "Generated guides"),
        ),
    ),
    NavGroup(
        "Downloads & testing",
        (
            m("firmwares.downloadtoken", "key", "Download tokens"),
            m("firmwares.downloadsession", "download", "Download sessions"),
            m("firmwares.serveddownload", "library_books", "Served downloads"),
            m("firmwares.adgatelog", "ads_click", "Ad gate log"),
            m("firmwares.hotlinkblock", "block", "Hotlink blocks"),
            m("firmwares.firmwaredownloadattempt", "downloading", "Download attempts"),
            m("firmwares.firmwareview", "visibility", "Firmware views"),
            m("firmwares.firmwarestats", "bar_chart", "Daily stats"),
            m("firmwares.trustedtester", "person_check", "Trusted testers"),
            m("firmwares.verificationreport", "task_alt", "Verification reports"),
            m("firmwares.testresult", "science", "Test results"),
            m("firmwares.verificationcredit", "stars", "Verification credits"),
        ),
    ),
    NavGroup(
        "Content",
        (
            m("blog.post", "edit_note", "Blog posts"),
            m("blog.category", "folder", "Categories"),
            m("blog.guiderequest", "help_center", "Guide requests (review)"),
            m(
                "tags.tag",
                "sell",
                "Tags",
                badge="apps.core.admin_navigation.badge_tags",
            ),
            m(
                "comments.comment",
                "forum",
                "Comments",
                badge="apps.core.admin_navigation.badge_comments",
            ),
            m("blog.autotopic", "smart_toy", "Automation queue"),
            m("blog.posttranslation", "translate", "Post translations"),
            m("blog.categorytranslation", "translate", "Category translations"),
            m("blog.tagtranslation", "translate", "Tag translations"),
            m("tags.keywordprovider", "key_visualizer", "Keyword providers"),
            m("tags.keywordsuggestion", "lightbulb", "Keyword suggestions"),
            u("Tag manager", "tune", "tags:manage", "tags.view_tag"),
            u("Similar tags", "merge", "tags:duplicates_review", "tags.change_tag"),
            u(
                "Keyword review",
                "rate_review",
                "tags:keyword_review",
                "tags.change_tag",
            ),
            u(
                "Moderation queue",
                "gavel",
                "comments:moderation_queue",
                "comments.change_comment",
            ),
        ),
    ),
    NavGroup(
        "SEO",
        (
            u("SEO dashboard", "dashboard", "seo:dashboard", "seo.view_issue"),
            u("Inventory", "inventory", "seo:inventory", "seo.view_issue"),
            u("Page inspector", "search", "seo:inspect", "seo.view_issue"),
            u("Issues board", "bug_report", "seo:issues", "seo.view_issue"),
            u("Interlinking", "add_link", "seo:links", "seo.view_linksuggestion"),
            u("Flash guides", "menu_book", "seo:guides", "seo.view_issue"),
            u("Change ledger", "fact_check", "seo:ledger", "seo.view_changeledger"),
            u("Job queue", "schedule", "seo:jobs", "seo.view_seojob"),
            u("SEO control settings", "tune", "seo:settings", "seo.view_seosettings"),
            m("seo.metadata", "label", "Metadata overrides"),
            m("seo.redirect", "alt_route", "Redirects"),
            m("seo.sitemapentry", "map", "Sitemap entries"),
            m("seo.linkableentity", "hub", "Linkable entities"),
            m("seo.linksuggestion", "add_link", "Link suggestions"),
            m("seo.interlinkexclusion", "link_off", "Link exclusions"),
            m("seo.seoentity", "account_tree", "Entities"),
            m("seo.seoentityedge", "share", "Entity relations"),
            m("seo.seokeywordhistory", "trending_up", "Keyword history"),
            m("seo.seomodel", "dataset", "SEO records"),
            m("seo.crawlrun", "travel_explore", "Crawl runs"),
            m("seo.pagesnapshot", "list_alt", "Scanned pages"),
            m("seo.pageobservation", "pageview", "Page observations"),
            m(
                "seo.issue",
                "bug_report",
                "Issues",
                badge="apps.core.admin_navigation.badge_seo_issues",
            ),
            m(
                "seo.changeledger",
                "fact_check",
                "Change ledger",
                badge="apps.core.admin_navigation.badge_seo_proposals",
            ),
            m("seo.seojob", "schedule", "Jobs"),
        ),
    ),
    NavGroup("Pages", (m("pages.page", "description", "Pages"),)),
    NavGroup(
        "Shop",
        (
            m(
                "shop.order",
                "receipt_long",
                "Orders",
                badge="apps.core.admin_navigation.badge_orders",
            ),
            m("shop.packagetier", "layers", "Packages"),
            m("shop.userpackage", "badge", "User packages"),
            m("shop.packagequotaoverride", "speed", "Package quota overrides"),
            m("shop.userquotaoverride", "manage_accounts", "Account overrides"),
            m("shop.quotaoverrideaudit", "history", "Override audit"),
            m("shop.quotaconsumption", "data_usage", "Quota consumption"),
            m("shop.featuredblogpurchase", "article", "Featured blog purchases"),
            m("shop.shopsettings", "tune", "Shop settings"),
        ),
    ),
    NavGroup(
        "Marketplace",
        (
            m(
                "marketplace.listing",
                "storefront",
                "Listings",
                badge="apps.core.admin_navigation.badge_listings",
            ),
            m("marketplace.sellerprofile", "store", "Sellers"),
            m("marketplace.sellerverification", "verified_user", "Seller verification"),
            m("marketplace.sellerearning", "payments", "Seller earnings"),
        ),
    ),
    NavGroup(
        "Credits economy",
        (
            m("wallet.rewardrule", "redeem", "Reward rules"),
            m("wallet.creditprice", "sell", "Credit prices"),
            m("wallet.wallet", "account_balance_wallet", "Wallets"),
            m("wallet.ledgerentry", "receipt_long", "Ledger"),
            m("wallet.topuprequest", "add_card", "Top-up requests"),
            m("wallet.payoutrequest", "payments", "Payout requests"),
            m("wallet.escrowhold", "lock_clock", "Escrow holds"),
            m("wallet.rewardrefusal", "flag", "Abuse flags"),
            m("wallet.walletsettings", "tune", "Credits & pricing"),
            m("referral.referral", "group_add", "Referrals"),
            m("referral.referralcode", "link", "Referral codes"),
            m("referral.referralsettings", "settings", "Referral settings"),
        ),
    ),
    NavGroup(
        "Firmware storage",
        (
            u(
                "Storage status",
                "hard_drive",
                "storage:status",
                "storage.view_firmwarestoragelocation",
            ),
            m("storage.firmwarestoragelocation", "cloud_done", "Storage locations"),
        ),
    ),
    NavGroup(
        "Ads",
        (
            u("Ads dashboard", "insights", "ads:dashboard", "ads.view_adplacement"),
            m("ads.adplacement", "ad_units", "Placements"),
            m("ads.campaign", "campaign", "Campaigns"),
            m("ads.adcreative", "image", "Creatives"),
            m("ads.placementassignment", "assignment", "Placement assignments"),
            m("ads.adnetwork", "lan", "Ad networks"),
            m("ads.adunit", "view_module", "Ad units"),
            m("ads.adevent", "ads_click", "Ad events"),
            m("ads.rewardedadconfig", "redeem", "Rewarded configs"),
            m("ads.rewardedadview", "smart_display", "Rewarded views"),
            m("ads.rewardedadgrant", "redeem", "Rewarded grants"),
            m("ads.affiliatesource", "handshake", "Affiliate sources"),
            m("ads.affiliatelink", "link", "Affiliate links"),
            m("ads.affiliateprovider", "storefront", "Affiliate providers"),
            m("ads.affiliateproductcategory", "category", "Product categories"),
            m("ads.affiliateproduct", "shopping_bag", "Affiliate products"),
            m("ads.affiliateproductmatch", "join_inner", "Product matches"),
            m("ads.affiliateclick", "ads_click", "Affiliate clicks"),
            m("ads.autoadsscanresult", "radar", "Auto-ads scans"),
            m("ads.scandiscovery", "pageview", "Scan discoveries"),
            m("ads.templateadexclusion", "block", "Template exclusions"),
        ),
    ),
    NavGroup(
        "Users",
        (
            m("users.customuser", "person", "Users"),
            m("auth.group", "groups", "Groups"),
            m("users.profile", "badge", "Profiles"),
            m("users.reputation", "military_tech", "Reputation"),
            m("users.following", "person_add", "Following"),
            m("users.activityfeed", "timeline", "Activity feed"),
            m("users.mfadevice", "phonelink_lock", "MFA devices"),
            m("users.securityquestion", "quiz", "Security questions"),
            m("account.emailaddress", "mail", "Email addresses"),
            m("socialaccount.socialapp", "apps", "Social apps"),
            m("socialaccount.socialaccount", "account_circle", "Social accounts"),
            m("socialaccount.socialtoken", "token", "Social tokens"),
            m("authtoken.tokenproxy", "vpn_key", "API tokens"),
        ),
    ),
    NavGroup(
        "Devices",
        (
            m("devices.device", "devices", "Devices"),
            m("devices.deviceevent", "event_note", "Device events"),
            m("devices.deviceslotrequest", "add_circle", "Slot requests"),
            m("devices.deviceslotreset", "restart_alt", "Slot resets"),
            m("devices.deviceresetallowance", "event_repeat", "Reset allowances"),
            m("devices.apppolicy", "policy", "App policies"),
            m("devices.devicefingerprint", "fingerprint", "Fingerprints"),
            m("devices.trustscore", "shield", "Trust scores"),
            m("devices.registryevent", "list_alt", "Registry events"),
            m("devices.behaviorinsight", "insights", "Behavior insights"),
        ),
    ),
    NavGroup(
        "Campaigns",
        (
            m("promotions.promotion", "campaign", "Promotions"),
            m("promotions.promotiongrant", "redeem", "Grants"),
            m("promotions.promotionprogress", "trending_up", "Earn progress"),
            m("promotions.promotionlog", "history", "Campaign log"),
        ),
    ),
    NavGroup(
        "Notifications",
        (
            u(
                "Notification dashboard",
                "notifications_active",
                "admin:users_notification_dashboard",
                "users.view_notification",
            ),
            m("users.notification", "notifications", "Notifications"),
            m("users.notificationpreferences", "tune", "Preferences"),
            m("users.pushsubscription", "web_asset", "Push subscriptions"),
            m("users.announcement", "campaign", "Announcements"),
        ),
    ),
    NavGroup(
        "Community",
        (
            m("gamification.badge", "military_tech", "Badges"),
            m("gamification.userbadge", "workspace_premium", "Awarded badges"),
            m("gamification.xprule", "tune", "XP rules"),
            m("gamification.level", "stairs", "Levels"),
            m("gamification.xptransaction", "history", "XP ledger"),
            m("gamification.streak", "local_fire_department", "Streaks"),
            m("bounty.bounty", "request_quote", "Bounties"),
            m(
                "bounty.bountydispute",
                "gavel",
                "Bounty reports (review)",
                badge="apps.core.admin_navigation.badge_bounty_reports",
            ),
            m("bounty.bountylog", "history", "Bounty log"),
        ),
    ),
    NavGroup(
        "Distribution",
        (
            u(
                "Dashboard",
                "monitoring",
                "admin:distribution_channel_dashboard",
                "distribution.view_channel",
            ),
            m("distribution.channel", "podcasts", "Channels"),
            m("distribution.campaign", "campaign", "Campaign presets"),
            u(
                "Content calendar",
                "calendar_month",
                "admin:distribution_channel_calendar",
                "distribution.view_sharejob",
            ),
            u(
                "Approval queue",
                "fact_check",
                "admin:distribution_channel_review",
                "distribution.change_sharejob",
            ),
            m("distribution.sharejob", "send", "Share queue"),
            u(
                "Link analytics",
                "insights",
                "admin:distribution_channel_analytics",
                "distribution.view_shortlink",
            ),
            m("distribution.shortlink", "link", "Short links"),
            m("distribution.sharelog", "list_alt", "Distribution log"),
            m("distribution.mediaasset", "image", "Generated media"),
            m("distribution.distributionsettings", "tune", "Retention settings"),
        ),
    ),
    NavGroup(
        "Compliance",
        (
            u(
                "Consent statistics",
                "bar_chart",
                "admin:consent_consentdecision_stats",
                "consent.view_consentdecision",
            ),
            m("consent.consentpolicy", "policy", "Policy versions"),
            m("consent.consentcategory", "category", "Categories"),
            m("consent.consentdecision", "fact_check", "Consent records"),
        ),
    ),
    NavGroup(
        "System",
        (
            m("site_settings.sitesettings", "settings", "Site settings"),
            m("site_settings.editorprofile", "edit_note", "Editor profiles"),
            m("site_settings.editorimage", "image", "Editor images"),
            m("site_settings.verificationmetatag", "code", "Verification meta tags"),
            m("site_settings.verificationfile", "upload_file", "Verification files"),
            m("core.appregistry", "toggle_on", "App registry"),
            m("core.searchsettings", "search", "Search and popularity"),
            m("users.userssettings", "manage_accounts", "Users settings"),
            m("devices.deviceconfig", "devices_other", "Device settings"),
            m("blog.blogsettings", "article", "Blog settings"),
            m("comments.commentsettings", "forum", "Comment settings"),
            m("tags.tagssettings", "sell", "Tag settings"),
            m("ads.adssettings", "ads_click", "Ads settings"),
            m("seo.seosettings", "travel_explore", "SEO settings"),
            m("sites.site", "language", "Sites"),
            u("Health check", "monitor_heart", "health-ready"),
        ),
    ),
)

BADGE_VARIANTS = {"danger", "warning", "info", "success", "primary"}


def badge_pending_firmware(request: HttpRequest) -> str:
    return metric_badge(request, "pending_firmware")


def badge_comments(request: HttpRequest) -> str:
    return metric_badge(request, "pending_comments")


def badge_brand_requests(request: HttpRequest) -> str:
    return metric_badge(request, "pending_brand_requests")


def badge_model_requests(request: HttpRequest) -> str:
    return metric_badge(request, "pending_model_requests")


def badge_variant_requests(request: HttpRequest) -> str:
    return metric_badge(request, "pending_variant_requests")


def badge_chipset_requests(request: HttpRequest) -> str:
    return metric_badge(request, "pending_chipset_requests")


def badge_firmware_requests(request: HttpRequest) -> str:
    return metric_badge(request, "open_firmware_requests")


def badge_tags(request: HttpRequest) -> str:
    return metric_badge(request, "unapproved_tags")


def badge_seo_issues(request: HttpRequest) -> str:
    return metric_badge(request, "open_seo_issues")


def badge_seo_proposals(request: HttpRequest) -> str:
    return metric_badge(request, "seo_proposals")


def badge_orders(request: HttpRequest) -> str:
    return metric_badge(request, "pending_orders")


def badge_listings(request: HttpRequest) -> str:
    return metric_badge(request, "pending_listings")


def badge_bounty_reports(request: HttpRequest) -> str:
    return metric_badge(request, "open_bounty_reports")


def changelist_url(model_label: str) -> str | None:
    """Admin changelist URL for ``app_label.modelname`` or None if unregistered."""
    app_label, _sep, model_name = model_label.partition(".")
    try:
        return reverse(f"admin:{app_label}_{model_name}_changelist")
    except NoReverseMatch:
        return None


def _registered_models() -> dict[str, Any]:
    return {
        model._meta.label_lower: admin.site.get_model_admin(model)
        for model in django_apps.get_models()
        if admin.site.is_registered(model)
    }


def listed_model_labels() -> set[str]:
    return {
        entry.label
        for group in NAV_GROUPS
        for entry in group.entries
        if isinstance(entry, ModelEntry)
    }


def _model_item(
    request: HttpRequest, entry: ModelEntry, registry: dict[str, Any]
) -> dict[str, Any] | None:
    model_admin = registry.get(entry.label)
    if model_admin is None or not model_admin.has_view_or_change_permission(request):
        return None
    url = changelist_url(entry.label)
    if url is None:
        return None
    item: dict[str, Any] = {
        "title": _(entry.title)
        if entry.title
        else str(model_admin.opts.verbose_name_plural).capitalize(),
        "icon": entry.icon,
        "link": url,
    }
    if entry.badge:
        item["badge"] = entry.badge
        item["badge_variant"] = entry.badge_variant
    return item


def _url_item(request: HttpRequest, entry: UrlEntry) -> dict[str, Any] | None:
    user = request.user
    if not user.is_active or not user.is_staff:
        return None
    if entry.permission and not user.has_perm(entry.permission):
        return None
    try:
        url = reverse(entry.url_name)
    except NoReverseMatch:
        return None
    return {"title": _(entry.title), "icon": entry.icon, "link": url}


def build_sidebar(request: HttpRequest) -> list[dict[str, Any]]:
    registry = _registered_models()
    groups: list[dict[str, Any]] = [
        {
            "title": _("Overview"),
            "separator": False,
            "items": [
                {
                    "title": _("Dashboard"),
                    "icon": "dashboard",
                    "link": reverse("admin:index"),
                }
            ],
        }
    ]
    for group in NAV_GROUPS:
        items = [
            item
            for entry in group.entries
            if (
                item := (
                    _model_item(request, entry, registry)
                    if isinstance(entry, ModelEntry)
                    else _url_item(request, entry)
                )
            )
        ]
        if items:
            groups.append(
                {
                    "title": _(group.title),
                    "separator": True,
                    "collapsible": group.collapsible,
                    "items": items,
                }
            )
    other = _other_group(request, registry)
    if other:
        groups.append(other)
    _mark_active_item(request, groups)
    return groups


def _link_path(link: str) -> str:
    return link.partition("?")[0]


def _mark_active_item(request: HttpRequest, groups: list[dict[str, Any]]) -> None:
    """Highlight exactly one sidebar entry: the one with the longest matching path.

    Unfold's own prefix test also lights a parent entry (a model changelist) when the
    page is a child URL (that model's dashboard), so two rows looked selected.
    """
    path = request.path
    index_path = reverse("admin:index")
    best: dict[str, Any] | None = None
    best_length = -1
    all_items = [item for group in groups for item in group["items"]]
    for item in all_items:
        item_path = _link_path(str(item["link"]))
        if item_path == index_path:
            matches = path == index_path
        else:
            matches = path == item_path or path.startswith(item_path.rstrip("/") + "/")
        if matches and len(item_path) > best_length:
            best, best_length = item, len(item_path)
    for item in all_items:
        item["active"] = item is best


def unlisted_models() -> list[str]:
    """Registered model labels missing from every navigation group."""
    return sorted(set(_registered_models()) - listed_model_labels())


def _other_group(
    request: HttpRequest, registry: dict[str, Any]
) -> dict[str, Any] | None:
    by_app: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for label in unlisted_models():
        entry = ModelEntry(label, "table_chart")
        item = _model_item(request, entry, registry)
        if item is not None:
            by_app[label.partition(".")[0]].append(item)
    if not by_app:
        return None
    items = [item for app_label in sorted(by_app) for item in by_app[app_label]]
    return {
        "title": _("Other"),
        "separator": True,
        "collapsible": True,
        "items": items,
    }


def _tab(
    request: HttpRequest,
    models: list[str],
    items: list[tuple[str, str, str]],
) -> dict[str, Any] | None:
    """Build one Unfold tab group; items are (title, model label, query string)."""
    registry = _registered_models()
    tab_items = []
    for title, label, query in items:
        model_admin = registry.get(label)
        url = changelist_url(label)
        if (
            model_admin is None
            or url is None
            or not model_admin.has_view_or_change_permission(request)
        ):
            continue
        tab_items.append(
            {"title": _(title), "link": f"{url}?{query}" if query else url}
        )
    if len(tab_items) < 2:
        return None
    return {
        "models": [label for label in models if label in registry],
        "items": tab_items,
    }


def _status_tabs(
    request: HttpRequest,
    label: str,
    field: str,
    statuses: list[tuple[str, str]],
) -> dict[str, Any] | None:
    items = [("All", label, "")] + [
        (title, label, f"{field}__exact={value}") for title, value in statuses
    ]
    return _tab(request, [label], items)


def build_tabs(request: HttpRequest) -> list[dict[str, Any]]:
    firmware_types = [
        ("Pending review", "firmwares.pendingfirmware"),
        ("Official", "firmwares.officialfirmware"),
        ("Engineering", "firmwares.engineeringfirmware"),
        ("Readback", "firmwares.readbackfirmware"),
        ("Modified", "firmwares.modifiedfirmware"),
        ("Other", "firmwares.otherfirmware"),
        ("Unclassified", "firmwares.unclassifiedfirmware"),
    ]
    tabs = [
        _tab(
            request,
            [label for _title, label in firmware_types],
            [(title, label, "") for title, label in firmware_types],
        ),
        _status_tabs(
            request,
            "firmwares.pendingfirmware",
            "admin_decision",
            [
                ("Pending", "pending"),
                ("Approved", "approved"),
                ("Rejected", "rejected"),
            ],
        ),
        _status_tabs(
            request,
            "comments.comment",
            "status",
            [
                ("Pending", "pending"),
                ("Approved", "approved"),
                ("Rejected", "rejected"),
                ("Spam", "spam"),
            ],
        ),
        _status_tabs(
            request,
            "blog.post",
            "status",
            [
                ("Drafts", "draft"),
                ("In review", "in_review"),
                ("Scheduled", "scheduled"),
                ("Published", "published"),
                ("Archived", "archived"),
            ],
        ),
        _status_tabs(
            request,
            "seo.issue",
            "status",
            [("Open", "open"), ("Resolved", "resolved"), ("Ignored", "ignored")],
        ),
        _status_tabs(
            request,
            "seo.changeledger",
            "status",
            [
                ("Proposed", "proposed"),
                ("Applied", "applied"),
                ("Rejected", "rejected"),
                ("Rolled back", "rolled_back"),
            ],
        ),
        _status_tabs(
            request,
            "shop.order",
            "status",
            [
                ("Pending", "pending"),
                ("Paid", "paid"),
                ("Refunded", "refunded"),
                ("Cancelled", "cancelled"),
            ],
        ),
        _status_tabs(
            request,
            "marketplace.listing",
            "status",
            [
                ("Pending", "pending"),
                ("Approved", "approved"),
                ("Rejected", "rejected"),
            ],
        ),
        _status_tabs(
            request,
            "marketplace.sellerverification",
            "status",
            [
                ("Pending", "pending"),
                ("Approved", "approved"),
                ("Rejected", "rejected"),
            ],
        ),
        _tab(
            request,
            ["tags.tag"],
            [
                ("All", "tags.tag", ""),
                ("Unapproved", "tags.tag", "is_curated__exact=0"),
                ("Curated", "tags.tag", "is_curated__exact=1"),
                ("AI suggested", "tags.tag", "ai_suggested__exact=1"),
            ],
        ),
    ]
    return [tab for tab in tabs if tab is not None]


def account_links(request: HttpRequest) -> list[dict[str, str]]:
    links = []
    for title, url_name in (
        (_("My dashboard"), "users:dashboard"),
        (_("My profile"), "users:profile"),
    ):
        try:
            links.append({"title": title, "link": reverse(url_name)})
        except NoReverseMatch:
            continue
    return links

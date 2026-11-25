"""Declarative sidebar catalogue of the staff console.

Pure data: :mod:`apps.core.admin_navigation` resolves it per request (registration,
permissions, badges, active state). ``icon`` values are Lucide icon names and must be
registered in the console icon set (``frontend/src/admin/js``); a test compares both.
A ``badge`` is the key of a cached metric from :mod:`apps.core.admin_dashboard`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """A model changelist link; shown when the model is registered and viewable."""

    label: str  # "app_label.modelname"
    icon: str
    title: str = ""
    badge: str = ""  # metric key shown as a count badge
    badge_variant: str = "warning"


@dataclass(frozen=True, slots=True)
class UrlEntry:
    """A staff tool outside the model admin, gated by an optional permission."""

    title: str
    icon: str
    url_name: str
    permission: str = ""


@dataclass(frozen=True, slots=True)
class NavGroup:
    title: str
    entries: tuple[ModelEntry | UrlEntry, ...]
    icon: str = "folder"
    collapsible: bool = True


def m(label: str, icon: str, title: str = "", badge: str = "") -> ModelEntry:
    return ModelEntry(label, icon, title, badge)


def u(title: str, icon: str, url_name: str, permission: str = "") -> UrlEntry:
    return UrlEntry(title, icon, url_name, permission)


NAV_GROUPS: tuple[NavGroup, ...] = (
    NavGroup(
        "Catalog",
        (
            m("firmwares.brand", "building", "Brands"),
            m("firmwares.chipsetvendor", "building", "Chipset vendors"),
            m("firmwares.model", "smartphone", "Models"),
            m("firmwares.variant", "tablet-smartphone", "Variants"),
            m("firmwares.chipset", "cpu", "Chipsets"),
        ),
        icon="library-big",
    ),
    NavGroup(
        "Review queues",
        (
            m(
                "firmwares.brandcreationrequest",
                "building",
                "Brand requests (review)",
                badge="pending_brand_requests",
            ),
            m(
                "firmwares.modelcreationrequest",
                "smartphone",
                "Model requests (review)",
                badge="pending_model_requests",
            ),
            m(
                "firmwares.variantcreationrequest",
                "tablet-smartphone",
                "Variant requests (review)",
                badge="pending_variant_requests",
            ),
            m(
                "firmwares.chipsetcreationrequest",
                "cpu",
                "Chipset requests (review)",
                badge="pending_chipset_requests",
            ),
            m(
                "firmwares.firmwarerequest",
                "circle-question-mark",
                "Firmware requests (review)",
                badge="open_firmware_requests",
            ),
            m(
                "firmwares.catalogupdateproposal",
                "table",
                "Catalog update proposals",
            ),
            m(
                "firmwares.firmwarelinkreport",
                "table",
                "External firmware link reports",
            ),
            m(
                "firmwares.accesschangerequest",
                "arrow-left-right",
                "Access change requests (review)",
            ),
        ),
        icon="inbox",
    ),
    NavGroup(
        "Firmware",
        (
            m(
                "firmwares.pendingfirmware",
                "clock",
                "Pending review",
                badge="pending_firmware",
            ),
            m("firmwares.officialfirmware", "badge-check", "Official"),
            m("firmwares.engineeringfirmware", "hammer", "Service / Engineering"),
            m("firmwares.readbackfirmware", "database-backup", "Readback"),
            m("firmwares.modifiedfirmware", "pencil-ruler", "Modified"),
            m("firmwares.otherfirmware", "folder-archive", "Other"),
            m("firmwares.unclassifiedfirmware", "file-question-mark", "Unclassified"),
            m("firmwares.firmwarediff", "diff", "Firmware diffs"),
            m("firmwares.changelogentry", "scroll-text", "Changelog"),
            m("firmwares.automatedbloglink", "link", "Automated blog links"),
            m("firmwares.accesschangelog", "rotate-ccw-clock", "Access change history"),
            m("firmwares.autofeaturesettings", "sparkles", "Auto-feature settings"),
        ),
        icon="package-open",
    ),
    NavGroup(
        "Tools & guides",
        (
            m("firmwares.flashingtool", "wrench", "Flashing tools"),
            m("firmwares.flashingtoolcategory", "shapes", "Tool categories"),
            m("firmwares.flashingguidetemplate", "notebook-text", "Guide templates"),
            m("firmwares.generatedflashingguide", "book-open", "Generated guides"),
        ),
        icon="wrench",
    ),
    NavGroup(
        "Downloads & testing",
        (
            m("firmwares.downloadtoken", "key-round", "Download tokens"),
            m("firmwares.downloadsession", "download", "Download sessions"),
            m("firmwares.serveddownload", "library", "Served downloads"),
            m("firmwares.adgatelog", "mouse-pointer-click", "Ad gate log"),
            m("firmwares.hotlinkblock", "ban", "Hotlink blocks"),
            m(
                "firmwares.shortenersettings",
                "sliders-horizontal",
                "Link shortener settings",
            ),
            m("firmwares.downloadshortlink", "link", "Short download links"),
            m("firmwares.thirdpartyshortlink", "link-2", "Provider short links"),
            m("firmwares.shorteneroutcome", "list", "Shortener log"),
            m(
                "firmwares.firmwareshorteneroverride",
                "sliders-horizontal",
                "Shortener overrides",
            ),
            m(
                "firmwares.firmwaredownloadattempt",
                "arrow-down-to-line",
                "Download attempts",
            ),
            m("firmwares.firmwareview", "eye", "Firmware views"),
            m("firmwares.firmwarestats", "chart-column", "Daily stats"),
            m("firmwares.trustedtester", "user-check", "Trusted testers"),
            m("firmwares.verificationreport", "circle-check", "Verification reports"),
            m("firmwares.testresult", "flask-conical", "Test results"),
            m("firmwares.verificationcredit", "star", "Verification credits"),
        ),
        icon="download",
    ),
    NavGroup(
        "Content",
        (
            m("blog.post", "pencil-line", "Blog posts"),
            m("blog.category", "folder", "Categories"),
            m("blog.guiderequest", "circle-question-mark", "Guide requests (review)"),
            m("tags.tag", "tag", "Tags", badge="unapproved_tags"),
            m(
                "comments.comment",
                "message-square",
                "Comments",
                badge="pending_comments",
            ),
            m("blog.posttranslation", "languages", "Post translations"),
            m("blog.categorytranslation", "languages", "Category translations"),
            m("blog.tagtranslation", "languages", "Tag translations"),
            m("tags.keywordprovider", "plug", "Keyword providers"),
            m("tags.keywordsuggestion", "lightbulb", "Keyword suggestions"),
            u("Tag manager", "sliders-horizontal", "tags:manage", "tags.view_tag"),
            u("Similar tags", "merge", "tags:duplicates_review", "tags.change_tag"),
            u(
                "Keyword review",
                "list-checks",
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
        icon="newspaper",
    ),
    NavGroup(
        "SEO",
        (
            u("SEO dashboard", "gauge", "seo:dashboard", "seo.view_issue"),
            u("Inventory", "boxes", "seo:inventory", "seo.view_issue"),
            u("Page inspector", "search", "seo:inspect", "seo.view_issue"),
            u("Issues board", "bug", "seo:issues", "seo.view_issue"),
            u("Interlinking", "link-2", "seo:links", "seo.view_linksuggestion"),
            u("Flash guides", "book-open", "seo:guides", "seo.view_issue"),
            u(
                "Change ledger",
                "clipboard-check",
                "seo:ledger",
                "seo.view_changeledger",
            ),
            u("Job queue", "timer", "seo:jobs", "seo.view_seojob"),
            u(
                "SEO control settings",
                "sliders-horizontal",
                "seo:settings",
                "seo.view_seosettings",
            ),
            m("seo.metadata", "tags", "Metadata overrides"),
            m("seo.redirect", "route", "Redirects"),
            m("seo.sitemapentry", "map", "Sitemap entries"),
            m("seo.linkableentity", "network", "Linkable entities"),
            m("seo.linksuggestion", "link-2", "Link suggestions"),
            m("seo.interlinkexclusion", "unlink", "Link exclusions"),
            m("seo.seoentity", "waypoints", "Entities"),
            m("seo.seoentityedge", "share-2", "Entity relations"),
            m("seo.seokeywordhistory", "trending-up", "Keyword history"),
            m("seo.seomodel", "database", "SEO records"),
            m("seo.crawlrun", "scan-search", "Crawl runs"),
            m("seo.pagesnapshot", "file-search", "Scanned pages"),
            m("seo.pageobservation", "eye", "Page observations"),
            m("seo.issue", "bug", "Issues", badge="open_seo_issues"),
            m(
                "seo.changeledger",
                "clipboard-check",
                "Change ledger",
                badge="seo_proposals",
            ),
            m("seo.seojob", "timer", "Jobs"),
        ),
        icon="search-check",
    ),
    NavGroup("Pages", (m("pages.page", "file-text", "Pages"),), icon="file-text"),
    NavGroup(
        "Pricing",
        (
            m("shop.order", "receipt", "Orders", badge="pending_orders"),
            m("shop.packagetier", "layers", "Packages"),
            m("shop.userpackage", "id-card", "User packages"),
            m("shop.packagequotaoverride", "gauge", "Package quota overrides"),
            m("shop.userquotaoverride", "user-cog", "Account overrides"),
            m("shop.quotaoverrideaudit", "rotate-ccw-clock", "Override audit"),
            m("shop.quotaconsumption", "chart-pie", "Quota consumption"),
            m("shop.adunlockticket", "lock-open", "Ad unlocks"),
            m("shop.featuredblogpurchase", "newspaper", "Featured blog purchases"),
            m("shop.shopsettings", "sliders-horizontal", "Pricing settings"),
        ),
        icon="shopping-bag",
    ),
    NavGroup(
        "Marketplace",
        (
            m("marketplace.listing", "store", "Listings", badge="pending_listings"),
            m("marketplace.sellerprofile", "shopping-basket", "Sellers"),
            m("marketplace.sellerverification", "shield-check", "Seller verification"),
            m("marketplace.sellerearning", "banknote", "Seller earnings"),
        ),
        icon="store",
    ),
    NavGroup(
        "Credits economy",
        (
            m("wallet.rewardrule", "gift", "Reward rules"),
            m("wallet.creditprice", "tag", "Credit prices"),
            m("wallet.wallet", "wallet", "Wallets"),
            m("wallet.ledgerentry", "receipt-text", "Ledger"),
            m("wallet.topuprequest", "credit-card", "Top-up requests"),
            m("wallet.payoutrequest", "hand-coins", "Payout requests"),
            m("wallet.escrowhold", "lock-keyhole", "Escrow holds"),
            m("wallet.rewardrefusal", "flag", "Abuse flags"),
            m("wallet.walletrepair", "wrench", "Wallet repairs"),
            m("wallet.walletsettings", "sliders-horizontal", "Credits & pricing"),
            m("referral.referral", "user-plus", "Referrals"),
            m("referral.referralcode", "link", "Referral codes"),
            m("referral.referralsettings", "settings", "Referral settings"),
        ),
        icon="coins",
    ),
    NavGroup(
        "Firmware storage",
        (
            u(
                "Storage status",
                "hard-drive",
                "storage:status",
                "storage.view_firmwarestoragelocation",
            ),
            m("storage.firmwarestoragelocation", "cloud", "Storage locations"),
        ),
        icon="hard-drive",
    ),
    NavGroup(
        "Ads",
        (
            u("Ads dashboard", "chart-line", "ads:dashboard", "ads.view_adplacement"),
            m("ads.adplacement", "layout-panel-top", "Placements"),
            m("ads.campaign", "megaphone", "Campaigns"),
            m("ads.adcreative", "image", "Creatives"),
            m("ads.placementassignment", "clipboard-list", "Placement assignments"),
            m("ads.adnetwork", "network", "Ad networks"),
            m("ads.adunit", "layout-grid", "Ad units"),
            m("ads.adevent", "mouse-pointer-click", "Ad events"),
            m("ads.rewardedadconfig", "gift", "Rewarded configs"),
            m("ads.rewardedadview", "circle-play", "Rewarded views"),
            m("ads.rewardedadgrant", "gift", "Rewarded grants"),
            m("ads.affiliatesource", "handshake", "Affiliate sources"),
            m("ads.affiliatelink", "link", "Affiliate links"),
            m("ads.affiliateprovider", "store", "Affiliate providers"),
            m("ads.affiliateproductcategory", "shapes", "Product categories"),
            m("ads.affiliateproduct", "shopping-bag", "Affiliate products"),
            m("ads.affiliateproductmatch", "git-compare", "Product matches"),
            m("ads.affiliateclick", "mouse-pointer-click", "Affiliate clicks"),
            m("ads.autoadsscanresult", "radar", "Auto-ads scans"),
            m("ads.scandiscovery", "file-search", "Scan discoveries"),
            m("ads.templateadexclusion", "ban", "Template exclusions"),
        ),
        icon="megaphone",
    ),
    NavGroup(
        "Users",
        (
            m("users.customuser", "user", "Users"),
            m("auth.group", "users", "Groups"),
            m("users.profile", "id-card", "Profiles"),
            m("users.reputation", "medal", "Reputation"),
            m("users.following", "user-plus", "Following"),
            m("users.activityfeed", "activity", "Activity feed"),
            m("users.mfadevice", "lock", "MFA devices"),
            m("users.securityquestion", "circle-question-mark", "Security questions"),
            m("users.accounterasure", "shield-check", "Account erasures"),
            m("account.emailaddress", "mail", "Email addresses"),
            m("socialaccount.socialapp", "app-window", "Social apps"),
            m("socialaccount.socialaccount", "circle-user", "Social accounts"),
            m("socialaccount.socialtoken", "ticket", "Social tokens"),
            m("authtoken.tokenproxy", "key", "API tokens"),
        ),
        icon="users",
    ),
    NavGroup(
        "Devices",
        (
            m("devices.device", "smartphone", "Devices"),
            m("devices.deviceevent", "calendar-clock", "Device events"),
            m("devices.deviceslotrequest", "circle-plus", "Slot requests"),
            m("devices.deviceslotreset", "rotate-ccw", "Slot resets"),
            m("devices.deviceresetallowance", "refresh-cw", "Reset allowances"),
        ),
        icon="smartphone",
    ),
    NavGroup(
        "Campaigns",
        (
            m("promotions.promotion", "megaphone", "Promotions"),
            m("promotions.promotiongrant", "gift", "Grants"),
            m("promotions.promotionprogress", "trending-up", "Earn progress"),
            m("promotions.promotionlog", "rotate-ccw-clock", "Campaign log"),
        ),
        icon="rocket",
    ),
    NavGroup(
        "Notifications",
        (
            u(
                "Notification dashboard",
                "bell-ring",
                "admin:users_notification_dashboard",
                "users.view_notification",
            ),
            m("users.notification", "bell", "Notifications"),
            m("users.notificationpreferences", "sliders-horizontal", "Preferences"),
            m("users.pushsubscription", "monitor-smartphone", "Push subscriptions"),
            m("users.announcement", "megaphone", "Announcements"),
        ),
        icon="bell",
    ),
    NavGroup(
        "Community",
        (
            m("gamification.badge", "medal", "Badges"),
            m("gamification.userbadge", "award", "Awarded badges"),
            m("gamification.xprule", "sliders-horizontal", "XP rules"),
            m("gamification.level", "chevrons-up", "Levels"),
            m("gamification.xptransaction", "rotate-ccw-clock", "XP ledger"),
            m("gamification.streak", "flame", "Streaks"),
            m("bounty.bounty", "target", "Bounties"),
            m(
                "bounty.bountydispute",
                "gavel",
                "Bounty reports (review)",
                badge="open_bounty_reports",
            ),
            m("bounty.bountylog", "rotate-ccw-clock", "Bounty log"),
        ),
        icon="trophy",
    ),
    NavGroup(
        "Distribution",
        (
            u(
                "Dashboard",
                "chart-line",
                "admin:distribution_channel_dashboard",
                "distribution.view_channel",
            ),
            m("distribution.channel", "radio", "Channels"),
            m("distribution.campaign", "megaphone", "Campaign presets"),
            u(
                "Content calendar",
                "calendar-days",
                "admin:distribution_channel_calendar",
                "distribution.view_sharejob",
            ),
            u(
                "Approval queue",
                "clipboard-check",
                "admin:distribution_channel_review",
                "distribution.change_sharejob",
            ),
            u(
                "Videos",
                "video",
                "admin:distribution_channel_videos",
                "distribution.view_videojob",
            ),
            u(
                "Media library",
                "image",
                "admin:distribution_channel_media_library",
                "distribution.view_videojob",
            ),
            m("distribution.sharejob", "send", "Share queue"),
            u(
                "Link analytics",
                "chart-column",
                "admin:distribution_channel_analytics",
                "distribution.view_shortlink",
            ),
            m("distribution.shortlink", "link", "Short links"),
            m("distribution.sharelog", "list", "Distribution log"),
            m("distribution.mediaasset", "image", "Generated media"),
            m(
                "distribution.distributionsettings",
                "sliders-horizontal",
                "Retention settings",
            ),
        ),
        icon="share-2",
    ),
    NavGroup(
        "Compliance",
        (
            u(
                "Consent statistics",
                "chart-column",
                "admin:consent_consentdecision_stats",
                "consent.view_consentdecision",
            ),
            m("consent.consentpolicy", "scale", "Policy versions"),
            m("consent.consentcategory", "shapes", "Categories"),
            m("consent.consentdecision", "clipboard-check", "Consent records"),
        ),
        icon="scale",
    ),
    NavGroup(
        "System",
        (
            m("site_settings.sitesettings", "settings", "Site settings"),
            m("site_settings.editorprofile", "pencil-line", "Editor profiles"),
            m("site_settings.editorimage", "image", "Editor images"),
            m("site_settings.verificationmetatag", "code", "Verification meta tags"),
            m("site_settings.verificationfile", "file-up", "Verification files"),
            m("core.appregistry", "toggle-right", "App registry"),
            m("core.searchsettings", "search", "Search and popularity"),
            m("users.userssettings", "user-cog", "Users settings"),
            m("devices.deviceconfig", "smartphone", "Device settings"),
            m("blog.blogsettings", "newspaper", "Blog settings"),
            m("comments.commentsettings", "message-square", "Comment settings"),
            m("tags.tagssettings", "tag", "Tag settings"),
            m("ads.adssettings", "megaphone", "Ads settings"),
            m("seo.seosettings", "search-check", "SEO settings"),
            m("sites.site", "globe", "Sites"),
            u("Health check", "heart-pulse", "health-ready"),
        ),
        icon="settings",
    ),
)

OVERVIEW_ICON = "layout-dashboard"
OTHER_GROUP_ICON = "ellipsis"
OTHER_ITEM_ICON = "table"

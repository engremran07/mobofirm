"""Allowed cross-app import edges, measured on 2026-11-07 (see scripts/app_imports.py).

Each key is "<importing app>-><imported app>" and the value lists the layers
(modules) of the imported app the importer may use. The graph is a ratchet: new edges
or new layers fail tests/backend/test_app_boundaries.py until the dependency is
justified and added here and to docs/04-backend/app-dependencies.md. Removing an
edge never needs a change.
"""

from __future__ import annotations

ALLOWED_EDGES: dict[str, frozenset[str]] = {
    "ads->blog": frozenset({"models"}),
    "ads->consent": frozenset({"constants", "services"}),
    "ads->core": frozenset(
        {
            "admin_base",
            "admin_dialogs",
            "admin_pages",
            "dialogs",
            "models",
            "seeding",
            "utils",
        }
    ),
    "ads->devices": frozenset({"slots"}),
    "ads->firmwares": frozenset({"crypto", "models"}),
    "ads->shop": frozenset({"(package)", "entitlements"}),
    "ads->users": frozenset({"services"}),
    "ads->wallet": frozenset({"api"}),
    "blog->ads": frozenset({"tasks"}),
    "blog->comments": frozenset({"models", "selectors"}),
    "blog->core": frozenset(
        {
            "(package)",
            "admin_base",
            "app_service",
            "dialogs",
            "editor",
            "events",
            "search",
            "seed_data",
            "seeding",
            "services",
            "signals",
            "utils",
        }
    ),
    "blog->firmwares": frozenset(
        {"(package)", "models", "seo_providers", "tool_matcher"}
    ),
    "blog->seo": frozenset(
        {
            "(package)",
            "contentsources",
            "contracts",
            "models",
            "policy",
            "providers",
            "sitemap_registry",
        }
    ),
    "blog->site_settings": frozenset({"services"}),
    "blog->tags": frozenset({"models", "selectors", "services", "tasks"}),
    "blog->users": frozenset({"models", "services"}),
    "blog->wallet": frozenset({"api"}),
    "bounty->core": frozenset(
        {"admin_base", "app_service", "dialogs", "events", "services"}
    ),
    "bounty->wallet": frozenset({"(package)", "api"}),
    "bounty->users": frozenset({"request_user"}),
    "comments->blog": frozenset({"access", "models"}),
    "comments->core": frozenset(
        {
            "admin_base",
            "admin_dialogs",
            "admin_pages",
            "dialogs",
            "editor",
            "models",
            "seed_data",
            "seeding",
            "utils",
        }
    ),
    "comments->users": frozenset({"services"}),
    "consent->core": frozenset({"admin_base", "app_service", "utils"}),
    "core->ads": frozenset({"models", "seeding"}),
    "core->blog": frozenset({"models"}),
    "core->comments": frozenset({"models"}),
    "core->devices": frozenset({"models"}),
    "core->firmwares": frozenset({"models", "seed_verification", "seeding"}),
    "core->site_settings": frozenset({"services"}),
    "core->users": frozenset({"models"}),
    "devices->ads": frozenset({"api"}),
    "devices->core": frozenset(
        {"admin_base", "admin_pages", "dialogs", "models", "services", "utils"}
    ),
    "devices->firmwares": frozenset({"models"}),
    "devices->shop": frozenset({"entitlements"}),
    "devices->users": frozenset({"services"}),
    "distribution->core": frozenset(
        {"admin_base", "admin_pages", "app_service", "events", "signals"}
    ),
    "distribution->seo": frozenset({"fetcher", "siteurls"}),
    "distribution->site_settings": frozenset({"crypto", "services"}),
    "firmwares->blog": frozenset({"models", "services"}),
    "firmwares->core": frozenset(
        {
            "(package)",
            "admin_base",
            "admin_pages",
            "ai_client",
            "app_service",
            "dialogs",
            "editor",
            "events",
            "search",
            "seed_data",
            "seeding",
            "signals",
            "throttling",
            "utils",
        }
    ),
    "firmwares->pages": frozenset({"(package)"}),
    "firmwares->seo": frozenset(
        {
            "(package)",
            "contentsources",
            "contracts",
            "models",
            "policy",
            "providers",
            "sitemap_registry",
            "siteurls",
        }
    ),
    "firmwares->site_settings": frozenset({"services"}),
    "firmwares->tags": frozenset({"services"}),
    "firmwares->users": frozenset({"decorators", "request_user", "services"}),
    "firmwares->wallet": frozenset({"api"}),
    "gamification->core": frozenset({"admin_base", "events", "services"}),
    "gamification->users": frozenset({"request_user"}),
    "gamification->wallet": frozenset({"api"}),
    "marketplace->core": frozenset(
        {"admin_base", "app_service", "decorators", "editor", "events", "search"}
    ),
    "marketplace->firmwares": frozenset({"catalog", "models"}),
    "marketplace->pages": frozenset({"widgets"}),
    "marketplace->users": frozenset({"models", "request_user", "services"}),
    "pages->blog": frozenset({"models"}),
    "pages->core": frozenset(
        {"admin_base", "app_service", "decorators", "editor", "search"}
    ),
    "pages->firmwares": frozenset({"catalog", "models"}),
    "pages->seo": frozenset(
        {
            "contentsources",
            "contracts",
            "models",
            "policy",
            "providers",
            "sitemap_registry",
        }
    ),
    "promotions->core": frozenset(
        {
            "admin_base",
            "app_service",
            "decorators",
            "dialogs",
            "editor",
            "events",
            "search",
        }
    ),
    "promotions->marketplace": frozenset({"services"}),
    "promotions->shop": frozenset({"(package)", "entitlements", "models"}),
    "promotions->users": frozenset({"models", "request_user", "services"}),
    "promotions->wallet": frozenset({"(package)"}),
    "referral->core": frozenset(
        {"admin_base", "app_service", "decorators", "events", "utils"}
    ),
    "referral->users": frozenset({"request_user"}),
    "referral->wallet": frozenset({"(package)", "api", "models"}),
    "seo->blog": frozenset({"models"}),
    "seo->core": frozenset(
        {"(package)", "admin_base", "admin_pages", "decorators", "models", "utils"}
    ),
    "seo->firmwares": frozenset({"models"}),
    "seo->site_settings": frozenset({"services"}),
    "shop->core": frozenset({"admin_base", "app_service", "decorators", "events"}),
    "shop->firmwares": frozenset({"(package)", "catalog"}),
    "shop->marketplace": frozenset({"services"}),
    "shop->users": frozenset({"request_user"}),
    "site_settings->consent": frozenset({"constants", "services"}),
    "site_settings->core": frozenset({"admin_base", "editor"}),
    "site_settings->pages": frozenset({"widgets"}),
    "storage->core": frozenset(
        {"admin_base", "admin_pages", "app_service", "decorators", "utils"}
    ),
    "storage->firmwares": frozenset({"(package)", "catalog"}),
    "tags->blog": frozenset({"(package)", "models"}),
    "tags->core": frozenset(
        {"admin_base", "admin_dialogs", "admin_pages", "dialogs", "models", "search"}
    ),
    "tags->seo": frozenset(
        {
            "contentsources",
            "contracts",
            "models",
            "policy",
            "providers",
            "sitemap_registry",
        }
    ),
    "tags->site_settings": frozenset({"services"}),
    "tags->users": frozenset({"services"}),
    "users->blog": frozenset({"models"}),
    "users->comments": frozenset({"models"}),
    "users->core": frozenset(
        {
            "admin_base",
            "app_service",
            "cache",
            "editor",
            "geo",
            "search",
            "seed_data",
            "seeding",
            "signals",
            "utils",
            "widgets",
        }
    ),
    "users->devices": frozenset({"models", "services"}),
    "users->firmwares": frozenset({"access", "catalog", "models"}),
    "users->shop": frozenset({"(package)"}),
    "users->site_settings": frozenset({"services"}),
    "users->wallet": frozenset({"(package)", "api"}),
    "wallet->core": frozenset(
        {"admin_base", "app_service", "decorators", "events", "search", "services"}
    ),
    "wallet->users": frozenset({"decorators", "request_user"}),
}

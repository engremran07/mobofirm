"""Throwaway admin audit (deleted after use): reads the dev DB, never writes."""

import json
import os
import re
import sys
from pathlib import Path

os.environ["DJANGO_SETTINGS_MODULE"] = "scripts.tmp_admin_settings"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import django

django.setup()

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.urls import NoReverseMatch, reverse

BASE = "http://127.0.0.1:8011"
OUT = Path(__file__).resolve().parent / "tmp_audit_urls.json"


def cookie_value() -> str:
    user = get_user_model().objects.get(email="admin@mobofirm.local")
    store = SessionStore()
    store[SESSION_KEY] = str(user.pk)
    store[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    store[HASH_SESSION_KEY] = user.get_session_auth_hash()
    store.save()
    return store.session_key


def urls() -> list[dict]:
    rows = []
    for model, model_admin in admin.site._registry.items():
        meta = model._meta
        key = f"{meta.app_label}_{meta.model_name}"
        label = f"{meta.app_label}.{meta.model_name}"
        try:
            rows.append({"label": label, "kind": "list", "path": reverse(f"admin:{key}_changelist")})
        except NoReverseMatch:
            continue
        try:
            rows.append({"label": label, "kind": "add", "path": reverse(f"admin:{key}_add")})
        except NoReverseMatch:
            pass
        try:
            obj = model._default_manager.order_by("-pk").first()
        except Exception:
            obj = None
        if obj is not None:
            try:
                rows.append(
                    {
                        "label": label,
                        "kind": "change",
                        "path": reverse(f"admin:{key}_change", args=[obj.pk]),
                    }
                )
            except NoReverseMatch:
                pass
    return rows


EXTRA = [
    "/admin/",
    "/admin/comments/comment/?status__exact=pending",
    "/admin/consent/consentdecision/stats/",
    "/admin/distribution/channel/analytics/",
    "/admin/distribution/channel/calendar/",
    "/admin/distribution/channel/dashboard/",
    "/admin/distribution/channel/review/",
    "/admin/users/notification/dashboard/",
    "/ads/dashboard/",
    "/ads/manage/placements/",
    "/ads/manage/suggestions/",
    "/comments/moderation/",
    "/devices/events/",
    "/seo/dashboard/",
    "/seo/guides/",
    "/seo/inspect/",
    "/seo/inventory/",
    "/seo/issues/",
    "/seo/jobs/",
    "/seo/ledger/",
    "/seo/links/",
    "/seo/settings/",
    "/storage/",
    "/tags/duplicates/review/",
    "/tags/keywords/review/",
    "/tags/manage/",
    "/firmwares/admin/requests/",
]


if __name__ == "__main__":
    rows = [
        {"label": "staff" + p.strip("/").replace("/", "_").replace("?", "_")[:40], "kind": "page", "path": p}
        for p in EXTRA
    ]
    data = {
        "cookie_name": settings.SESSION_COOKIE_NAME,
        "cookie": cookie_value(),
        "urls": rows + urls(),
    }
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(len(data["urls"]), "urls;", data["cookie_name"])

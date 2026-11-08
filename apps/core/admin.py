"""Core admin: application registry and Unfold versions of third-party admins.

Models owned by Django, allauth and DRF ship with plain ``admin.ModelAdmin`` classes;
they are re-registered here on the shared Unfold base so the whole console renders
and behaves the same. Their own options (forms, actions, search) are inherited
unchanged. This module is imported after those apps' admin modules (INSTALLED_APPS
order), so every model is registered exactly once.
"""

from __future__ import annotations

from allauth.account.admin import EmailAddressAdmin as BaseEmailAddressAdmin
from allauth.account.models import EmailAddress
from allauth.socialaccount.admin import SocialAccountAdmin as BaseSocialAccountAdmin
from allauth.socialaccount.admin import SocialTokenAdmin as BaseSocialTokenAdmin
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.contrib.sites.admin import SiteAdmin as BaseSiteAdmin
from django.contrib.sites.models import Site
from django.utils.translation import gettext_lazy as _
from rest_framework.authtoken.admin import TokenAdmin as BaseTokenAdmin
from rest_framework.authtoken.models import TokenProxy

from apps.core.admin_base import ModelAdmin, SingletonModelAdmin
from apps.core.models import AppRegistry

for _model in (Group, Site, EmailAddress, SocialAccount, SocialToken, TokenProxy):
    admin.site.unregister(_model)


@admin.register(AppRegistry)
class AppRegistryAdmin(SingletonModelAdmin):
    """Feature flags for the installed applications."""

    list_display = (
        "ads_enabled",
        "blog_enabled",
        "comments_enabled",
        "firmwares_enabled",
        "tags_enabled",
        "users_enabled",
    )
    fieldsets = (
        (
            _("Content & catalog"),
            {
                "fields": (
                    "firmwares_enabled",
                    "blog_enabled",
                    "comments_enabled",
                    "tags_enabled",
                    "pages_enabled",
                    "seo_enabled",
                )
            },
        ),
        (
            _("Commerce & storage"),
            {
                "fields": (
                    "shop_enabled",
                    "marketplace_enabled",
                    "wallet_enabled",
                    "referral_enabled",
                    "promotions_enabled",
                    "storage_enabled",
                    "ads_enabled",
                )
            },
        ),
        (
            _("Community & compliance"),
            {
                "fields": (
                    "consent_enabled",
                    "gamification_enabled",
                    "bounty_enabled",
                    "distribution_enabled",
                )
            },
        ),
        (
            _("Accounts & devices"),
            {
                "fields": (
                    "users_enabled",
                    "devices_enabled",
                    "device_identity_enabled",
                )
            },
        ),
    )


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(Site)
class SiteAdmin(BaseSiteAdmin, ModelAdmin):
    pass


@admin.register(EmailAddress)
class EmailAddressAdmin(BaseEmailAddressAdmin, ModelAdmin):
    pass


@admin.register(SocialAccount)
class SocialAccountAdmin(BaseSocialAccountAdmin, ModelAdmin):
    pass


@admin.register(SocialToken)
class SocialTokenAdmin(BaseSocialTokenAdmin, ModelAdmin):
    pass


@admin.register(TokenProxy)
class TokenAdmin(BaseTokenAdmin, ModelAdmin):
    pass

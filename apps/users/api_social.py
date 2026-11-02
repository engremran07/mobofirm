"""
REST API for Social Authentication and Social Posting Account Management.

Provides endpoints for:
- Social auth provider configuration (Google, Facebook, Microsoft, GitHub, etc.)
- Social posting account management (Facebook Pages, Twitter, Telegram, etc.)

All endpoints require staff authentication.
"""

from __future__ import annotations

import json
import logging

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from .models_social import SocialPostingAccount, SocialProviderConfig

logger = logging.getLogger(__name__)


# ==============================================================================
# Social Provider APIs
# ==============================================================================


@csrf_protect
@staff_member_required
@require_http_methods(["GET"])
def social_providers_list(request: HttpRequest) -> JsonResponse:
    """
    List all configured social authentication providers.

    Returns:
        JSON list of provider configurations with status and user counts.
    """
    try:
        providers = SocialProviderConfig.objects.all()

        data = []
        for p in providers:
            p.update_user_count()
            data.append(
                {
                    "id": str(p.id),
                    "provider": p.provider,
                    "display_name": p.display_name or p.get_provider_display(),  # type: ignore[attr-defined]
                    "status": p.status,
                    "is_enabled": p.is_enabled,
                    "has_credentials": p.has_credentials,
                    "users_count": p.users_count,
                    "scopes": p.scopes,
                    "callback_url": p.callback_url,
                    "console_url": p.provider_console_url,
                    "requires_browser_oauth": p.requires_browser_oauth,
                    "last_tested_at": p.last_tested_at.isoformat()
                    if p.last_tested_at
                    else None,
                    "last_error": p.last_error,
                    "created_at": p.created_at.isoformat(),
                }
            )

        return JsonResponse({"providers": data})
    except Exception as exc:
        logger.exception("Failed to list social providers: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


@csrf_protect
@staff_member_required
@require_http_methods(["POST"])
def social_provider_create(request: HttpRequest) -> JsonResponse:
    """
    Create or update a social authentication provider.

    Body parameters:
        - provider: Provider type (google, facebook, microsoft, github, etc.)
        - client_id: OAuth client ID
        - client_secret: OAuth client secret
        - display_name: Optional display name
        - tenant_id: For Microsoft - Azure AD tenant ID
        - team_id: For Apple - Team ID
        - key_id: For Apple - Key ID
        - scopes: List of OAuth scopes
    """
    try:
        data = json.loads(request.body) if request.body else {}

        provider = data.get("provider")
        client_id = data.get("client_id", "").strip()
        client_secret = data.get("client_secret", "").strip()

        if not provider:
            return JsonResponse({"error": "Provider type is required"}, status=400)

        if not client_id or not client_secret:
            return JsonResponse(
                {"error": "Client ID and secret are required"}, status=400
            )

        # Validate provider type
        valid_providers = dict(SocialProviderConfig.PROVIDER_CHOICES).keys()
        if provider not in valid_providers:
            return JsonResponse(
                {
                    "error": f"Invalid provider. Must be one of: {', '.join(valid_providers)}"
                },
                status=400,
            )

        # Create or update
        config, created = SocialProviderConfig.objects.get_or_create(
            provider=provider,
            defaults={
                "display_name": data.get("display_name")
                or dict(SocialProviderConfig.PROVIDER_CHOICES).get(provider),
                "status": "unconfigured",
                "scopes": data.get("scopes")
                or SocialProviderConfig.get_default_scopes(provider),
                "created_by": request.user,
            },
        )

        config.set_client_id(client_id)
        config.set_client_secret(client_secret)

        # Handle provider-specific fields
        if provider == "microsoft" and data.get("tenant_id"):
            config.tenant_id = data["tenant_id"]
        elif provider == "apple":
            if data.get("team_id"):
                config.team_id = data["team_id"]
            if data.get("key_id"):
                config.key_id = data["key_id"]

        if data.get("scopes"):
            config.scopes = data["scopes"]

        if data.get("display_name"):
            config.display_name = data["display_name"]

        config.status = "active"
        config.save()

        # Sync to allauth
        sync_success = config.sync_to_allauth()

        return JsonResponse(
            {
                "success": True,
                "created": created,
                "synced": sync_success,
                "provider": {
                    "id": str(config.id),
                    "provider": config.provider,
                    "display_name": config.display_name,
                    "status": config.status,
                    "callback_url": config.callback_url,
                },
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)
    except Exception as exc:
        logger.exception("Failed to create social provider: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


@csrf_protect
@staff_member_required
@require_http_methods(["GET", "PUT", "DELETE"])
def social_provider_detail(request: HttpRequest, provider_id: str) -> JsonResponse:
    """
    Get, update, or delete a social provider configuration.
    """
    try:
        config = SocialProviderConfig.objects.filter(id=provider_id).first()
        if not config:
            return JsonResponse({"error": "Provider not found"}, status=404)

        if request.method == "GET":
            config.update_user_count()
            return JsonResponse(
                {
                    "id": str(config.id),
                    "provider": config.provider,
                    "display_name": config.display_name
                    or config.get_provider_display(),  # type: ignore[attr-defined]
                    "status": config.status,
                    "is_enabled": config.is_enabled,
                    "has_credentials": config.has_credentials,
                    "users_count": config.users_count,
                    "scopes": config.scopes,
                    "tenant_id": config.tenant_id,
                    "team_id": config.team_id,
                    "key_id": config.key_id,
                    "callback_url": config.callback_url,
                    "console_url": config.provider_console_url,
                    "requires_browser_oauth": config.requires_browser_oauth,
                    "settings_json": config.settings_json,
                    "last_tested_at": config.last_tested_at.isoformat()
                    if config.last_tested_at
                    else None,
                    "last_error": config.last_error,
                    "created_at": config.created_at.isoformat(),
                    "updated_at": config.updated_at.isoformat(),
                }
            )

        elif request.method == "PUT":
            data = json.loads(request.body) if request.body else {}

            if data.get("client_id"):
                config.set_client_id(data["client_id"])
            if data.get("client_secret"):
                config.set_client_secret(data["client_secret"])
            if data.get("display_name"):
                config.display_name = data["display_name"]
            if data.get("scopes") is not None:
                config.scopes = data["scopes"]
            if data.get("settings_json") is not None:
                config.settings_json = data["settings_json"]
            if data.get("is_enabled") is not None:
                config.is_enabled = data["is_enabled"]
                config.status = "active" if data["is_enabled"] else "disabled"

            # Provider-specific
            if config.provider == "microsoft" and data.get("tenant_id") is not None:
                config.tenant_id = data["tenant_id"]
            elif config.provider == "apple":
                if data.get("team_id") is not None:
                    config.team_id = data["team_id"]
                if data.get("key_id") is not None:
                    config.key_id = data["key_id"]

            config.save()

            # Sync to allauth
            sync_success = config.sync_to_allauth()

            return JsonResponse(
                {
                    "success": True,
                    "synced": sync_success,
                    "provider": {
                        "id": str(config.id),
                        "status": config.status,
                    },
                }
            )

        elif request.method == "DELETE":
            if not getattr(request.user, "is_superuser", False):
                return JsonResponse(
                    {"error": "Superuser required to delete providers"}, status=403
                )

            config.delete()
            return JsonResponse({"success": True, "deleted": True})

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)
    except Exception as exc:
        logger.exception("Social provider operation failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)
    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_protect
@staff_member_required
@require_http_methods(["POST"])
def social_provider_test(request: HttpRequest, provider_id: str) -> JsonResponse:
    """
    Test a social provider's credentials.
    """
    try:
        config = SocialProviderConfig.objects.filter(id=provider_id).first()
        if not config:
            return JsonResponse({"error": "Provider not found"}, status=404)

        success, message = config.test_connection()

        return JsonResponse(
            {
                "success": success,
                "message": message,
                "tested_at": config.last_tested_at.isoformat()
                if config.last_tested_at
                else None,
            }
        )

    except Exception as exc:
        logger.exception("Social provider test failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


@csrf_protect
@staff_member_required
@require_http_methods(["POST"])
def social_provider_sync(request: HttpRequest, provider_id: str) -> JsonResponse:
    """
    Sync a social provider configuration to django-allauth.
    """
    try:
        config = SocialProviderConfig.objects.filter(id=provider_id).first()
        if not config:
            return JsonResponse({"error": "Provider not found"}, status=404)

        success = config.sync_to_allauth()

        return JsonResponse(
            {
                "success": success,
                "error": config.last_error if not success else None,
            }
        )

    except Exception as exc:
        logger.exception("Social provider sync failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


# ==============================================================================
# Social Posting Account APIs
# ==============================================================================


@csrf_protect
@staff_member_required
@require_http_methods(["GET"])
def social_posting_list(request: HttpRequest) -> JsonResponse:
    """
    List all configured social posting accounts.
    """
    try:
        accounts = SocialPostingAccount.objects.all()

        data = []
        for a in accounts:
            data.append(
                {
                    "id": str(a.id),
                    "platform": a.platform,
                    "platform_display": a.get_platform_display(),  # type: ignore[attr-defined]
                    "account_name": a.account_name,
                    "auth_type": a.auth_type,
                    "destination_type": a.destination_type,
                    "destination_id": a.destination_id,
                    "destination_name": a.destination_name,
                    "status": a.status,
                    "is_enabled": a.is_enabled,
                    "has_credentials": a.has_credentials,
                    "auto_post_enabled": a.auto_post_enabled,
                    "post_content_types": a.post_content_types,
                    "total_posts": a.total_posts,
                    "successful_posts": a.successful_posts,
                    "failed_posts": a.failed_posts,
                    "can_post_now": a.can_post_now,
                    "requires_oauth": a.requires_oauth,
                    "last_post_at": a.last_post_at.isoformat()
                    if a.last_post_at
                    else None,
                    "last_tested_at": a.last_tested_at.isoformat()
                    if a.last_tested_at
                    else None,
                    "last_error": a.last_error,
                    "created_at": a.created_at.isoformat(),
                }
            )

        return JsonResponse({"accounts": data})
    except Exception as exc:
        logger.exception("Failed to list social posting accounts: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


@csrf_protect
@staff_member_required
@require_http_methods(["POST"])
def social_posting_create(request: HttpRequest) -> JsonResponse:
    """
    Create a social posting account.

    Body parameters:
        - platform: Platform type (facebook, twitter, telegram, etc.)
        - account_name: Account display name
        - destination_type: Type of destination (page, group, channel)
        - destination_id: Platform-specific destination ID
        - bot_token: For Telegram
        - webhook_url: For Discord
        - access_token: For Medium or already authorized platforms
        - api_key/api_secret: For Twitter API v2
    """
    try:
        data = json.loads(request.body) if request.body else {}

        platform = data.get("platform")
        account_name = data.get("account_name", "").strip()

        if not platform:
            return JsonResponse({"error": "Platform is required"}, status=400)

        if not account_name:
            return JsonResponse({"error": "Account name is required"}, status=400)

        # Validate platform type
        valid_platforms = dict(SocialPostingAccount.PLATFORM_CHOICES).keys()
        if platform not in valid_platforms:
            return JsonResponse(
                {
                    "error": f"Invalid platform. Must be one of: {', '.join(valid_platforms)}"
                },
                status=400,
            )

        # Get auth info for this platform
        auth_info = SocialPostingAccount.get_auth_info(platform)
        auth_type = auth_info.get("auth_type", "oauth2")

        # Create account
        account = SocialPostingAccount.objects.create(
            platform=platform,
            account_name=account_name,
            auth_type=auth_type,
            destination_type=data.get("destination_type", "page"),
            destination_id=data.get("destination_id", "").strip(),
            destination_name=data.get("destination_name", "").strip(),
            status="unconfigured",
            created_by=request.user,
        )

        # Set credentials based on auth type
        if auth_type == "api_token" and data.get("bot_token"):
            account.set_bot_token(data["bot_token"])
            if data.get("destination_id"):
                account.status = "active"
        elif auth_type == "webhook" and data.get("webhook_url"):
            account.set_webhook_url(data["webhook_url"])
            account.status = "active"
        elif auth_type == "access_token" and data.get("access_token"):
            account.set_access_token(data["access_token"])
            account.status = "active"
        elif auth_type in ["api_key_secret", "oauth2"]:
            if data.get("api_key"):
                account.set_api_key(data["api_key"])
            if data.get("api_secret"):
                account.set_api_secret(data["api_secret"])

        account.save()

        return JsonResponse(
            {
                "success": True,
                "account": {
                    "id": str(account.id),
                    "platform": account.platform,
                    "account_name": account.account_name,
                    "status": account.status,
                    "requires_oauth": account.requires_oauth,
                },
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)
    except Exception as exc:
        logger.exception("Failed to create social posting account: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


@csrf_protect
@staff_member_required
@require_http_methods(["GET", "PUT", "DELETE"])
def social_posting_detail(request: HttpRequest, account_id: str) -> JsonResponse:
    """
    Get, update, or delete a social posting account.
    """
    try:
        account = SocialPostingAccount.objects.filter(id=account_id).first()
        if not account:
            return JsonResponse({"error": "Account not found"}, status=404)

        if request.method == "GET":
            return JsonResponse(
                {
                    "id": str(account.id),
                    "platform": account.platform,
                    "platform_display": account.get_platform_display(),  # type: ignore[attr-defined]
                    "account_name": account.account_name,
                    "auth_type": account.auth_type,
                    "destination_type": account.destination_type,
                    "destination_id": account.destination_id,
                    "destination_name": account.destination_name,
                    "destination_url": account.destination_url,
                    "status": account.status,
                    "is_enabled": account.is_enabled,
                    "has_credentials": account.has_credentials,
                    "auto_post_enabled": account.auto_post_enabled,
                    "post_content_types": account.post_content_types,
                    "post_template": account.post_template,
                    "include_image": account.include_image,
                    "include_link": account.include_link,
                    "hashtags": account.hashtags,
                    "min_post_interval_minutes": account.min_post_interval_minutes,
                    "daily_post_limit": account.daily_post_limit,
                    "posts_today": account.posts_today,
                    "total_posts": account.total_posts,
                    "successful_posts": account.successful_posts,
                    "failed_posts": account.failed_posts,
                    "consecutive_failures": account.consecutive_failures,
                    "notes": account.notes,
                    "requires_oauth": account.requires_oauth,
                    "is_token_expired": account.is_token_expired,
                    "can_post_now": account.can_post_now,
                    "token_expiry": account.token_expiry.isoformat()
                    if account.token_expiry
                    else None,
                    "last_post_at": account.last_post_at.isoformat()
                    if account.last_post_at
                    else None,
                    "last_tested_at": account.last_tested_at.isoformat()
                    if account.last_tested_at
                    else None,
                    "last_error": account.last_error,
                    "created_at": account.created_at.isoformat(),
                    "updated_at": account.updated_at.isoformat(),
                }
            )

        elif request.method == "PUT":
            data = json.loads(request.body) if request.body else {}

            # Update credentials based on auth type
            if account.auth_type == "api_token" and data.get("bot_token"):
                account.set_bot_token(data["bot_token"])
            elif account.auth_type == "webhook" and data.get("webhook_url"):
                account.set_webhook_url(data["webhook_url"])
            elif account.auth_type == "access_token" and data.get("access_token"):
                account.set_access_token(data["access_token"])
            elif account.auth_type in ["api_key_secret", "oauth2"]:
                if data.get("api_key"):
                    account.set_api_key(data["api_key"])
                if data.get("api_secret"):
                    account.set_api_secret(data["api_secret"])

            # Update other fields
            if data.get("account_name"):
                account.account_name = data["account_name"]
            if data.get("destination_type"):
                account.destination_type = data["destination_type"]
            if data.get("destination_id") is not None:
                account.destination_id = data["destination_id"]
            if data.get("destination_name") is not None:
                account.destination_name = data["destination_name"]
            if data.get("destination_url") is not None:
                account.destination_url = data["destination_url"]
            if data.get("auto_post_enabled") is not None:
                account.auto_post_enabled = data["auto_post_enabled"]
            if data.get("post_content_types"):
                account.post_content_types = data["post_content_types"]
            if data.get("post_template") is not None:
                account.post_template = data["post_template"]
            if data.get("include_image") is not None:
                account.include_image = data["include_image"]
            if data.get("include_link") is not None:
                account.include_link = data["include_link"]
            if data.get("hashtags") is not None:
                account.hashtags = data["hashtags"]
            if data.get("min_post_interval_minutes"):
                account.min_post_interval_minutes = int(
                    data["min_post_interval_minutes"]
                )
            if data.get("daily_post_limit"):
                account.daily_post_limit = int(data["daily_post_limit"])
            if data.get("notes") is not None:
                account.notes = data["notes"]
            if data.get("is_enabled") is not None:
                account.is_enabled = data["is_enabled"]
                if not data["is_enabled"]:
                    account.status = "disabled"
                elif account.has_credentials:
                    account.status = "active"

            if account.has_credentials and account.status == "unconfigured":
                account.status = "active"

            account.save()

            return JsonResponse(
                {
                    "success": True,
                    "account": {
                        "id": str(account.id),
                        "status": account.status,
                    },
                }
            )

        elif request.method == "DELETE":
            if not getattr(request.user, "is_superuser", False):
                return JsonResponse(
                    {"error": "Superuser required to delete accounts"}, status=403
                )

            account.delete()
            return JsonResponse({"success": True, "deleted": True})

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)
    except Exception as exc:
        logger.exception("Social posting account operation failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)
    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_protect
@staff_member_required
@require_http_methods(["POST"])
def social_posting_test(request: HttpRequest, account_id: str) -> JsonResponse:
    """
    Test a social posting account's credentials.
    """
    try:
        account = SocialPostingAccount.objects.filter(id=account_id).first()
        if not account:
            return JsonResponse({"error": "Account not found"}, status=404)

        success, message = account.test_connection()

        return JsonResponse(
            {
                "success": success,
                "message": message,
                "tested_at": account.last_tested_at.isoformat()
                if account.last_tested_at
                else None,
            }
        )

    except Exception as exc:
        logger.exception("Social posting account test failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


@csrf_protect
@staff_member_required
@require_http_methods(["POST"])
def social_posting_send_test(request: HttpRequest, account_id: str) -> JsonResponse:
    """
    Send a test post to verify the account is working.
    """
    try:
        account = SocialPostingAccount.objects.filter(id=account_id).first()
        if not account:
            return JsonResponse({"error": "Account not found"}, status=404)

        if account.status != "active":
            return JsonResponse(
                {"error": "Account must be active to send test post"}, status=400
            )

        # Format test post
        test_content = account.format_post(
            title="Test Post from Admin",
            excerpt="This is a test post to verify the connection is working correctly.",
            url="https://example.com/test",
            tags=["test"],
        )

        # In a real implementation, this would call the actual posting service
        # For now, we just return the formatted content
        return JsonResponse(
            {
                "success": True,
                "message": "Test post prepared (actual sending requires posting service)",
                "content": test_content,
            }
        )

    except Exception as exc:
        logger.exception("Social posting test send failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


# ==============================================================================
# Provider Info APIs (No auth required - public info)
# ==============================================================================


def social_providers_info(request: HttpRequest) -> JsonResponse:
    """
    Get public information about available social providers.
    No authentication required - used for login page.
    """
    try:
        # Get enabled providers
        providers = SocialProviderConfig.objects.filter(
            is_enabled=True, status="active"
        ).values("provider", "display_name")

        data = []
        for p in providers:
            data.append(
                {
                    "provider": p["provider"],
                    "name": p["display_name"]
                    or dict(SocialProviderConfig.PROVIDER_CHOICES).get(p["provider"]),
                }
            )

        return JsonResponse({"providers": data})
    except Exception:
        return JsonResponse({"providers": []})


def social_platforms_info(request: HttpRequest) -> JsonResponse:
    """
    Get public information about available social posting platforms.
    """
    data = []
    for key, name in SocialPostingAccount.PLATFORM_CHOICES:
        auth_info = SocialPostingAccount.get_auth_info(key)
        data.append(
            {
                "id": key,
                "name": name,
                "auth_type": auth_info.get("auth_type", "oauth2"),
                "requires_oauth": auth_info.get("requires_oauth", False),
                "setup_url": auth_info.get("setup_url", ""),
                "description": auth_info.get("description", ""),
            }
        )

    return JsonResponse({"platforms": data})

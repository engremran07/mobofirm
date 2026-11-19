"""The device policy holds on EVERY way of signing in (audit F-004).

Each test drives a real login path against the real devices contract (no mocks of
the policy): the account is in strict mode with one approved browser, so a second
browser is refused; after staff release the slot the same path succeeds.

Paths: web password form, MFA completion, allauth email-confirmation link, email
code, social callback, API password login, API Google login.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from allauth.account.middleware import AccountMiddleware
from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AnonymousUser, Permission
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.devices.models import AppPolicy, Device, DeviceConfig
from apps.devices.services import DEVICE_COOKIE_NAME, resolve_or_create_device
from apps.devices.slots import STAFF_PERMISSION, reset_slots_as_staff
from apps.users.mfa import TOTPService
from apps.users.models import CustomUser, MFADevice
from apps.users.services import device_gate
from apps.users.services.email_verification import SESSION_KEY

PASSWORD = "Strong-pass-4271!"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0 Safari/537"
TRUSTED_KEY = "t" * 43
OTHER_KEY = "o" * 43


class StrictAccountMixin:
    """An account in strict mode whose only approved browser is ``TRUSTED_KEY``."""

    user: CustomUser
    client: Client

    def build_account(self, **user_fields) -> None:
        cache.clear()
        self.user = get_user_model().objects.create_user(
            email="paths@example.com",
            username="paths",
            password=PASSWORD,
            profile_completed=True,
            **user_fields,
        )
        EmailAddress.objects.create(
            user=self.user, email="paths@example.com", primary=True, verified=True
        )
        config = DeviceConfig.get_solo()
        config.strict_new_device_login = True
        config.save(update_fields=["strict_new_device_login"])
        self.approve_browser(TRUSTED_KEY)

    def request_for(self, key: str):
        request = RequestFactory().get("/", HTTP_USER_AGENT=UA)
        request.COOKIES[DEVICE_COOKIE_NAME] = key
        request.user = self.user
        request.session = SessionStore()
        return request

    def approve_browser(self, key: str) -> None:
        device, _, _ = resolve_or_create_device(self.request_for(key), self.user)
        assert device is not None
        Device.objects.filter(pk=device.pk).update(is_trusted=True)

    def other_browser(self) -> Client:
        client = Client(HTTP_USER_AGENT=UA)
        client.cookies[DEVICE_COOKIE_NAME] = OTHER_KEY
        return client

    def release_slot(self) -> None:
        """Staff free the account's only slot, as the devices app allows."""
        staff = get_user_model().objects.create_user(
            email="staff-reset@example.com",
            username="staffreset",
            password=PASSWORD,
            is_staff=True,
        )
        staff.user_permissions.add(
            Permission.objects.get(codename=STAFF_PERMISSION.split(".")[1])
        )
        reset_slots_as_staff(self.user, actor=staff, reason="path regression")


def signed_in(client: Client) -> bool:
    return "_auth_user_id" in client.session


class PasswordAndMfaPathTests(StrictAccountMixin, TestCase):
    def setUp(self):
        self.build_account()

    def post_login(self, client: Client):
        return client.post(
            reverse("users:account_login"),
            {"login": "paths@example.com", "password": PASSWORD},
        )

    def test_password_login_is_refused_then_allowed_after_a_slot_reset(self):
        browser = self.other_browser()

        refused = self.post_login(browser)

        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, "has not been approved")
        self.assertFalse(signed_in(browser))

        self.release_slot()
        allowed = self.post_login(browser)

        self.assertEqual(allowed.status_code, 302)
        self.assertTrue(signed_in(browser))

    def enrol_totp(self) -> str:
        secret = TOTPService.generate_secret()
        device = MFADevice(
            user=self.user, device_type="totp", name="App", is_primary=True
        )
        device.set_secret(secret)
        device.save()
        return secret

    def test_mfa_completion_judges_the_device_policy_again(self):
        config = DeviceConfig.get_solo()
        config.strict_new_device_login = False
        config.save(update_fields=["strict_new_device_login"])
        secret = self.enrol_totp()
        browser = self.other_browser()
        challenged = self.post_login(browser)
        self.assertRedirects(
            challenged, reverse("users:mfa_verify"), fetch_redirect_response=False
        )

        # The policy tightens while the person is typing the code.
        config.strict_new_device_login = True
        config.save(update_fields=["strict_new_device_login"])
        refused = browser.post(
            reverse("users:mfa_verify"),
            {"code": TOTPService.generate_current_code(secret)},
        )

        self.assertRedirects(
            refused, reverse("users:account_login"), fetch_redirect_response=False
        )
        self.assertFalse(signed_in(browser))
        self.assertNotIn("pending_mfa_user_id", browser.session)

        self.release_slot()
        self.assertEqual(self.post_login(browser).status_code, 302)
        later_step = int(time.time()) + 30  # the refused code is spent (replay guard)
        allowed = browser.post(
            reverse("users:mfa_verify"),
            {"code": TOTPService.generate_current_code(secret, at_time=later_step)},
        )

        self.assertRedirects(
            allowed, reverse("users:dashboard"), fetch_redirect_response=False
        )
        self.assertTrue(signed_in(browser))


class AllauthPathTests(StrictAccountMixin, TestCase):
    """Email link, email code and social callback share ``CustomAccountAdapter``."""

    def setUp(self):
        self.build_account()

    def unverify(self) -> EmailAddress:
        EmailAddress.objects.filter(user=self.user).update(verified=False)
        return EmailAddress.objects.get(user=self.user)

    def start_unverified_login(self, client: Client) -> EmailAddress:
        """Sign in with an address that is not confirmed: allauth parks the login."""
        address = self.unverify()
        parked = client.post(
            reverse("users:account_login"),
            {"login": "paths@example.com", "password": PASSWORD},
        )
        assert parked.status_code == 302
        assert not signed_in(client)
        return address

    def confirm_by_link(self, client: Client):
        key = EmailConfirmationHMAC(self.start_unverified_login(client)).key
        return client.post(reverse("account_confirm_email", args=[key]))

    def test_email_confirmation_link_login_is_refused_then_allowed(self):
        browser = self.other_browser()

        refused = self.confirm_by_link(browser)

        self.assertRedirects(
            refused, reverse("users:account_login"), fetch_redirect_response=False
        )
        self.assertFalse(signed_in(browser))

        self.release_slot()
        allowed = self.confirm_by_link(browser)

        self.assertEqual(allowed.status_code, 302)
        self.assertTrue(signed_in(browser))

    def confirm_by_code(self, client: Client):
        address = self.start_unverified_login(client)
        session = client.session
        session[SESSION_KEY] = {
            "email_address_id": address.pk,
            "code_hash": make_password("ABCD2345"),
            "expires_at": int(time.time()) + 600,
            "attempts": 0,
        }
        session.save()
        return client.post(
            reverse("users:verify_email"), {"action": "verify", "code": "ABCD2345"}
        )

    def test_email_code_login_is_refused_then_allowed(self):
        browser = self.other_browser()

        refused = self.confirm_by_code(browser)

        self.assertRedirects(
            refused, reverse("users:account_login"), fetch_redirect_response=False
        )
        self.assertFalse(signed_in(browser))

        self.release_slot()
        allowed = self.confirm_by_code(browser)

        self.assertEqual(allowed.status_code, 302)
        self.assertTrue(signed_in(browser))

    def social_callback(self, key: str):
        """Finish a Google sign-in the way the provider callback does."""
        request = RequestFactory().get(
            "/accounts/google/login/callback/", HTTP_USER_AGENT=UA
        )
        request.COOKIES[DEVICE_COOKIE_NAME] = key
        request.session = SessionStore()
        request.user = AnonymousUser()
        SocialAccount.objects.get_or_create(
            user=self.user, provider="google", uid="google-paths"
        )
        sociallogin = SocialLogin(
            user=get_user_model()(),
            account=SocialAccount(provider="google", uid="google-paths"),
        )
        sociallogin.state = {}
        handler = MessageMiddleware(
            AccountMiddleware(lambda req: complete_social_login(req, sociallogin))
        )
        return handler(request), request

    def test_social_login_is_refused_then_allowed_after_a_slot_reset(self):
        refused, refused_request = self.social_callback(OTHER_KEY)

        self.assertEqual(refused["Location"], reverse("users:account_login"))
        self.assertNotIn("_auth_user_id", refused_request.session)

        self.release_slot()
        allowed, allowed_request = self.social_callback(OTHER_KEY)

        self.assertNotEqual(allowed["Location"], reverse("users:account_login"))
        self.assertEqual(
            str(allowed_request.session["_auth_user_id"]), str(self.user.pk)
        )

    def test_mfa_only_refusal_needs_an_enrolled_factor(self):
        config = DeviceConfig.get_solo()
        config.strict_new_device_login = False
        config.save(update_fields=["strict_new_device_login"])
        AppPolicy.objects.update_or_create(
            name="default", defaults={"mfa_requirement": "always"}
        )

        without_factor, request_a = self.social_callback(OTHER_KEY)
        self.assertEqual(without_factor["Location"], reverse("users:account_login"))
        self.assertNotIn("_auth_user_id", request_a.session)

        device = MFADevice(user=self.user, device_type="totp", name="App")
        device.set_secret(TOTPService.generate_secret())
        device.save()
        with_factor, request_b = self.social_callback(OTHER_KEY)
        self.assertNotEqual(with_factor["Location"], reverse("users:account_login"))
        self.assertIn("_auth_user_id", request_b.session)


class ApiPathTests(StrictAccountMixin, TestCase):
    def setUp(self):
        self.build_account()

    def api_client(self) -> APIClient:
        client = APIClient(HTTP_USER_AGENT=UA)
        client.cookies[DEVICE_COOKIE_NAME] = OTHER_KEY
        return client

    def test_api_password_login_is_refused_then_allowed_after_a_slot_reset(self):
        client = self.api_client()
        payload = {"identifier": "paths@example.com", "password": PASSWORD}

        refused = client.post(reverse("users:api_login"), payload, format="json")

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.data["device_policy_error"], "untrusted_new_device")
        self.assertFalse(Token.objects.filter(user=self.user).exists())

        self.release_slot()
        allowed = client.post(reverse("users:api_login"), payload, format="json")

        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(Token.objects.filter(user=self.user).exists())

    def post_google(self, client: APIClient):
        provider = Mock()
        provider.verify_token.return_value = object()

        def complete_login(request, _sociallogin):
            request.user = self.user

        with (
            patch(
                "apps.users.api_auth.get_social_adapter",
                return_value=Mock(get_provider=Mock(return_value=provider)),
            ),
            patch(
                "apps.users.api_auth.complete_social_login",
                side_effect=complete_login,
            ),
            self.settings(
                SOCIALACCOUNT_PROVIDERS={
                    "google": {"APP": {"client_id": "id", "secret": "secret"}}
                }
            ),
        ):
            return client.post(
                reverse("users:api_google_login"), {"id_token": "x"}, format="json"
            )

    def test_api_google_login_is_refused_then_allowed_after_a_slot_reset(self):
        client = self.api_client()

        refused = self.post_google(client)

        self.assertEqual(refused.status_code, 403)
        self.assertFalse(Token.objects.filter(user=self.user).exists())

        self.release_slot()
        allowed = self.post_google(client)

        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(Token.objects.filter(user=self.user).exists())


class GateContractTests(StrictAccountMixin, TestCase):
    def setUp(self):
        self.build_account()

    def test_an_installed_but_broken_contract_refuses_the_sign_in(self):
        with patch(
            "apps.users.services.device_gate.AppService.get",
            return_value=SimpleNamespace(),
        ):
            decision = device_gate.evaluate_device_policy(
                self.request_for(OTHER_KEY), self.user
            )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.unavailable)

    def test_a_failing_policy_check_refuses_the_sign_in(self):
        contract = SimpleNamespace(
            DevicePolicyError=ValueError,
            enforce_device_policy_for_login=Mock(side_effect=RuntimeError("boom")),
        )
        with patch(
            "apps.users.services.device_gate.AppService.get", return_value=contract
        ):
            decision = device_gate.evaluate_device_policy(
                self.request_for(OTHER_KEY), self.user
            )

        self.assertTrue(decision.unavailable)

    def test_without_the_devices_app_nothing_is_enforced(self):
        with patch(
            "apps.users.services.device_gate.apps.is_installed", return_value=False
        ):
            decision = device_gate.evaluate_device_policy(
                self.request_for(OTHER_KEY), self.user
            )

        self.assertTrue(decision.allowed)
        self.assertFalse(
            Device.objects.filter(user=self.user, is_trusted=False).exists()
        )

    def test_the_real_policy_reports_the_reason(self):
        decision = device_gate.evaluate_device_policy(
            self.request_for(OTHER_KEY), self.user
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "untrusted_new_device")
        self.assertIn("has not been approved", decision.message)

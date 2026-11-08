from __future__ import annotations

import re

from allauth.account.models import EmailAddress
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.users.mfa import TOTPService, provisioning_uri
from apps.users.models import CustomUser, MFADevice
from apps.users.services.mfa_qr import QR_LABEL, render_qr_svg

PASSWORD = "Str0ng-Pass-Phrase-91"


def _member() -> CustomUser:
    user = CustomUser.objects.create_user(
        username="qr_member",
        email="qr-member@example.com",
        password=PASSWORD,
        profile_completed=True,
        email_verified_at=timezone.now(),
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


class QrRenderingTests(TestCase):
    def test_svg_is_inline_accessible_and_csp_safe(self):
        svg = str(render_qr_svg("otpauth://totp/Issuer:me@example.com?secret=ABC"))

        self.assertTrue(svg.startswith("<svg "))
        self.assertIn('role="img"', svg)
        self.assertIn(f'aria-label="{QR_LABEL}"', svg)
        self.assertIn("<path", svg)
        self.assertNotIn("style=", svg)
        self.assertNotIn("<script", svg.lower())

    def test_payload_is_escaped_never_reflected_as_markup(self):
        svg = str(render_qr_svg('"><script>alert(1)</script>'))

        self.assertNotIn("<script", svg)


class MfaSetupPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _member()
        self.client.force_login(self.user)

    def test_setup_page_has_qr_hidden_key_toggle_and_confirm_form(self):
        response = self.client.get(reverse("users:mfa_setup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<svg", count=1)
        self.assertContains(response, 'role="img"')
        self.assertContains(response, "Can't scan? Show setup key")
        self.assertContains(response, 'aria-controls="mfa-setup-key"')
        self.assertRegex(
            response.content.decode(), r'id="mfa-setup-key"[^>]*\bhidden\b'
        )
        self.assertContains(response, 'x-data="mfaSetup"')
        self.assertContains(response, 'name="code"')
        self.assertContains(response, 'aria-label="Breadcrumb"')
        self.assertNotContains(response, "onclick=")

    def test_setup_key_matches_the_pending_secret_and_uri_is_not_leaked(self):
        response = self.client.get(reverse("users:mfa_setup"))
        secret = self.client.session["pending_mfa_setup_secret"]

        self.assertEqual(response.context["setup_key"].replace(" ", ""), secret)
        body = response.content.decode()
        self.assertNotIn("otpauth://", body)
        self.assertTrue(
            provisioning_uri(secret, self.user.email, issuer="Mobofirm").startswith(
                "otpauth://totp/"
            )
        )

    def test_secret_is_stable_across_reloads_until_confirmed(self):
        self.client.get(reverse("users:mfa_setup"))
        first = self.client.session["pending_mfa_setup_secret"]
        self.client.get(reverse("users:mfa_setup"))

        self.assertEqual(self.client.session["pending_mfa_setup_secret"], first)

    def test_end_to_end_enrol_challenge_and_disable(self):
        self.client.get(reverse("users:mfa_setup"))
        secret = self.client.session["pending_mfa_setup_secret"]

        confirm = self.client.post(
            reverse("users:mfa_setup"),
            {"code": TOTPService.generate_current_code(secret)},
        )
        self.assertRedirects(
            confirm, reverse("users:mfa_backup_codes"), fetch_redirect_response=False
        )
        codes_page = self.client.get(reverse("users:mfa_backup_codes"))
        codes = codes_page.context["backup_codes"]
        self.assertEqual(len(codes), 10)
        self.assertContains(codes_page, codes[0])
        self.assertRedirects(
            self.client.get(reverse("users:mfa_backup_codes")),
            reverse("users:dashboard"),
            fetch_redirect_response=False,
        )

        # A fresh browser must pass the second factor before it is signed in.
        browser = self.client_class()
        login = browser.post(
            reverse("users:account_login"),
            {"login": self.user.email, "password": PASSWORD},
        )
        self.assertRedirects(
            login, reverse("users:mfa_verify"), fetch_redirect_response=False
        )
        self.assertNotIn("_auth_user_id", browser.session)

        bad = browser.post(reverse("users:mfa_verify"), {"code": "000000"})
        self.assertRedirects(
            bad, reverse("users:mfa_verify"), fetch_redirect_response=False
        )
        self.assertNotIn("_auth_user_id", browser.session)

        device = MFADevice.objects.get(user=self.user, device_type="totp")
        device.last_used_counter = None
        device.save(update_fields=["last_used_counter"])
        good = browser.post(
            reverse("users:mfa_verify"),
            {"code": TOTPService.generate_current_code(secret)},
        )
        self.assertEqual(good.status_code, 302)
        self.assertIn("_auth_user_id", browser.session)

        # Recovery codes work once, then the authenticator can be removed with one.
        disable = browser.post(reverse("users:mfa_disable"), {"code": codes[1]})
        self.assertRedirects(
            disable, reverse("users:dashboard"), fetch_redirect_response=False
        )
        self.assertFalse(MFADevice.objects.filter(user=self.user).exists())

    def test_setup_is_blocked_after_too_many_wrong_codes(self):
        self.client.get(reverse("users:mfa_setup"))
        secret = self.client.session["pending_mfa_setup_secret"]
        for _ in range(12):
            self.client.post(reverse("users:mfa_setup"), {"code": "000000"})

        response = self.client.post(
            reverse("users:mfa_setup"),
            {"code": TOTPService.generate_current_code(secret)},
            follow=True,
        )

        self.assertFalse(MFADevice.objects.filter(user=self.user).exists())
        self.assertTrue(
            re.search(
                "Too many verification attempts",
                " ".join(str(m) for m in response.context["messages"]),
            )
        )

    def test_anonymous_visitors_are_sent_to_login(self):
        self.client.logout()

        response = self.client.get(reverse("users:mfa_setup"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("users:account_login"), response["Location"])

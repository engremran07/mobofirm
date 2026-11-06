from __future__ import annotations

import smtplib
from typing import Any
from unittest import mock

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.locmem import EmailBackend as LocmemBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.site_settings.admin import SiteSettingsAdmin
from apps.site_settings.forms import SiteSettingsAdminForm
from apps.site_settings.models import SiteSettings
from apps.site_settings.services import clear_site_settings_cache, get_site_settings

PLAINTEXT = "plain-secret-value-123"
STAFF_CREDENTIAL = "Pa55-word-for-tests"


def management_forms() -> dict[str, str]:
    data: dict[str, str] = {}
    for prefix in ("meta_tags", "verification_files"):
        data.update(
            {
                f"{prefix}-TOTAL_FORMS": "0",
                f"{prefix}-INITIAL_FORMS": "0",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
            }
        )
    return data


def minimal_post(**extra: str) -> dict[str, str]:
    return {
        "email_mode": "environment",
        "smtp_port": "587",
        "smtp_use_tls": "on",
        "email_verification_code_length": "6",
        "email_verification_code_type": "alphanumeric",
        **management_forms(),
        **extra,
    }


class SiteSettingsAdminFormTests(TestCase):
    def form(self, instance: SiteSettings, **extra: str) -> SiteSettingsAdminForm:
        data = {
            key: value for key, value in minimal_post(**extra).items() if "-" not in key
        }
        return SiteSettingsAdminForm(data=data, instance=instance)

    def test_a_typed_secret_is_stored_encrypted(self):
        row = SiteSettings.get_solo()
        form = self.form(row, recaptcha_private_key=PLAINTEXT)

        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        raw = SiteSettings.objects.values_list(
            "recaptcha_private_key_encrypted", flat=True
        ).get()
        self.assertTrue(raw)
        self.assertNotIn(PLAINTEXT, raw)
        self.assertEqual(
            SiteSettings.get_solo().get_secret("recaptcha_private_key"), PLAINTEXT
        )

    def test_an_empty_secret_input_keeps_the_stored_value(self):
        row = SiteSettings.get_solo()
        row.set_secret("smtp_password", PLAINTEXT)
        row.save()

        form = self.form(SiteSettings.get_solo(), smtp_password="")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertEqual(SiteSettings.get_solo().get_secret("smtp_password"), PLAINTEXT)

    def test_the_clear_checkbox_removes_the_stored_value(self):
        row = SiteSettings.get_solo()
        row.set_secret("smtp_password", PLAINTEXT)
        row.save()

        form = self.form(SiteSettings.get_solo(), clear_smtp_password="on")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertFalse(SiteSettings.get_solo().has_secret("smtp_password"))

    def test_the_secret_widget_never_renders_the_stored_value(self):
        row = SiteSettings.get_solo()
        row.set_secret("smtp_password", PLAINTEXT)
        row.save()

        html = str(
            SiteSettingsAdminForm(instance=SiteSettings.get_solo())["smtp_password"]
        )

        self.assertNotIn(PLAINTEXT, html)
        self.assertNotIn(SiteSettings.get_solo().smtp_password_encrypted, html)

    def test_gmail_mode_accepts_a_password_typed_in_the_same_submission(self):
        form = self.form(
            SiteSettings.get_solo(),
            email_mode="gmail",
            gmail_username="ops@example.org",
            gmail_app_password="abcd efgh ijkl mnop",
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.changed_secret_names(), ["Gmail app password"])


class SiteSettingsAdminPageTests(TestCase):
    def setUp(self) -> None:
        self.staff = get_user_model().objects.create_superuser(
            username="root", email="root@example.org", password=STAFF_CREDENTIAL
        )
        self.client.force_login(self.staff)
        self.row = SiteSettings.get_solo()
        self.change_url = reverse(
            "admin:site_settings_sitesettings_change", args=[self.row.pk]
        )

    def test_the_settings_page_and_both_inline_models_load(self):
        for name in (
            "admin:site_settings_sitesettings_changelist",
            "admin:site_settings_verificationmetatag_changelist",
            "admin:site_settings_verificationfile_changelist",
            "admin:site_settings_verificationmetatag_add",
            "admin:site_settings_verificationfile_add",
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name), follow=True)
                self.assertEqual(response.status_code, 200)

        response = self.client.get(self.change_url)
        self.assertEqual(response.status_code, 200)
        for tab in ("Branding", "Email", "Captcha", "Download gate", "Analytics"):
            self.assertContains(response, tab)
        self.assertContains(response, "Save and send test email")

    def test_saving_stores_secrets_encrypted_and_never_renders_them_back(self):
        with self.assertLogs("apps.site_settings.audit", level="INFO") as logs:
            response = self.client.post(
                self.change_url,
                minimal_post(
                    site_name="Admin Edited",
                    smtp_password=PLAINTEXT,
                    recaptcha_private_key=PLAINTEXT + "-r",
                ),
            )

        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        row = SiteSettings.get_solo()
        self.assertEqual(row.site_name, "Admin Edited")
        self.assertEqual(row.get_secret("smtp_password"), PLAINTEXT)
        self.assertEqual(get_site_settings().site_name, "Admin Edited")
        audit = "\n".join(logs.output)
        self.assertIn("site_name", audit)
        self.assertNotIn(PLAINTEXT, audit)

        page = self.client.get(self.change_url).content.decode()
        self.assertNotIn(PLAINTEXT, page)
        self.assertNotIn(row.smtp_password_encrypted, page)
        self.assertIn("stored (encrypted)", page)

    def test_an_invalid_submission_is_rejected_without_saving(self):
        response = self.client.post(
            self.change_url, minimal_post(primary_color="#ffff00", site_name="Nope")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SiteSettings.get_solo().site_name, "")

    def test_staff_without_the_change_permission_cannot_edit(self):
        viewer = get_user_model().objects.create_user(
            username="viewer",
            email="viewer@example.org",
            password=STAFF_CREDENTIAL,
            is_staff=True,
        )
        self.client.force_login(viewer)

        self.assertEqual(self.client.get(self.change_url).status_code, 403)
        self.assertEqual(
            self.client.post(self.change_url, minimal_post(site_name="X")).status_code,
            403,
        )

    def test_the_admin_header_follows_the_runtime_site_header(self):
        SiteSettings.objects.all().delete()
        row = SiteSettings.get_solo()
        row.site_header = "Ops Console"
        row.save()

        self.assertContains(self.client.get(reverse("admin:index")), "Ops Console")

    def test_previews_use_the_shared_helper_without_inline_styles(self):
        model_admin = SiteSettingsAdmin(SiteSettings, mock.Mock())
        SiteSettings.objects.update(logo="branding/logo.png")
        row = SiteSettings.get_solo()

        html = str(model_admin.logo_preview(row))

        self.assertIn("/media/branding/logo.png", html)
        self.assertNotIn("style=", html)
        self.assertEqual(model_admin.dark_logo_preview(row), "No image uploaded.")


class TestEmailActionTests(TestCase):
    def setUp(self) -> None:
        self.model_admin = SiteSettingsAdmin(SiteSettings, mock.Mock())
        self.row = SiteSettings.get_solo()

    def request(self, email: str) -> HttpRequest:
        request = RequestFactory().post("/admin/")
        request.user = get_user_model()(
            pk=1, email=email, is_active=True, is_superuser=True
        )
        request.session = SessionStore()
        MessageMiddleware(lambda _: HttpResponse()).process_request(request)
        return request

    @staticmethod
    def levels(request: HttpRequest) -> list[int]:
        return [message.level for message in messages.get_messages(request)]

    def test_the_action_sends_to_the_acting_staff_user(self):
        request = self.request("staff@example.org")

        self.model_admin.send_test_email(request, self.row)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["staff@example.org"])
        self.assertEqual(self.levels(request), [messages.SUCCESS])

    def test_a_delivery_failure_is_reported_without_leaking_details(self):
        request = self.request("staff@example.org")

        with mock.patch(
            "django.core.mail.EmailMessage.send",
            side_effect=smtplib.SMTPAuthenticationError(535, b"bad credentials xyz"),
        ):
            self.model_admin.send_test_email(request, self.row)

        self.assertEqual(self.levels(request), [messages.ERROR])
        text = " ".join(str(m) for m in messages.get_messages(request))
        self.assertNotIn("xyz", text)

    def test_a_user_without_an_address_gets_a_clear_error(self):
        request = self.request("")

        self.model_admin.send_test_email(request, self.row)

        self.assertEqual(mail.outbox, [])
        self.assertEqual(self.levels(request), [messages.ERROR])


class RuntimeEmailBackendTests(TestCase):
    def backend(self) -> Any:
        from apps.core.email_backends.runtime import RuntimeEmailBackend

        return RuntimeEmailBackend()

    def test_gmail_mode_uses_the_stored_app_password(self):
        row = SiteSettings.get_solo()
        row.email_mode = SiteSettings.EmailMode.GMAIL
        row.gmail_username = "ops@example.org"
        row.set_secret("gmail_app_password", "app-pass")
        row.save()

        inner = self.backend()._backend

        self.assertIsInstance(inner, SmtpBackend)
        self.assertEqual(inner.host, "smtp.gmail.com")
        self.assertEqual(inner.username, "ops@example.org")
        self.assertEqual(inner.password, "app-pass")
        self.assertTrue(inner.use_tls)

    def test_smtp_mode_uses_the_stored_server_settings(self):
        row = SiteSettings.get_solo()
        row.email_mode = SiteSettings.EmailMode.SMTP
        row.smtp_host = "smtp.example.org"
        row.smtp_port = 2525
        row.smtp_use_tls = False
        row.save()

        inner = self.backend()._backend

        self.assertEqual(
            (inner.host, inner.port, inner.use_tls), ("smtp.example.org", 2525, False)
        )

    def test_a_half_configured_mode_fails_loudly(self):
        SiteSettings.get_solo()
        SiteSettings.objects.update(email_mode="gmail")
        clear_site_settings_cache()

        with self.assertRaises(ImproperlyConfigured):
            self.backend()

    @override_settings(
        GMAIL_ENABLED=True,
        GMAIL_USERNAME="env@example.org",
        GMAIL_APP_PASSWORD="env-pass",
        GMAIL_FROM_EMAIL="",
    )
    def test_environment_mode_falls_back_to_the_gmail_environment(self):
        inner = self.backend()._backend

        self.assertIsInstance(inner, SmtpBackend)
        self.assertEqual(inner.username, "env@example.org")
        self.assertEqual(inner.password, "env-pass")

    @override_settings(GMAIL_ENABLED=False)
    def test_environment_mode_without_smtp_uses_the_default_backend(self):
        inner = self.backend()._backend

        self.assertFalse(isinstance(inner, SmtpBackend))

    @override_settings(DEFAULT_FROM_EMAIL="noreply@localhost")
    def test_the_stored_sender_replaces_the_default_sender(self):
        row = SiteSettings.get_solo()
        row.email_mode = SiteSettings.EmailMode.SMTP
        row.smtp_host = "smtp.example.org"
        row.from_email = "hello@example.org"
        row.save()
        backend = self.backend()
        backend._backend = LocmemBackend()
        message = mail.EmailMessage("s", "b", "noreply@localhost", ["a@example.org"])

        backend.send_messages([message])

        self.assertEqual(message.from_email, "hello@example.org")
        self.assertEqual(len(mail.outbox), 1)

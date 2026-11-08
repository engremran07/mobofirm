from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sites.models import Site
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.smtp import EmailBackend as SmtpEmailBackend
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings


class EnsureSystemUserTests(TestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command("ensure_system_user", *args, stdout=out)
        return out.getvalue()

    def test_creates_a_non_login_automation_user_once(self):
        first = self.run_command()
        second = self.run_command()

        user = get_user_model().objects.get(username="mobofirm-automation")
        self.assertIn("Created", first)
        self.assertIn("Verified", second)
        self.assertEqual(get_user_model().objects.filter(pk=user.pk).count(), 1)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertTrue(user.can_create_blog_posts)

    def test_repairs_a_tampered_account_without_creating_a_second_one(self):
        self.run_command()
        user = get_user_model().objects.get(username="mobofirm-automation")
        user.set_password("a-login-password-1!")
        user.is_staff = True
        user.save()

        self.run_command()

        user.refresh_from_db()
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertEqual(
            get_user_model().objects.filter(username="mobofirm-automation").count(), 1
        )

    def test_username_is_configurable(self):
        self.run_command("--username", "custom-bot")

        self.assertTrue(get_user_model().objects.filter(username="custom-bot").exists())

    def test_blank_username_is_rejected(self):
        with self.assertRaises(CommandError):
            self.run_command("--username", "  ")

    def test_matches_the_account_the_blog_automation_resolves(self):
        from apps.firmwares.blog_automation import get_automation_author

        self.run_command()

        author = get_automation_author()
        self.assertEqual(author.username, "mobofirm-automation")
        self.assertFalse(author.has_usable_password())


@override_settings(SITE_URL="https://mobofirm.example", SITE_NAME="Mobofirm Test")
class BootstrapSiteTests(TestCase):
    def run_command(self):
        out, err = StringIO(), StringIO()
        call_command("bootstrap_site", stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_prepares_site_registry_groups_and_system_user(self):
        from apps.core.models import AppRegistry

        AppRegistry.objects.all().delete()
        Site.objects.filter(pk=1).update(domain="example.com", name="example.com")

        out, _ = self.run_command()

        site = Site.objects.get(pk=1)
        self.assertEqual(site.domain, "mobofirm.example")
        self.assertEqual(site.name, "Mobofirm Test")
        self.assertEqual(AppRegistry.objects.count(), 1)
        self.assertTrue(
            Group.objects.filter(
                name__in=["Editors", "Moderators", "Firmware reviewers"]
            ).count()
            == 3
        )
        editors = Group.objects.get(name="Editors")
        self.assertTrue(editors.permissions.filter(codename="change_post").exists())
        self.assertTrue(
            get_user_model().objects.filter(username="mobofirm-automation").exists()
        )
        self.assertIn("Site bootstrap complete", out)

    def test_is_idempotent(self):
        self.run_command()
        counts = (
            Site.objects.count(),
            Group.objects.count(),
            get_user_model().objects.count(),
            Group.objects.get(name="Editors").permissions.count(),
        )

        self.run_command()

        self.assertEqual(
            counts,
            (
                Site.objects.count(),
                Group.objects.count(),
                get_user_model().objects.count(),
                Group.objects.get(name="Editors").permissions.count(),
            ),
        )

    def test_missing_permissions_are_reported_not_silently_skipped(self):
        from apps.core.management.commands import bootstrap_site

        original = bootstrap_site.GROUP_PERMISSIONS
        bootstrap_site.GROUP_PERMISSIONS = {"Ghost": (("blog", "no_such_permission"),)}
        try:
            _, err = self.run_command()
        finally:
            bootstrap_site.GROUP_PERMISSIONS = original

        self.assertIn("blog.no_such_permission", err)


class DatabaseMaintenanceTests(TestCase):
    def test_failures_exit_non_zero_instead_of_reporting_success(self):
        # The test database is SQLite, so the PostgreSQL-only version probe fails.
        with self.assertRaises(CommandError):
            call_command("db_maintenance", "--vacuum", stdout=StringIO())


class QueueBackendTests(SimpleTestCase):
    def test_unknown_backend_is_a_configuration_error_not_a_silent_sync_fallback(
        self,
    ):
        from apps.core.infrastructure import QueueService

        with self.assertRaises(ImproperlyConfigured):
            QueueService(backend="no-such-backend")

    @override_settings(QUEUE_BACKEND="sync")
    def test_configured_backend_is_used(self):
        from apps.core.infrastructure import QueueService

        self.assertFalse(QueueService().is_async)


class MailerConfigurationTests(SimpleTestCase):
    def test_smtp_options_reach_the_backend_in_django_6_1_format(self):
        from django.core.mail import mailers

        with override_settings(
            MAILERS={
                "default": {
                    "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
                    "OPTIONS": {
                        "host": "smtp.example.org",
                        "port": 2525,
                        "username": "u",
                        "password": "p",
                        "use_tls": True,
                    },
                }
            }
        ):
            connection = mailers["default"]

        assert isinstance(connection, SmtpEmailBackend)
        self.assertEqual(connection.host, "smtp.example.org")
        self.assertEqual(connection.port, 2525)

    def test_gmail_backend_falls_back_to_the_configured_default_backend(self):
        from apps.core.email_backends.gmail import GmailBackend

        with override_settings(
            GMAIL_ENABLED=False,
            DEFAULT_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ):
            backend = GmailBackend()
            sent = backend.send_messages(
                [mail.EmailMessage("s", "b", "from@example.com", ["to@example.com"])]
            )

        self.assertEqual(sent, 1)

    def test_gmail_backend_rejects_an_unimportable_fallback(self):
        from apps.core.email_backends.gmail import GmailBackend

        with (
            override_settings(
                GMAIL_ENABLED=False, DEFAULT_EMAIL_BACKEND="no.such.Backend"
            ),
            self.assertRaises(ImproperlyConfigured),
        ):
            GmailBackend()


class NotificationServiceTests(TestCase):
    def test_core_notification_service_stores_a_real_notification(self):
        from apps.core.services import notification_service
        from apps.users.models import Notification

        user = get_user_model().objects.create_user(
            email="core-notify@example.com", username="corenotify"
        )

        sent = notification_service.send(user.pk, "Title", "Body", "warning")
        bulk = notification_service.send_bulk([user.pk], "Bulk", "Body")

        self.assertTrue(sent)
        self.assertEqual(bulk, 1)
        self.assertEqual(
            Notification.objects.filter(recipient=user, priority="warning").count(), 1
        )
        self.assertFalse(notification_service.send(999999, "x", "y"))


class CelerySchedulingTests(SimpleTestCase):
    # Importing the task modules lets Celery's Django fixup run model checks.
    databases = "__all__"

    def test_scheduled_tasks_exist_in_the_celery_registry(self):
        from django.conf import settings

        from app.celery import app

        app.loader.import_default_modules()
        schedule = settings.CELERY_BEAT_SCHEDULE
        for key in (
            "core-cleanup-old-data",
            "devices-purge-old-events",
            "devices-expire-old-fingerprints",
            "devices-cleanup-behavior-insights",
        ):
            with self.subTest(entry=key):
                self.assertIn(schedule[key]["task"], app.tasks)

    def test_celery_defaults_to_production_settings(self):
        import os
        import subprocess
        import sys

        env = {
            key: value
            for key, value in os.environ.items()
            if key != "DJANGO_SETTINGS_MODULE"
        }
        code = "import app.celery, os; print(os.environ['DJANGO_SETTINGS_MODULE'])"
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

        self.assertEqual(result.stdout.strip(), "app.settings.production")

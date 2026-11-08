from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.cache import cache
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

ROOT = Path(__file__).resolve().parents[3]

PRODUCTION_ENV = {
    "DJANGO_SETTINGS_MODULE": "app.settings.production",
    "DJANGO_SECRET_KEY": "p" * 25 + "q" * 25 + "rstuvwxyz",
    "DB_PASSWORD": "not-a-real-password",
    "DJANGO_ALLOWED_HOSTS": "example.org",
    "SITE_URL": "https://example.org",
    "MFA_ENCRYPTION_KEY": "m" * 40,
    "SOCIAL_CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "CACHE_URL": "redis://redis:6379/1",
    "CELERY_BROKER_URL": "redis://redis:6379/0",
    "EMAIL_HOST": "smtp.example.org",
    "EMAIL_HOST_USER": "mailer",
    "EMAIL_HOST_PASSWORD": "not-a-real-mail-password",
    "DEFAULT_FROM_EMAIL": "Mobofirm <noreply@example.org>",
    "GMAIL_ENABLED": "false",
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "FORCE_HTTPS": "",
    "CSP_AD_HOSTS": "",
    "LOG_DIR": "",
}

PROBE = """
import json
from django.conf import settings
from django.core.mail import mailers
connection = mailers["default"]
print(json.dumps({
    "debug": settings.DEBUG,
    "ssl_redirect": settings.SECURE_SSL_REDIRECT,
    "redirect_exempt": settings.SECURE_REDIRECT_EXEMPT,
    "hsts": settings.SECURE_HSTS_SECONDS,
    "proxy_header": list(settings.SECURE_PROXY_SSL_HEADER),
    "csrf_origins": settings.CSRF_TRUSTED_ORIGINS,
    "cookies_secure": [settings.SESSION_COOKIE_SECURE, settings.CSRF_COOKIE_SECURE],
    "cache": settings.CACHES["default"]["BACKEND"],
    "queue": settings.QUEUE_BACKEND,
    "proxies": [settings.TRUSTED_PROXY_COUNT, settings.NUM_PROXIES],
    "mail_host": connection.host,
    "mail_port": connection.port,
    "mail_user": connection.username,
    "mail_tls": connection.use_tls,
    "force_https": settings.FORCE_HTTPS,
    "protocol": settings.ACCOUNT_DEFAULT_HTTP_PROTOCOL,
}))
"""


def run_settings(overrides: dict[str, str], code: str = PROBE):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DJANGO_", "DB_", "EMAIL_", "GMAIL_"))
    }
    env.update(PRODUCTION_ENV)
    env.update(overrides)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"import django; django.setup()\n{code}",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


class ProductionSettingsTests(SimpleTestCase):
    def test_production_configuration_is_secure_and_email_options_are_honoured(self):
        result = run_settings({})

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertFalse(data["debug"])
        self.assertTrue(data["ssl_redirect"])
        self.assertEqual(data["redirect_exempt"], [r"^health/"])
        self.assertGreaterEqual(data["hsts"], 31_536_000)
        self.assertEqual(data["proxy_header"], ["HTTP_X_FORWARDED_PROTO", "https"])
        self.assertEqual(data["csrf_origins"], ["https://example.org"])
        self.assertEqual(data["cookies_secure"], [True, True])
        self.assertTrue(data["cache"].endswith("RedisCache"))
        self.assertEqual(data["queue"], "celery")
        self.assertEqual(data["proxies"], [1, 1])
        self.assertEqual(data["mail_host"], "smtp.example.org")
        self.assertEqual(data["mail_port"], 587)
        self.assertEqual(data["mail_user"], "mailer")
        self.assertTrue(data["mail_tls"])
        self.assertTrue(data["force_https"])
        self.assertEqual(data["protocol"], "https")

    def test_missing_required_production_values_fail_startup(self):
        cases = {
            "CACHE_URL": "shared Redis cache",
            "MFA_ENCRYPTION_KEY": "MFA_ENCRYPTION_KEY",
            "SOCIAL_CREDENTIAL_ENCRYPTION_KEY": "Fernet key",
            "SITE_URL": "SITE_URL",
            "CELERY_BROKER_URL": "CELERY_BROKER_URL",
            "EMAIL_HOST": "EMAIL_HOST",
            "DJANGO_ALLOWED_HOSTS": "DJANGO_ALLOWED_HOSTS",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                result = run_settings({name: ""})

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ImproperlyConfigured", result.stderr)
                self.assertIn(expected, result.stderr)

    def test_console_mail_backend_and_short_secret_keys_are_rejected(self):
        console = run_settings(
            {"EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"}
        )
        short_key = run_settings({"DJANGO_SECRET_KEY": "too-short"})

        self.assertIn("real email backend", console.stderr)
        self.assertIn("DJANGO_SECRET_KEY", short_key.stderr)

    def test_deploy_checks_report_no_security_warnings(self):
        result = run_settings(
            {},
            "from django.core.management import call_command\n"
            "call_command('check', '--deploy', '--fail-level', 'WARNING')\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


class HealthEndpointTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_liveness_and_readiness_report_status_and_release_version(self):
        from app.version import app_version

        live = self.client.get(reverse("health-live"))
        ready = self.client.get(reverse("health-ready"))

        self.assertEqual(live.json(), {"status": "ok", "version": app_version()})
        payload = ready.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["version"], app_version())
        self.assertEqual(set(payload["build"]), {"git_sha", "date"})
        self.assertIn("no-cache", ready["Cache-Control"])

    def test_probes_accept_head_but_not_writes(self):
        self.assertEqual(self.client.head(reverse("health-ready")).status_code, 200)
        self.assertEqual(self.client.post(reverse("health-ready")).status_code, 405)

    def test_database_failure_is_unavailable_without_leaking_details(self):
        with patch("app.health.connections") as connections:
            connections.__getitem__.return_value.cursor.side_effect = DatabaseError(
                "password authentication failed for user mobofirm_app"
            )
            response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotIn(b"password", response.content)

    def test_cache_failure_makes_the_instance_unready(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        with patch("app.health.cache") as broken_cache:
            broken_cache.set.side_effect = RedisConnectionError("redis://secret-host")
            response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotIn(b"secret-host", response.content)

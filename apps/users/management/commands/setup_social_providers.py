"""
Management command to check social authentication provider status.

Shows which OAuth providers are configured and ready to use.
To add real credentials, use Django Admin: /admin/socialaccount/socialapp/

Usage: python manage.py setup_social_providers
"""

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check social authentication provider configuration status"

    def handle(self, *args, **options):
        site = Site.objects.get_current()

        expected_providers = ["google", "facebook", "microsoft", "github"]

        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("Social Authentication Status"))
        self.stdout.write("=" * 50)

        configured = []
        missing = []

        for provider_id in expected_providers:
            app = SocialApp.objects.filter(provider=provider_id).first()

            if app and app.client_id and not app.client_id.startswith("PLACEHOLDER"):
                # Has real credentials
                app.sites.add(site)  # Ensure site is linked
                configured.append(provider_id.capitalize())
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ {provider_id.capitalize()}: Configured")
                )
            else:
                missing.append(provider_id.capitalize())
                self.stdout.write(
                    self.style.WARNING(
                        f"  ✗ {provider_id.capitalize()}: Not configured"
                    )
                )

        self.stdout.write("")

        if configured:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ {len(configured)} provider(s) ready: {', '.join(configured)}"
                )
            )
            self.stdout.write(
                "  Social login buttons will appear on login/signup pages."
            )

        if missing:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"⚠ {len(missing)} provider(s) need configuration: {', '.join(missing)}"
                )
            )
            self.stdout.write("")
            self.stdout.write(
                "  To enable social login, add OAuth credentials in Django Admin:"
            )
            self.stdout.write("  → /admin/socialaccount/socialapp/")
            self.stdout.write("")
            self.stdout.write("  Get OAuth credentials from:")
            self.stdout.write(
                "  • Google:    https://console.cloud.google.com/apis/credentials"
            )
            self.stdout.write("  • Facebook:  https://developers.facebook.com/apps/")
            self.stdout.write(
                "  • Microsoft: https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps"
            )
            self.stdout.write("  • GitHub:    https://github.com/settings/developers")

        if not missing:
            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS("✓ All social providers are configured!")
            )

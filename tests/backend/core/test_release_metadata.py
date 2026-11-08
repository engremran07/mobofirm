from django.test import TestCase
from django.urls import reverse

from app.version import app_version


class ReleaseMetadataTests(TestCase):
    def test_public_shell_renders_the_installed_release_version(self):
        response = self.client.get(reverse("home"))
        release_version = app_version()

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<meta name="application-version" content="{release_version}">',
            html=True,
        )
        self.assertContains(response, f"Version {release_version}")

    def test_admin_shell_renders_the_same_release_version(self):
        from django.contrib.auth import get_user_model

        staff_user = get_user_model().objects.create_superuser(
            username="release-admin",
            email="release-admin@example.com",
            password="test-password",
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"v{app_version()}")

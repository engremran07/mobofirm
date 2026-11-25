from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.devices.models import Device
from apps.devices.services import hash_browser_key


class DeviceApprovalSecurityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="device_approval_user",
            email="device-approval@example.com",
            password="test-password",
        )
        self.browser_key = "x" * 43
        self.device = Device.objects.create(
            user=self.user,
            browser_key_hash=hash_browser_key(self.browser_key),
            is_trusted=False,
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.client.cookies["mobofirm_device"] = self.browser_key
        session = self.client.session
        session["device_registration_prompt"] = {"device_name": "Windows device"}
        session.save()

    def test_approval_is_not_performed_by_get(self):
        response = self.client.get(reverse("users:device_approval_needed"))

        self.assertRedirects(
            response,
            reverse("devices:my_devices"),
            fetch_redirect_response=False,
        )
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_trusted)

    def test_browser_only_approval_is_rejected(self):
        page = self.client.get(reverse("devices:my_devices"))

        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Approve this browser")
        self.assertNotContains(page, "Register this browser only")
        response = self.client.post(
            reverse("devices:acknowledge_new_device"),
            {
                "decision": "approve",
                "csrfmiddlewaretoken": self.client.cookies["csrftoken"].value,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_trusted)

    def test_dismissing_prompt_requires_csrf_and_does_not_approve(self):
        self.client.get(reverse("devices:my_devices"))
        csrf = self.client.cookies["csrftoken"].value
        response = self.client.post(
            reverse("devices:acknowledge_new_device"),
            {"decision": "later", "csrfmiddlewaretoken": csrf},
        )

        self.assertRedirects(
            response,
            reverse("users:dashboard"),
            fetch_redirect_response=False,
        )
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_trusted)

    def test_approval_post_requires_csrf(self):
        response = self.client.post(
            reverse("devices:acknowledge_new_device"),
            {"decision": "approve"},
        )

        self.assertEqual(response.status_code, 403)
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_trusted)

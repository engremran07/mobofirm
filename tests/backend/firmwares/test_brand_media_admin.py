from __future__ import annotations

from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.firmwares.models import Brand, Model


def image_upload(filename: str) -> SimpleUploadedFile:
    image = BytesIO()
    Image.new("RGB", (1, 1)).save(image, format="PNG")
    return SimpleUploadedFile(
        filename,
        image.getvalue(),
        content_type="image/png",
    )


class BrandMediaAdminTests(TestCase):
    """Verify permission-aware brand controls and image uploads in the admin."""

    def setUp(self):
        """Create a brand and authenticate a staff user for admin requests."""
        self.brand = Brand.objects.create(name="Upload Brand", slug="upload-brand")
        staff = get_user_model().objects.create_superuser(
            username="brand-admin",
            email="brand-admin@example.test",
            password="Pass-12345!",
        )
        self.client.force_login(staff)
        self.url = reverse("admin:firmwares_brand_change", args=[self.brand.pk])

    def test_change_form_has_upload_input_and_prefilled_add_model_action(self):
        """Show upload controls and the prefilled action for an empty brand."""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="file"')
        self.assertContains(response, 'name="logo_upload"')
        self.assertContains(
            response,
            f'href="/admin/firmwares/model/add/?brand={self.brand.pk}"',
        )
        self.assertNotContains(response, "Add another Model")

    def test_existing_models_do_not_get_the_empty_brand_action(self):
        """Hide the add-model action when the brand already has a model."""
        Model.objects.create(
            brand=self.brand,
            name="Existing Phone",
            slug="existing-phone",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Add model")

    def test_staff_without_model_add_permission_does_not_get_the_action(self):
        """Require model-add permission before exposing the inline action."""
        staff = get_user_model().objects.create_user(
            username="brand-viewer",
            email="brand-viewer@example.test",
            is_staff=True,
        )
        staff.user_permissions.add(
            Permission.objects.get(codename="view_brand"),
        )
        self.client.force_login(staff)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Add model")
        self.assertEqual(
            self.client.get(reverse("admin:firmwares_model_add")).status_code,
            403,
        )

    def test_brand_logo_upload_is_saved_through_the_admin_change_form(self):
        """Persist a valid logo uploaded through the brand change form."""
        change_page = self.client.get(self.url)
        inline_formset = change_page.context["inline_admin_formsets"][0].formset
        prefix = inline_formset.prefix
        with (
            TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            response = self.client.post(
                self.url,
                {
                    "name": self.brand.name,
                    "slug": self.brand.slug,
                    "description": "",
                    "website_url": "",
                    "website_link": "",
                    "logo_url": "",
                    "logo_link": "",
                    "shortener_mode": "inherit",
                    f"{prefix}-TOTAL_FORMS": "0",
                    f"{prefix}-INITIAL_FORMS": "0",
                    f"{prefix}-MIN_NUM_FORMS": "0",
                    f"{prefix}-MAX_NUM_FORMS": "1000",
                    "_save": "Save",
                    "logo_upload": image_upload("brand.png"),
                },
            )
            self.assertEqual(
                response.status_code,
                302,
                response.content.decode()[:1000],
            )
            self.brand.refresh_from_db()
            logo_path = self.brand.logo_upload.name
            if not logo_path:
                self.fail("Brand logo was not saved.")
            self.assertTrue(logo_path.endswith("brand.png"))
            self.assertTrue(self.brand.logo_upload.storage.exists(logo_path))

    def test_model_image_upload_is_saved_through_the_admin_change_form(self):
        """Persist a valid device image uploaded through the model change form."""
        model = Model.objects.create(
            brand=self.brand,
            name="Upload Phone",
            slug="upload-phone",
        )
        url = reverse("admin:firmwares_model_change", args=[model.pk])
        change_page = self.client.get(url)
        inline_data = {}
        for inline in change_page.context["inline_admin_formsets"]:
            prefix = inline.formset.prefix
            inline_data.update(
                {
                    f"{prefix}-TOTAL_FORMS": "0",
                    f"{prefix}-INITIAL_FORMS": "0",
                    f"{prefix}-MIN_NUM_FORMS": "0",
                    f"{prefix}-MAX_NUM_FORMS": "1000",
                }
            )
        self.assertContains(change_page, 'name="image_upload"')

        with (
            TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            response = self.client.post(
                url,
                {
                    "brand": str(self.brand.pk),
                    "name": model.name,
                    "slug": model.slug,
                    "status": model.status,
                    **inline_data,
                    "_save": "Save",
                    "image_upload": image_upload("device.png"),
                },
            )
            if response.status_code != 302:
                errors = response.context["adminform"].form.errors
            else:
                errors = ""
            self.assertEqual(response.status_code, 302, errors)
            model.refresh_from_db()
            image_path = model.image_upload.name
            if not image_path:
                self.fail("Device image was not saved.")
            self.assertTrue(image_path.endswith("device.png"))
            self.assertTrue(model.image_upload.storage.exists(image_path))

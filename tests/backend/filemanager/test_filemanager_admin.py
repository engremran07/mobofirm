from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.filemanager import services
from apps.filemanager.models import FileItem, FileShare, StorageUsage

DIALOG = {"_form_submitted": "on"}


class FileManagerAdminTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(FILEMANAGER_ROOT=Path(tmp.name))
        override.enable()
        self.addCleanup(override.disable)
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="fm-admin-owner",
            email="fm-admin-owner@example.test",
            email_verified_at=timezone.now(),
        )
        self.admin_user = user_model.objects.create_superuser(
            username="fm-admin-root", email="fm-admin-root@example.test"
        )
        self.item = services.store_upload(
            self.owner, SimpleUploadedFile("notes.txt", b"hello world"), None
        )
        self.client = Client()
        self.client.force_login(self.admin_user)

    def trash(self, days_ago: int) -> None:
        services.soft_delete_file(self.owner, self.item)
        FileItem.objects.filter(pk=self.item.pk).update(
            deleted_at=timezone.now() - timedelta(days=days_ago)
        )

    def test_purge_expired_trash_removes_only_files_past_retention(self) -> None:
        self.trash(days_ago=services.TRASH_RETENTION_DAYS + 1)
        recent = services.store_upload(
            self.owner, SimpleUploadedFile("recent.txt", b"recent"), None
        )
        services.soft_delete_file(self.owner, recent)
        url = reverse("admin:filemanager_fileitem_purge_expired_trash")

        opened = self.client.get(url)
        done = self.client.post(url, DIALOG)

        self.assertEqual(opened.status_code, 200)
        self.assertIn("HX-Redirect", done.headers)
        self.assertFalse(FileItem.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(FileItem.objects.filter(pk=recent.pk).exists())

    def test_bulk_purge_refuses_files_that_are_not_in_the_trash(self) -> None:
        changelist = reverse("admin:filemanager_fileitem_changelist")

        self.client.post(
            changelist,
            {
                "action": "purge_selected_from_trash",
                "_selected_action": [self.item.pk],
            },
        )
        self.assertTrue(FileItem.objects.filter(pk=self.item.pk).exists())

        services.soft_delete_file(self.owner, self.item)
        self.client.post(
            changelist,
            {
                "action": "purge_selected_from_trash",
                "_selected_action": [self.item.pk],
            },
        )
        self.assertFalse(FileItem.objects.filter(pk=self.item.pk).exists())

    def test_purge_actions_need_the_delete_permission(self) -> None:
        self.trash(days_ago=services.TRASH_RETENTION_DAYS + 1)
        editor = get_user_model().objects.create_user(
            username="fm-editor", email="fm-editor@example.test", is_staff=True
        )
        editor.user_permissions.add(
            *Permission.objects.filter(
                codename__in=("view_fileitem", "change_fileitem")
            )
        )
        client = Client()
        client.force_login(editor)

        response = client.post(
            reverse("admin:filemanager_fileitem_purge_expired_trash"), DIALOG
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(FileItem.objects.filter(pk=self.item.pk).exists())

    def test_files_cannot_be_added_through_the_admin(self) -> None:
        response = self.client.get(reverse("admin:filemanager_fileitem_add"))

        self.assertEqual(response.status_code, 403)

    def test_revoke_share_dialog_deactivates_the_link(self) -> None:
        share = services.create_share(self.owner, self.item)
        url = reverse("admin:filemanager_fileshare_revoke_share", args=[share.pk])

        self.client.post(url, DIALOG)

        share.refresh_from_db()
        self.assertFalse(share.is_active)

    def test_expire_shares_list_action_only_touches_overdue_links(self) -> None:
        overdue = services.create_share(self.owner, self.item)
        FileShare.objects.filter(pk=overdue.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )
        live = services.create_share(self.owner, self.item)

        self.client.post(reverse("admin:filemanager_fileshare_expire_shares"), DIALOG)

        overdue.refresh_from_db()
        live.refresh_from_db()
        self.assertFalse(overdue.is_active)
        self.assertTrue(live.is_active)

    def test_share_secrets_are_not_part_of_the_admin_form(self) -> None:
        share = services.create_share(self.owner, self.item)

        response = self.client.get(
            reverse("admin:filemanager_fileshare_change", args=[share.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, share.token)
        self.assertNotContains(response, 'name="password_hash"')

    def test_recalculate_usage_corrects_drift(self) -> None:
        StorageUsage.objects.filter(user=self.owner).update(used_bytes=999999)
        usage = StorageUsage.objects.get(user=self.owner)
        url = reverse(
            "admin:filemanager_storageusage_recalculate_usage", args=[usage.pk]
        )

        self.client.post(url, DIALOG)

        usage.refresh_from_db()
        self.assertEqual(usage.used_bytes, self.item.size)

        StorageUsage.objects.filter(pk=usage.pk).update(used_bytes=1)
        self.client.post(
            reverse("admin:filemanager_storageusage_changelist"),
            {"action": "recalculate_selected", "_selected_action": [usage.pk]},
        )
        usage.refresh_from_db()
        self.assertEqual(usage.used_bytes, self.item.size)

    def test_staff_without_change_permission_cannot_recalculate_or_revoke(self) -> None:
        viewer = get_user_model().objects.create_user(
            username="fm-viewer", email="fm-viewer@example.test", is_staff=True
        )
        viewer.user_permissions.add(
            *Permission.objects.filter(
                codename__in=("view_storageusage", "view_fileshare")
            )
        )
        client = Client()
        client.force_login(viewer)
        usage = StorageUsage.objects.get(user=self.owner)
        share = services.create_share(self.owner, self.item)

        recalculated = client.post(
            reverse(
                "admin:filemanager_storageusage_recalculate_usage", args=[usage.pk]
            ),
            DIALOG,
        )
        revoked = client.post(
            reverse("admin:filemanager_fileshare_revoke_share", args=[share.pk]),
            DIALOG,
        )

        self.assertEqual(recalculated.status_code, 403)
        self.assertEqual(revoked.status_code, 403)
        share.refresh_from_db()
        self.assertTrue(share.is_active)

    def test_every_filemanager_changelist_and_add_form_render(self) -> None:
        request = RequestFactory().get("/")
        request.user = self.admin_user
        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label != "filemanager":
                continue
            name = model._meta.model_name
            with self.subTest(model=model._meta.label, page="changelist"):
                self.assertEqual(
                    self.client.get(
                        reverse(f"admin:filemanager_{name}_changelist")
                    ).status_code,
                    200,
                )
            if model_admin.has_add_permission(request):
                with self.subTest(model=model._meta.label, page="add"):
                    self.assertEqual(
                        self.client.get(
                            reverse(f"admin:filemanager_{name}_add")
                        ).status_code,
                        200,
                    )
        detail = reverse("admin:filemanager_fileitem_change", args=[self.item.pk])
        self.assertEqual(self.client.get(detail).status_code, 200)

from __future__ import annotations

import io
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.core.models import AppRegistry
from apps.filemanager import permissions, services
from apps.filemanager.models import (
    FileItem,
    FileShare,
    Folder,
    FolderPermission,
    StorageUsage,
)
from apps.filemanager.tasks import purge_deleted_files, purge_expired_shares
from apps.filemanager.templatetags.filemanager_tags import filesize
from apps.filemanager.validation import (
    UploadRejected,
    sanitize_filename,
    validate_upload,
)

PASSWORD = "Pass-12345!"


def zip_bytes(name: str = "a.txt") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, "hello")
    return buffer.getvalue()


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, "PNG")
    return buffer.getvalue()


def upload(name: str, payload: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, payload)


class FileManagerTestCase(TestCase):
    def setUp(self):
        cache.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        override = override_settings(FILEMANAGER_ROOT=self.root)
        override.enable()
        self.addCleanup(override.disable)
        user_model = get_user_model()

        def make(name: str):
            return user_model.objects.create_user(
                username=name,
                email=f"{name}@example.com",
                password=PASSWORD,
                email_verified_at=timezone.now(),
            )

        self.owner = make("fm_owner")
        self.other = make("fm_other")

    def track(self, response):
        """Close streamed file responses so Windows can delete the temp files."""
        self.addCleanup(response.close)
        return response


class UploadValidationTests(FileManagerTestCase):
    def test_filename_is_sanitised_and_path_components_removed(self):
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("C:\\Users\\x\\evil.zip"), "evil.zip")
        self.assertEqual(sanitize_filename(".hidden.txt"), "hidden.txt")
        for bad in ("", "...", "/"):
            with self.subTest(name=bad), self.assertRaises(UploadRejected):
                sanitize_filename(bad)

    def test_allow_list_rejects_scriptable_and_unknown_types(self):
        for name in ("x.svg", "x.html", "x.js", "x.exe", "noext"):
            with self.subTest(name=name), self.assertRaises(UploadRejected):
                validate_upload(upload(name, b"payload"))

    def test_content_must_match_the_extension(self):
        mismatches = {
            "fake.zip": b"not a zip",
            "fake.pdf": b"<html>",
            "fake.png": b"not an image",
            "fake.txt": b"\x00\x01binary",
            "fake.gz": b"plain",
        }
        for name, payload in mismatches.items():
            with self.subTest(name=name), self.assertRaises(UploadRejected):
                validate_upload(upload(name, payload))

    def test_valid_files_are_classified_hashed_and_mime_is_server_derived(self):
        archive = validate_upload(upload("pack.zip", zip_bytes()))
        image = validate_upload(upload("pic.png", png_bytes()))
        note = validate_upload(upload("note.txt", "héllo".encode()))

        self.assertEqual(archive.file_type, "archive")
        self.assertEqual(image.file_type, "image")
        self.assertEqual(image.mime_type, "image/png")
        self.assertEqual(len(note.sha256), 64)

    @override_settings(FILEMANAGER_MAX_UPLOAD_BYTES=10)
    def test_size_limit_and_empty_files(self):
        with self.assertRaises(UploadRejected):
            validate_upload(upload("big.txt", b"x" * 11))
        with self.assertRaises(UploadRejected):
            validate_upload(upload("empty.txt", b""))


class StoreUploadTests(FileManagerTestCase):
    def test_upload_uses_a_server_chosen_private_path(self):
        item = services.store_upload(self.owner, upload("../evil.txt", b"hello"), None)

        self.assertEqual(item.name, "evil.txt")
        stored = Path(item.file.path)
        self.assertTrue(stored.is_file())
        self.assertTrue(stored.resolve().is_relative_to(self.root.resolve()))
        stored_name = item.file.name or ""
        self.assertNotIn("evil", stored_name)
        self.assertTrue(stored_name.startswith(f"{self.owner.pk}/"))
        with self.assertRaises(ValueError):
            item.file.storage.url(item.file.name)

    def test_quota_is_enforced_atomically_and_released_on_delete(self):
        StorageUsage.objects.create(user=self.owner, quota_bytes=10)
        first = services.store_upload(self.owner, upload("a.txt", b"123456"), None)

        with self.assertRaises(services.QuotaExceeded):
            services.store_upload(self.owner, upload("b.txt", b"123456"), None)
        self.assertEqual(FileItem.objects.count(), 1)
        self.assertEqual(StorageUsage.objects.get(user=self.owner).used_bytes, 6)

        services.soft_delete_file(self.owner, first)
        self.assertEqual(StorageUsage.objects.get(user=self.owner).used_bytes, 0)
        services.store_upload(self.owner, upload("c.txt", b"123456"), None)

    def test_recalculate_usage_uses_active_files_as_source_of_truth(self):
        services.store_upload(self.owner, upload("a.txt", b"12345"), None)
        StorageUsage.objects.filter(user=self.owner).update(used_bytes=999)

        self.assertEqual(services.recalculate_usage(self.owner).used_bytes, 5)

    def test_upload_rate_limit(self):
        for index in range(services.UPLOADS_PER_MINUTE):
            services.store_upload(self.owner, upload(f"{index}.txt", b"x"), None)
        with self.assertRaises(services.FileManagerError):
            services.store_upload(self.owner, upload("late.txt", b"x"), None)

    def test_failed_validation_stores_nothing(self):
        with self.assertRaises(services.FileManagerError):
            services.store_upload(self.owner, upload("x.exe", b"MZ"), None)

        self.assertFalse(FileItem.objects.exists())
        self.assertEqual(list(self.root.rglob("*")), [])


class FolderAndPermissionTests(FileManagerTestCase):
    def test_folder_names_are_unique_per_parent_and_validated(self):
        parent = services.create_folder(self.owner, "Docs", None)
        services.create_folder(self.owner, "Docs2", None)
        child = services.create_folder(self.owner, "Specs", parent)

        with self.assertRaises(services.FileManagerError):
            services.create_folder(self.owner, "Docs", None)
        with self.assertRaises(services.FileManagerError):
            services.create_folder(self.owner, "Specs", parent)
        for bad in ("", "a/b", "..", "x" * 256):
            with self.subTest(name=bad), self.assertRaises(services.FileManagerError):
                services.create_folder(self.owner, bad, None)
        self.assertEqual(child.get_ancestors(), [parent])
        self.assertEqual(
            services.create_folder(self.other, "Docs", None).owner, self.other
        )

    def test_database_enforces_unique_root_folder_names(self):
        Folder.objects.create(name="Root", owner=self.owner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Folder.objects.create(name="Root", owner=self.owner)

    def test_permission_requires_user_or_group(self):
        folder = Folder.objects.create(name="Shared", owner=self.owner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FolderPermission.objects.create(folder=folder)

    def test_strangers_have_no_access_and_grants_are_action_specific(self):
        folder = Folder.objects.create(name="Shared", owner=self.owner)
        item = services.store_upload(self.owner, upload("a.txt", b"hello"), folder)

        self.assertFalse(permissions.can_access_folder(self.other, folder, "read"))
        self.assertFalse(permissions.can_access_file(self.other, item, "read"))

        FolderPermission.objects.create(folder=folder, user=self.other, can_read=True)

        self.assertTrue(permissions.can_access_file(self.other, item, "read"))
        self.assertFalse(permissions.can_access_file(self.other, item, "delete"))
        self.assertFalse(permissions.can_access_folder(self.other, folder, "write"))
        self.assertIn(item, permissions.readable_files(self.other))
        self.assertIn(folder, permissions.readable_folders(self.other))

    def test_group_grants_apply_to_group_members(self):
        folder = Folder.objects.create(name="Team", owner=self.owner)
        group = Group.objects.create(name="fm-team")
        FolderPermission.objects.create(folder=folder, group=group, can_read=True)
        self.assertFalse(permissions.can_access_folder(self.other, folder, "read"))

        self.other.groups.add(group)

        self.assertTrue(permissions.can_access_folder(self.other, folder, "read"))

    def test_staff_do_not_bypass_ownership(self):
        staff = get_user_model().objects.create_user(
            username="fm_staff",
            email="fm-staff@example.com",
            password=PASSWORD,
            is_staff=True,
        )
        folder = Folder.objects.create(name="Private", owner=self.owner)

        self.assertFalse(permissions.can_access_folder(staff, folder, "read"))


class DeletionTests(FileManagerTestCase):
    def test_soft_delete_hides_revokes_shares_and_keeps_bytes_until_purged(self):
        item = services.store_upload(self.owner, upload("a.txt", b"hello"), None)
        share = services.create_share(self.owner, item)
        path = Path(item.file.path)

        services.soft_delete_file(self.owner, item)

        item.refresh_from_db()
        share.refresh_from_db()
        self.assertFalse(item.is_active)
        self.assertFalse(share.is_active)
        self.assertTrue(path.exists())
        self.assertEqual(purge_deleted_files(), 0)
        self.assertTrue(path.exists())

    def test_purge_removes_only_old_soft_deleted_files(self):
        old = services.store_upload(self.owner, upload("old.txt", b"old"), None)
        recent = services.store_upload(self.owner, upload("recent.txt", b"new"), None)
        live = services.store_upload(self.owner, upload("live.txt", b"live"), None)
        services.soft_delete_file(self.owner, old)
        services.soft_delete_file(self.owner, recent)
        old_path, recent_path, live_path = (
            Path(old.file.path),
            Path(recent.file.path),
            Path(live.file.path),
        )
        FileItem.objects.filter(pk=old.pk).update(
            deleted_at=timezone.now() - timedelta(days=31)
        )

        self.assertEqual(purge_deleted_files(), 1)

        self.assertFalse(old_path.exists())
        self.assertTrue(recent_path.exists())
        self.assertTrue(live_path.exists())
        self.assertFalse(FileItem.objects.filter(pk=old.pk).exists())

    def test_delete_requires_permission(self):
        item = services.store_upload(self.owner, upload("a.txt", b"hello"), None)

        with self.assertRaises(services.PermissionDenied):
            services.soft_delete_file(self.other, item)


class ShareTests(FileManagerTestCase):
    def setUp(self):
        super().setUp()
        self.item = services.store_upload(self.owner, upload("a.txt", b"hello"), None)

    def test_share_options_are_validated(self):
        with self.assertRaises(services.FileManagerError):
            services.create_share(self.owner, self.item, expires_hours=0)
        with self.assertRaises(services.FileManagerError):
            services.create_share(self.owner, self.item, max_downloads=0)
        with self.assertRaises(services.PermissionDenied):
            services.create_share(self.other, self.item)

    def test_tokens_are_unguessable_and_passwords_are_hashed(self):
        share = services.create_share(self.owner, self.item, password="s3cret-pass")

        self.assertGreaterEqual(len(share.token), 40)
        self.assertNotIn("s3cret-pass", share.password_hash)

    def test_expired_revoked_and_exhausted_links_are_unavailable(self):
        expired = services.create_share(self.owner, self.item, expires_hours=1)
        FileShare.objects.filter(pk=expired.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        revoked = services.create_share(self.owner, self.item)
        services.revoke_share(self.owner, revoked)
        limited = services.create_share(self.owner, self.item, max_downloads=1)
        self.assertTrue(services.consume_share(limited))

        for share in (expired, revoked, limited):
            with (
                self.subTest(pk=share.pk),
                self.assertRaises(services.ShareUnavailable),
            ):
                services.authorize_share(share.token)

    def test_max_downloads_cannot_be_overrun(self):
        share = services.create_share(self.owner, self.item, max_downloads=2)

        results = [services.consume_share(share) for _ in range(4)]

        self.assertEqual(results, [True, True, False, False])
        share.refresh_from_db()
        self.assertEqual(share.download_count, 2)

    def test_wrong_passwords_lock_the_link_after_repeated_failures(self):
        share = services.create_share(self.owner, self.item, password="right-pass")

        for _ in range(services.SHARE_MAX_FAILED_ATTEMPTS):
            with self.assertRaises(services.WrongSharePassword):
                services.authorize_share(share.token, "wrong")
        with self.assertRaises(services.ShareLocked):
            services.authorize_share(share.token, "right-pass")

        FileShare.objects.filter(pk=share.pk).update(
            locked_until=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(
            services.authorize_share(share.token, "right-pass").pk, share.pk
        )

    def test_expiry_task_deactivates_overdue_shares(self):
        share = services.create_share(self.owner, self.item, expires_hours=1)
        FileShare.objects.filter(pk=share.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        self.assertEqual(purge_expired_shares(), 1)
        self.assertEqual(purge_expired_shares(), 0)


class FileManagerViewTests(FileManagerTestCase):
    def test_all_private_routes_require_login(self):
        item = services.store_upload(self.owner, upload("a.txt", b"hello"), None)
        for name, kwargs in (
            ("filemanager:browser", {}),
            ("filemanager:download", {"pk": item.pk}),
        ):
            with self.subTest(route=name):
                response = self.client.get(reverse(name, kwargs=kwargs))
                self.assertEqual(response.status_code, 302)
        for name, kwargs in (
            ("filemanager:upload", {}),
            ("filemanager:create_folder", {}),
            ("filemanager:rename_file", {"pk": item.pk}),
            ("filemanager:delete_file", {"pk": item.pk}),
            ("filemanager:create_share", {"pk": item.pk}),
        ):
            with self.subTest(route=name):
                response = self.client.post(reverse(name, kwargs=kwargs))
                self.assertEqual(response.status_code, 302)
        self.assertTrue(FileItem.objects.get(pk=item.pk).is_active)

    def test_other_users_cannot_discover_or_download_files(self):
        item = services.store_upload(self.owner, upload("secret.txt", b"hello"), None)
        self.client.force_login(self.other)

        for name in ("download", "rename_file", "delete_file", "create_share"):
            with self.subTest(route=name):
                url = reverse(f"filemanager:{name}", kwargs={"pk": item.pk})
                response = (
                    self.client.get(url)
                    if name == "download"
                    else self.client.post(url, {"name": "x.txt"})
                )
                self.assertEqual(response.status_code, 404)
        self.assertNotContains(
            self.client.get(reverse("filemanager:browser")), "secret.txt"
        )
        self.assertEqual(
            self.client.get(
                reverse("filemanager:browser"), {"folder": 99999}
            ).status_code,
            404,
        )

    def test_upload_view_stores_files_and_reports_errors(self):
        self.client.force_login(self.owner)
        url = reverse("filemanager:upload")

        response = self.client.post(
            url,
            {"files": [upload("ok.txt", b"hello"), upload("bad.exe", b"MZ")]},
        )

        self.assertRedirects(response, reverse("filemanager:browser"))
        self.assertEqual(
            list(FileItem.objects.values_list("name", flat=True)), ["ok.txt"]
        )
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("bad.exe" in m for m in messages))

    def test_unverified_users_cannot_upload_or_share(self):
        unverified = get_user_model().objects.create_user(
            username="fm_unverified",
            email="fm-unverified@example.com",
            password=PASSWORD,
        )
        self.client.force_login(unverified)

        self.client.post(
            reverse("filemanager:upload"), {"files": upload("a.txt", b"x")}
        )

        self.assertFalse(FileItem.objects.exists())

    def test_download_is_a_forced_attachment_never_inline(self):
        item = services.store_upload(self.owner, upload("page.txt", b"<script>"), None)
        self.client.force_login(self.owner)

        response = self.track(
            self.client.get(reverse("filemanager:download", kwargs={"pk": item.pk}))
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(b"".join(response.streaming_content), b"<script>")
        item.refresh_from_db()
        self.assertEqual(item.download_count, 1)

    def test_missing_file_on_disk_is_a_404(self):
        item = services.store_upload(self.owner, upload("a.txt", b"hello"), None)
        Path(item.file.path).unlink()
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("filemanager:download", kwargs={"pk": item.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_shared_folder_member_can_read_but_not_delete(self):
        folder = Folder.objects.create(name="Shared", owner=self.owner)
        item = services.store_upload(self.owner, upload("a.txt", b"hello"), folder)
        FolderPermission.objects.create(folder=folder, user=self.other, can_read=True)
        self.client.force_login(self.other)

        browse = self.client.get(reverse("filemanager:browser"), {"folder": folder.pk})
        self.assertContains(browse, "a.txt")
        download = self.track(
            self.client.get(reverse("filemanager:download", kwargs={"pk": item.pk}))
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(
            self.client.post(
                reverse("filemanager:delete_file", kwargs={"pk": item.pk})
            ).status_code,
            404,
        )

    def test_state_changing_routes_enforce_csrf(self):
        item = services.store_upload(self.owner, upload("a.txt", b"hello"), None)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)

        for name, kwargs in (
            ("filemanager:upload", {}),
            ("filemanager:delete_file", {"pk": item.pk}),
            ("filemanager:create_share", {"pk": item.pk}),
        ):
            with self.subTest(route=name):
                response = csrf_client.post(reverse(name, kwargs=kwargs))
                self.assertEqual(response.status_code, 403)

    def test_create_share_view_returns_a_link_and_revocation_works(self):
        item = services.store_upload(self.owner, upload("a.txt", b"hello"), None)
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("filemanager:create_share", kwargs={"pk": item.pk}),
            {"max_downloads": 1, "password": "pw-123456"},
        )

        share = FileShare.objects.get()
        link = reverse("filemanager:shared_file", kwargs={"token": share.token})
        self.assertTrue(
            any(link in str(m) for m in get_messages(response.wsgi_request))
        )
        self.client.post(reverse("filemanager:revoke_share", kwargs={"pk": share.pk}))
        share.refresh_from_db()
        self.assertFalse(share.is_active)

    def test_only_the_creator_or_owner_can_revoke(self):
        item = services.store_upload(self.owner, upload("a.txt", b"hello"), None)
        share = services.create_share(self.owner, item)
        self.client.force_login(self.other)

        response = self.client.post(
            reverse("filemanager:revoke_share", kwargs={"pk": share.pk})
        )

        self.assertEqual(response.status_code, 404)
        share.refresh_from_db()
        self.assertTrue(share.is_active)


class PublicShareViewTests(FileManagerTestCase):
    def setUp(self):
        super().setUp()
        self.item = services.store_upload(
            self.owner, upload("shared.txt", b"hello"), None
        )

    def url(self, share: FileShare) -> str:
        return reverse("filemanager:shared_file", kwargs={"token": share.token})

    def test_get_shows_metadata_only_and_download_is_post(self):
        share = services.create_share(self.owner, self.item)

        page = self.client.get(self.url(share))
        self.assertContains(page, "shared.txt")
        self.assertContains(page, "noindex")

        download = self.track(self.client.post(self.url(share)))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), b"hello")

    def test_download_requires_csrf_token(self):
        share = services.create_share(self.owner, self.item)

        response = Client(enforce_csrf_checks=True).post(self.url(share))

        self.assertEqual(response.status_code, 403)

    def test_password_protected_link_and_lockout(self):
        share = services.create_share(self.owner, self.item, password="right-pass")

        self.assertEqual(
            self.client.post(self.url(share), {"password": "bad"}).status_code, 403
        )
        ok = self.track(self.client.post(self.url(share), {"password": "right-pass"}))
        self.assertEqual(ok.status_code, 200)
        for _ in range(services.SHARE_MAX_FAILED_ATTEMPTS):
            self.client.post(self.url(share), {"password": "bad"})
        locked = self.client.post(self.url(share), {"password": "right-pass"})
        self.assertEqual(locked.status_code, 429)

    def test_download_limit_is_enforced(self):
        share = services.create_share(self.owner, self.item, max_downloads=1)

        first = self.track(self.client.post(self.url(share)))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.client.post(self.url(share)).status_code, 404)

    def test_unknown_expired_or_deleted_targets_are_404(self):
        share = services.create_share(self.owner, self.item)
        unknown = self.client.get(
            reverse("filemanager:shared_file", kwargs={"token": "nope"})
        )
        self.assertEqual(unknown.status_code, 404)
        FileItem.objects.filter(pk=self.item.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.url(share)).status_code, 404)
        self.assertEqual(self.client.post(self.url(share)).status_code, 404)


class FileManagerMiscTests(FileManagerTestCase):
    def test_filesize_filter(self):
        self.assertEqual(filesize(None), "—")
        self.assertEqual(filesize(0), "0 B")
        self.assertEqual(filesize(1536), "1.5 KB")
        self.assertEqual(filesize("abc"), "abc")

    def test_every_model_is_registered_and_share_tokens_are_not_exposed(self):
        for model in (Folder, FileItem, FileShare, FolderPermission, StorageUsage):
            with self.subTest(model=model.__name__):
                self.assertIn(model, admin.site._registry)
        share_admin = admin.site._registry[FileShare]
        excluded = share_admin.exclude or ()
        self.assertIn("token", excluded)
        self.assertIn("password_hash", excluded)

    def test_feature_flag_hides_the_file_manager(self):
        registry = AppRegistry.get_solo()
        registry.filemanager_enabled = False
        registry.save()
        self.client.force_login(self.owner)

        self.assertEqual(
            self.client.get(reverse("filemanager:browser")).status_code, 404
        )

from __future__ import annotations

import io
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.filemanager import services
from apps.filemanager.models import (
    FileItem,
    FileShare,
    FileTag,
    FileVersion,
    Folder,
    FolderPermission,
    StorageUsage,
)

PASSWORD = "Pass-12345!"


def png_bytes(size: tuple[int, int] = (2, 2), color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def upload(name: str, payload: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, payload)


class FileUiTestCase(TestCase):
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

        self.owner = make("ui_fm_owner")
        self.other = make("ui_fm_other")

    def client_for(self, user, **kwargs) -> Client:
        client = Client(**kwargs)
        client.force_login(user)
        return client

    def store(self, name="a.txt", payload=b"hello", folder=None, user=None):
        return services.store_upload(user or self.owner, upload(name, payload), folder)

    def usage(self, user=None) -> int:
        return StorageUsage.objects.get(user=user or self.owner).used_bytes

    def messages(self, response) -> list[str]:
        return [str(message) for message in response.wsgi_request._messages]

    def track(self, response):
        self.addCleanup(response.close)
        return response


class BrowserUiTests(FileUiTestCase):
    def test_browser_has_dropzone_upload_form_quota_meter_and_navigation(self):
        response = self.client_for(self.owner).get(reverse("filemanager:browser"))

        self.assertContains(response, 'x-data="fmDropzone"')
        self.assertContains(response, 'hx-encoding="multipart/form-data"')
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, "<meter")
        self.assertContains(response, reverse("filemanager:trash"))
        self.assertContains(response, reverse("filemanager:shares"))
        self.assertContains(response, "No files here yet")

    def test_htmx_upload_stores_files_and_redirects_the_client(self):
        folder = services.create_folder(self.owner, "Target", None)

        response = self.client_for(self.owner).post(
            reverse("filemanager:upload"),
            {"files": upload("drop.txt", b"dropped"), "folder": folder.pk},
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["HX-Redirect"],
            f"{reverse('filemanager:browser')}?folder={folder.pk}",
        )
        self.assertEqual(FileItem.objects.get().folder, folder)
        self.assertTrue(
            any("Uploaded 1 file" in text for text in self.messages(response))
        )

    def test_htmx_upload_failures_come_back_as_messages(self):
        response = self.client_for(self.owner).post(
            reverse("filemanager:upload"),
            {"files": upload("bad.exe", b"MZ")},
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response["HX-Redirect"], reverse("filemanager:browser"))
        self.assertTrue(any("bad.exe" in text for text in self.messages(response)))
        self.assertFalse(FileItem.objects.exists())

    def test_plain_upload_still_works_without_javascript(self):
        response = self.client_for(self.owner).post(
            reverse("filemanager:upload"), {"files": upload("plain.txt", b"plain")}
        )

        self.assertRedirects(response, reverse("filemanager:browser"))
        self.assertEqual(FileItem.objects.count(), 1)

    def test_cards_show_name_size_type_tags_and_actions(self):
        item = self.store("report.txt", b"12345")
        services.set_file_tags(self.owner, item, "Work")

        response = self.client_for(self.owner).get(reverse("filemanager:browser"))

        self.assertContains(response, "report.txt")
        self.assertContains(response, "5 B")
        self.assertContains(response, "Work")
        self.assertContains(response, reverse("filemanager:download", args=[item.pk]))
        self.assertContains(
            response, reverse("filemanager:delete_file", args=[item.pk])
        )

    def test_search_covers_all_folders_and_respects_type_and_tag_filters(self):
        folder = services.create_folder(self.owner, "Deep", None)
        needle = self.store("needle.txt", folder=folder)
        self.store("haystack.zip", self._zip(), folder=None)
        services.set_file_tags(self.owner, needle, "important")
        client = self.client_for(self.owner)
        url = reverse("filemanager:browser")

        by_name = client.get(url, {"q": "needle"})
        by_type = client.get(url, {"type": "archive"})
        by_tag = client.get(url, {"tag": "important"})
        nothing = client.get(url, {"q": "zzz"})

        self.assertContains(by_name, "needle.txt")
        self.assertNotContains(by_name, "haystack.zip")
        self.assertContains(by_type, "haystack.zip")
        self.assertNotContains(by_type, "needle.txt")
        self.assertContains(by_tag, "needle.txt")
        self.assertContains(by_tag, "Clear filter")
        self.assertContains(nothing, "No files match")

    def test_search_never_reveals_other_peoples_files(self):
        self.store("mine.txt")
        self.store("theirs.txt", user=self.other)

        response = self.client_for(self.owner).get(
            reverse("filemanager:browser"), {"q": "txt"}
        )

        self.assertContains(response, "mine.txt")
        self.assertNotContains(response, "theirs.txt")

    def test_tag_filter_only_matches_the_visitors_own_tags(self):
        theirs = self.store("theirs.txt", user=self.other)
        services.set_file_tags(self.other, theirs, "shared-name")

        response = self.client_for(self.owner).get(
            reverse("filemanager:browser"), {"tag": "shared-name"}
        )

        self.assertNotContains(response, "theirs.txt")

    def test_file_grid_is_paginated(self):
        for index in range(3):
            self.store(f"f{index}.txt")

        with mock.patch("apps.filemanager.views.PAGE_SIZE", 2):
            response = self.client_for(self.owner).get(reverse("filemanager:browser"))

        self.assertContains(response, 'aria-label="Files"')
        self.assertContains(response, "Page 1 of 2")

    def test_unknown_filter_values_are_ignored(self):
        response = self.client_for(self.owner).get(
            reverse("filemanager:browser"), {"type": "bogus<script>"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "bogus<script>")

    def _zip(self) -> bytes:
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "x")
        return buffer.getvalue()


class FolderManagementTests(FileUiTestCase):
    def test_rename_folder(self):
        folder = services.create_folder(self.owner, "Old", None)

        response = self.client_for(self.owner).post(
            reverse("filemanager:rename_folder", args=[folder.pk]), {"name": "New"}
        )

        folder.refresh_from_db()
        self.assertEqual(folder.name, "New")
        self.assertRedirects(
            response, f"{reverse('filemanager:browser')}?folder={folder.pk}"
        )

    def test_rename_to_an_existing_sibling_name_is_a_friendly_error(self):
        services.create_folder(self.owner, "Taken", None)
        folder = services.create_folder(self.owner, "Mine", None)

        response = self.client_for(self.owner).post(
            reverse("filemanager:rename_folder", args=[folder.pk]), {"name": "Taken"}
        )

        folder.refresh_from_db()
        self.assertEqual(folder.name, "Mine")
        self.assertIn("already exists", " ".join(self.messages(response)))

    def test_only_empty_folders_can_be_deleted(self):
        parent = services.create_folder(self.owner, "Parent", None)
        child = services.create_folder(self.owner, "Child", parent)
        self.store("inside.txt", folder=child)
        client = self.client_for(self.owner)

        blocked = client.post(reverse("filemanager:delete_folder", args=[child.pk]))
        self.assertTrue(Folder.objects.filter(pk=child.pk).exists())
        self.assertIn("empty", " ".join(self.messages(blocked)))

        services.soft_delete_file(self.owner, FileItem.objects.get())
        done = client.post(reverse("filemanager:delete_folder", args=[child.pk]))

        self.assertFalse(Folder.objects.filter(pk=child.pk).exists())
        self.assertRedirects(
            done, f"{reverse('filemanager:browser')}?folder={parent.pk}"
        )
        item = FileItem.objects.get()
        self.assertIsNone(item.folder)
        self.assertFalse(item.is_active)

    def test_other_users_cannot_touch_a_folder(self):
        folder = services.create_folder(self.owner, "Private", None)
        client = self.client_for(self.other)

        rename = client.post(
            reverse("filemanager:rename_folder", args=[folder.pk]), {"name": "Hacked"}
        )
        delete = client.post(reverse("filemanager:delete_folder", args=[folder.pk]))

        self.assertEqual((rename.status_code, delete.status_code), (404, 404))
        folder.refresh_from_db()
        self.assertEqual(folder.name, "Private")

    def test_folder_page_offers_rename_and_a_confirmation_dialog_for_delete(self):
        folder = services.create_folder(self.owner, "Docs", None)

        response = self.client_for(self.owner).get(
            reverse("filemanager:browser"), {"folder": folder.pk}
        )

        self.assertContains(
            response, reverse("filemanager:rename_folder", args=[folder.pk])
        )
        self.assertContains(response, 'data-dialog-open="delete-folder-dialog"')
        self.assertContains(response, 'id="delete-folder-dialog"')
        self.assertNotContains(response, "confirm(")


class MoveTests(FileUiTestCase):
    def test_move_between_folders_and_back_to_the_top_level(self):
        folder = services.create_folder(self.owner, "Docs", None)
        item = self.store()
        client = self.client_for(self.owner)

        moved = client.post(
            reverse("filemanager:move_file", args=[item.pk]), {"destination": folder.pk}
        )
        item.refresh_from_db()
        self.assertEqual(item.folder, folder)
        self.assertRedirects(
            moved, f"{reverse('filemanager:browser')}?folder={folder.pk}"
        )

        client.post(
            reverse("filemanager:move_file", args=[item.pk]), {"destination": ""}
        )
        item.refresh_from_db()
        self.assertIsNone(item.folder)

    def test_cannot_move_into_someone_elses_folder(self):
        theirs = services.create_folder(self.other, "Theirs", None)
        item = self.store()

        response = self.client_for(self.owner).post(
            reverse("filemanager:move_file", args=[item.pk]), {"destination": theirs.pk}
        )

        self.assertEqual(response.status_code, 404)
        item.refresh_from_db()
        self.assertIsNone(item.folder)

    def test_writers_cannot_move_files_across_owners_or_to_the_owners_root(self):
        shared = services.create_folder(self.owner, "Shared", None)
        FolderPermission.objects.create(folder=shared, user=self.other, can_write=True)
        mine = services.create_folder(self.other, "Mine", None)
        item = self.store(folder=shared)

        with self.assertRaises(services.FileManagerError):
            services.move_file(self.other, item, mine)
        with self.assertRaises(services.PermissionDenied):
            services.move_file(self.other, item, None)

    def test_detail_page_lists_only_the_owners_folders_as_destinations(self):
        services.create_folder(self.owner, "Alpha", None)
        services.create_folder(self.other, "Beta", None)
        item = self.store()

        response = self.client_for(self.owner).get(
            reverse("filemanager:file_detail", args=[item.pk])
        )

        self.assertContains(response, "Alpha")
        self.assertNotContains(response, "Beta")

    def test_folder_paths_include_parents(self):
        parent = services.create_folder(self.owner, "A", None)
        services.create_folder(self.owner, "B", parent)

        paths = [path for _, path in services.folder_path_choices(self.owner)]

        self.assertEqual(paths, ["A", "A / B"])


class TrashTests(FileUiTestCase):
    def test_delete_moves_to_trash_and_the_trash_page_lists_it(self):
        item = self.store("gone.txt", b"12345")
        client = self.client_for(self.owner)

        response = client.post(reverse("filemanager:delete_file", args=[item.pk]))
        trash = client.get(reverse("filemanager:trash"))

        self.assertIn("trash", " ".join(self.messages(response)))
        self.assertContains(trash, "gone.txt")
        self.assertContains(trash, "Delete forever")
        self.assertContains(trash, "Deleted files wait here for 30 days")
        self.assertEqual(self.usage(), 0)

    def test_restore_reactivates_the_file_and_charges_the_quota_again(self):
        item = self.store("back.txt", b"12345")
        services.soft_delete_file(self.owner, item)

        response = self.client_for(self.owner).post(
            reverse("filemanager:restore_file", args=[item.pk])
        )

        item.refresh_from_db()
        self.assertTrue(item.is_active)
        self.assertIsNone(item.deleted_at)
        self.assertEqual(self.usage(), 5)
        self.assertRedirects(
            response, reverse("filemanager:file_detail", args=[item.pk])
        )

    def test_restore_is_blocked_when_the_quota_is_now_full(self):
        item = self.store("big.txt", b"12345")
        services.soft_delete_file(self.owner, item)
        StorageUsage.objects.filter(user=self.owner).update(quota_bytes=3)

        response = self.client_for(self.owner).post(
            reverse("filemanager:restore_file", args=[item.pk])
        )

        item.refresh_from_db()
        self.assertFalse(item.is_active)
        self.assertIn("quota", " ".join(self.messages(response)))
        self.assertEqual(self.usage(), 0)

    def test_restoring_twice_does_not_double_count(self):
        item = self.store("once.txt", b"12345")
        services.soft_delete_file(self.owner, item)
        services.restore_file(self.owner, item)
        services.restore_file(self.owner, item)

        self.assertEqual(self.usage(), 5)

    def test_purge_removes_the_bytes_and_the_row(self):
        item = self.store("purge.txt")
        path = Path(item.file.path)
        services.soft_delete_file(self.owner, item)

        self.client_for(self.owner).post(
            reverse("filemanager:purge_file", args=[item.pk])
        )

        self.assertFalse(FileItem.objects.exists())
        self.assertFalse(path.exists())

    def test_active_files_cannot_be_purged_directly(self):
        item = self.store()

        response = self.client_for(self.owner).post(
            reverse("filemanager:purge_file", args=[item.pk])
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(FileItem.objects.exists())

    def test_empty_trash_removes_everything_in_it_only(self):
        keep = self.store("keep.txt")
        for name in ("x.txt", "y.txt"):
            services.soft_delete_file(self.owner, self.store(name))

        response = self.client_for(self.owner).post(reverse("filemanager:empty_trash"))

        self.assertEqual(list(FileItem.objects.all()), [keep])
        self.assertIn("2 file", " ".join(self.messages(response)))

    def test_other_users_cannot_restore_or_purge_my_trash(self):
        item = self.store()
        services.soft_delete_file(self.owner, item)
        client = self.client_for(self.other)

        restore = client.post(reverse("filemanager:restore_file", args=[item.pk]))
        purge = client.post(reverse("filemanager:purge_file", args=[item.pk]))

        self.assertEqual((restore.status_code, purge.status_code), (404, 404))
        self.assertTrue(FileItem.objects.filter(pk=item.pk, is_active=False).exists())

    def test_retention_purge_removes_versions_and_thumbnails_too(self):
        item = self.store("pic.png", png_bytes())
        services.add_version(
            self.owner, item, upload("pic.png", png_bytes(color="blue"))
        )
        item.refresh_from_db()
        version = item.versions.get()
        paths = [
            Path(item.file.path),
            Path(version.file.path),
            Path(item.thumbnail.path),
        ]
        services.soft_delete_file(self.owner, item)
        FileItem.objects.filter(pk=item.pk).update(
            deleted_at=timezone.now() - timedelta(days=31)
        )

        self.assertEqual(services.purge_deleted_files(), 1)

        self.assertFalse(any(path.exists() for path in paths))
        self.assertFalse(FileVersion.objects.exists())

    def test_trash_page_is_empty_state_without_files(self):
        response = self.client_for(self.owner).get(reverse("filemanager:trash"))

        self.assertContains(response, "The trash is empty")


class TagTests(FileUiTestCase):
    def test_tags_are_normalised_deduplicated_and_saved_through_the_view(self):
        item = self.store()

        response = self.client_for(self.owner).post(
            reverse("filemanager:set_tags", args=[item.pk]),
            {"tags": "Work, work ,  Home  Office; ,Taxes 2026"},
        )

        names = sorted(tag.name for tag in item.tags.all())
        self.assertEqual(names, ["Home Office", "Taxes 2026", "Work"])
        self.assertRedirects(
            response, reverse("filemanager:file_detail", args=[item.pk]) + "#tags"
        )

    def test_replacing_tags_prunes_unused_ones(self):
        item = self.store()
        services.set_file_tags(self.owner, item, "a, b")
        services.set_file_tags(self.owner, item, "b")

        self.assertEqual(list(FileTag.objects.values_list("slug", flat=True)), ["b"])

    def test_tags_are_private_per_owner(self):
        mine = self.store()
        theirs = self.store(user=self.other)
        services.set_file_tags(self.owner, mine, "invoices")
        services.set_file_tags(self.other, theirs, "invoices")

        self.assertEqual(FileTag.objects.filter(slug="invoices").count(), 2)
        self.assertNotEqual(mine.tags.get().owner_id, theirs.tags.get().owner_id)

    def test_invalid_tags_are_rejected_with_a_message(self):
        item = self.store()
        client = self.client_for(self.owner)

        too_long = client.post(
            reverse("filemanager:set_tags", args=[item.pk]), {"tags": "x" * 41}
        )
        too_many = client.post(
            reverse("filemanager:set_tags", args=[item.pk]),
            {"tags": ", ".join(f"t{i}" for i in range(11))},
        )
        symbols = client.post(
            reverse("filemanager:set_tags", args=[item.pk]), {"tags": "!!!"}
        )

        self.assertIn("at most 40", " ".join(self.messages(too_long)))
        self.assertIn("at most 10", " ".join(self.messages(too_many)))
        self.assertIn("letter or number", " ".join(self.messages(symbols)))
        self.assertFalse(item.tags.exists())

    def test_other_users_cannot_tag_my_file(self):
        item = self.store()

        response = self.client_for(self.other).post(
            reverse("filemanager:set_tags", args=[item.pk]), {"tags": "x"}
        )

        self.assertEqual(response.status_code, 404)

    def test_sidebar_counts_active_files_per_tag(self):
        item = self.store()
        trashed = self.store("t.txt")
        services.set_file_tags(self.owner, item, "docs")
        services.set_file_tags(self.owner, trashed, "docs")
        services.soft_delete_file(self.owner, trashed)

        response = self.client_for(self.owner).get(reverse("filemanager:browser"))

        self.assertContains(response, "docs · 1")


class VersionTests(FileUiTestCase):
    def test_new_version_keeps_the_previous_content_and_updates_usage(self):
        item = self.store("notes.txt", b"first")

        updated = services.add_version(
            self.owner, item, upload("notes.txt", b"second!")
        )

        self.assertEqual(updated.size, 7)
        self.assertEqual(updated.versions.count(), 1)
        old = updated.versions.get()
        self.assertEqual((old.version_number, old.size), (1, 5))
        self.assertEqual(Path(old.file.path).read_bytes(), b"first")
        self.assertEqual(Path(updated.file.path).read_bytes(), b"second!")
        self.assertEqual(self.usage(), 12)

    def test_version_must_keep_the_file_type(self):
        item = self.store("notes.txt")

        with self.assertRaises(services.FileManagerError):
            services.add_version(self.owner, item, upload("notes.md", b"x"))

        self.assertFalse(item.versions.exists())

    def test_version_upload_through_the_view_requires_a_file_and_csrf(self):
        item = self.store("notes.txt")
        url = reverse("filemanager:upload_version", args=[item.pk])

        missing = self.client_for(self.owner).post(url)
        csrf = self.client_for(self.owner, enforce_csrf_checks=True).post(
            url, {"file": upload("notes.txt", b"v2")}
        )
        ok = self.client_for(self.owner).post(url, {"file": upload("notes.txt", b"v2")})

        self.assertIn("Choose a file", " ".join(self.messages(missing)))
        self.assertEqual(csrf.status_code, 403)
        self.assertRedirects(
            ok, reverse("filemanager:file_detail", args=[item.pk]) + "#versions"
        )
        self.assertEqual(item.versions.count(), 1)

    def test_quota_blocks_a_version_that_does_not_fit(self):
        item = self.store("notes.txt", b"12345")
        StorageUsage.objects.filter(user=self.owner).update(quota_bytes=8)

        with self.assertRaises(services.QuotaExceeded):
            services.add_version(self.owner, item, upload("notes.txt", b"123456"))

        item.refresh_from_db()
        self.assertEqual((item.size, item.versions.count()), (5, 0))
        self.assertEqual(self.usage(), 5)
        self.assertEqual(len(list((self.root / str(self.owner.pk)).iterdir())), 1)

    def test_version_limit_is_enforced(self):
        item = self.store("notes.txt", b"v0")
        for index in range(services.MAX_VERSIONS_PER_FILE):
            services.add_version(
                self.owner, item, upload("notes.txt", f"v{index + 1}".encode())
            )

        with self.assertRaises(services.FileManagerError) as caught:
            services.add_version(self.owner, item, upload("notes.txt", b"one-too-many"))

        self.assertIn("version limit", str(caught.exception))

    def test_restore_swaps_content_without_changing_usage(self):
        item = self.store("notes.txt", b"first")
        services.add_version(self.owner, item, upload("notes.txt", b"second!"))
        first = item.versions.get()

        restored = services.restore_version(self.owner, item, first)

        self.assertEqual(restored.size, 5)
        self.assertEqual(Path(restored.file.path).read_bytes(), b"first")
        replaced = restored.versions.get()
        self.assertEqual((replaced.version_number, replaced.size), (2, 7))
        self.assertEqual(Path(replaced.file.path).read_bytes(), b"second!")
        self.assertEqual(self.usage(), 12)

    def test_delete_version_removes_bytes_and_frees_quota(self):
        item = self.store("notes.txt", b"first")
        services.add_version(self.owner, item, upload("notes.txt", b"second!"))
        version = item.versions.get()
        path = Path(version.file.path)

        services.delete_version(self.owner, item, version)

        self.assertFalse(path.exists())
        self.assertEqual(self.usage(), 7)

    def test_trash_and_restore_account_for_versions(self):
        item = self.store("notes.txt", b"first")
        services.add_version(self.owner, item, upload("notes.txt", b"second!"))
        self.assertEqual(self.usage(), 12)

        services.soft_delete_file(self.owner, item)
        self.assertEqual(self.usage(), 0)
        services.restore_file(self.owner, item)

        self.assertEqual(self.usage(), 12)

    def test_recalculate_usage_matches_incremental_accounting(self):
        item = self.store("notes.txt", b"first")
        services.add_version(self.owner, item, upload("notes.txt", b"second!"))
        self.store("other.txt", b"abc")

        self.assertEqual(services.recalculate_usage(self.owner).used_bytes, 15)

    def test_version_download_streams_the_old_bytes_to_authorised_users_only(self):
        item = self.store("notes.txt", b"first")
        services.add_version(self.owner, item, upload("notes.txt", b"second!"))
        version = item.versions.get()
        url = reverse("filemanager:download_version", args=[item.pk, version.pk])

        ok = self.track(self.client_for(self.owner).get(url))
        denied = self.client_for(self.other).get(url)

        self.assertEqual(b"".join(ok.streaming_content), b"first")
        self.assertIn("attachment", ok["Content-Disposition"])
        self.assertEqual(denied.status_code, 404)

    def test_version_ids_from_another_file_are_not_reachable(self):
        mine = self.store("mine.txt", b"a")
        services.add_version(self.owner, mine, upload("mine.txt", b"b"))
        other = self.store("other.txt", b"a")
        version = mine.versions.get()

        response = self.client_for(self.owner).post(
            reverse("filemanager:restore_version", args=[other.pk, version.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_readers_cannot_add_or_restore_versions(self):
        shared = services.create_folder(self.owner, "Shared", None)
        FolderPermission.objects.create(folder=shared, user=self.other, can_read=True)
        item = self.store("notes.txt", b"a", folder=shared)

        with self.assertRaises(services.PermissionDenied):
            services.add_version(self.other, item, upload("notes.txt", b"b"))

    def test_detail_page_lists_versions_with_actions(self):
        item = self.store("notes.txt", b"first")
        services.add_version(self.owner, item, upload("notes.txt", b"second!"))
        version = item.versions.get()

        response = self.client_for(self.owner).get(
            reverse("filemanager:file_detail", args=[item.pk])
        )

        self.assertContains(response, "v1")
        self.assertContains(
            response, reverse("filemanager:restore_version", args=[item.pk, version.pk])
        )
        self.assertContains(response, f'id="delete-version-{version.pk}"')
        self.assertContains(response, "with versions")


class ThumbnailTests(FileUiTestCase):
    def test_image_uploads_get_a_small_jpeg_preview(self):
        item = self.store("big.png", png_bytes((1000, 500)))

        self.assertTrue(item.thumbnail)
        with Image.open(item.thumbnail.path) as preview:
            self.assertEqual(preview.format, "JPEG")
            self.assertLessEqual(max(preview.size), 320)
        self.assertTrue(item.thumbnail.name.startswith("thumbnails/"))

    def test_transparent_images_are_flattened_onto_white(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(buffer, "PNG")

        item = self.store("clear.png", buffer.getvalue())

        with Image.open(item.thumbnail.path) as preview:
            self.assertEqual(preview.convert("RGB").getpixel((1, 1)), (255, 255, 255))

    def test_non_images_have_no_preview(self):
        self.assertFalse(self.store("a.txt").thumbnail)

    def test_upload_succeeds_without_a_preview_when_thumbnailing_fails(self):
        with mock.patch("apps.filemanager.services.make_thumbnail", return_value=None):
            item = self.store("pic.png", png_bytes())

        self.assertTrue(item.file)
        self.assertFalse(item.thumbnail)
        self.assertEqual(self.usage(), len(png_bytes()))

    def test_thumbnail_endpoint_serves_private_cached_jpeg_to_authorised_users(self):
        item = self.store("pic.png", png_bytes())
        url = reverse("filemanager:thumbnail", args=[item.pk])

        ok = self.track(self.client_for(self.owner).get(url))
        denied = self.client_for(self.other).get(url)
        anonymous = self.client.get(url)

        self.assertEqual(ok["Content-Type"], "image/jpeg")
        self.assertIn("private", ok["Cache-Control"])
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(anonymous.status_code, 302)

    def test_thumbnail_404s_for_files_without_one(self):
        item = self.store("a.txt")

        response = self.client_for(self.owner).get(
            reverse("filemanager:thumbnail", args=[item.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_new_version_replaces_the_preview_and_deletes_the_old_one(self):
        item = self.store("pic.png", png_bytes(color="red"))
        old_preview = Path(item.thumbnail.path)

        updated = services.add_version(
            self.owner, item, upload("pic.png", png_bytes((50, 50), "blue"))
        )

        self.assertFalse(old_preview.exists())
        self.assertTrue(Path(updated.thumbnail.path).exists())

    def test_grid_and_detail_use_the_thumbnail_endpoint(self):
        item = self.store("pic.png", png_bytes())
        client = self.client_for(self.owner)
        url = reverse("filemanager:thumbnail", args=[item.pk])

        self.assertContains(client.get(reverse("filemanager:browser")), url)
        self.assertContains(
            client.get(reverse("filemanager:file_detail", args=[item.pk])), url
        )


class ShareUiTests(FileUiTestCase):
    def test_create_share_with_expiry_limit_and_password_from_the_detail_page(self):
        item = self.store()
        client = self.client_for(self.owner)

        response = client.post(
            reverse("filemanager:create_share", args=[item.pk]),
            {"expires_hours": "24", "max_downloads": "3", "password": "s3cret-pass"},
        )

        share = FileShare.objects.get()
        if share.expires_at is None:
            self.fail("The share link should expire.")
        self.assertAlmostEqual(
            share.expires_at,
            timezone.now() + timedelta(hours=24),
            delta=timedelta(minutes=1),
        )
        self.assertEqual(share.max_downloads, 3)
        self.assertTrue(share.password_hash)
        self.assertRedirects(
            response, reverse("filemanager:file_detail", args=[item.pk]) + "#shares"
        )
        page = client.get(reverse("filemanager:file_detail", args=[item.pk]))
        self.assertContains(page, share.token)
        self.assertContains(page, "Password protected")
        self.assertContains(page, "Copy link")
        self.assertContains(page, f'id="revoke-{share.pk}"')

    def test_share_without_expiry_never_expires(self):
        item = self.store()

        self.client_for(self.owner).post(
            reverse("filemanager:create_share", args=[item.pk]), {"expires_hours": ""}
        )

        self.assertIsNone(FileShare.objects.get().expires_at)

    def test_out_of_range_expiry_is_rejected(self):
        item = self.store()

        response = self.client_for(self.owner).post(
            reverse("filemanager:create_share", args=[item.pk]),
            {"expires_hours": "99999"},
        )

        self.assertFalse(FileShare.objects.exists())
        self.assertIn("Check the share options", " ".join(self.messages(response)))

    def test_share_list_page_shows_links_and_revoking_returns_to_it(self):
        item = self.store()
        share = services.create_share(self.owner, item, expires_hours=1)
        client = self.client_for(self.owner)

        listing = client.get(reverse("filemanager:shares"))
        revoke = client.post(
            reverse("filemanager:revoke_share", args=[share.pk]), {"next": "shares"}
        )

        self.assertContains(listing, share.token)
        self.assertContains(listing, f'data-copy-target="share-link-{share.pk}"')
        self.assertRedirects(revoke, reverse("filemanager:shares"))
        share.refresh_from_db()
        self.assertFalse(share.is_active)
        self.assertContains(
            client.get(reverse("filemanager:shares")), "No active share links"
        )

    def test_share_list_hides_other_users_links_and_trashed_files(self):
        mine = self.store("mine.txt")
        theirs = self.store("theirs.txt", user=self.other)
        trashed = self.store("trashed.txt")
        services.create_share(self.owner, mine)
        other_share = services.create_share(self.other, theirs)
        services.create_share(self.owner, trashed)
        services.soft_delete_file(self.owner, trashed)

        response = self.client_for(self.owner).get(reverse("filemanager:shares"))

        self.assertContains(response, "mine.txt")
        self.assertNotContains(response, other_share.token)
        self.assertNotContains(response, "trashed.txt")

    def test_revoking_someone_elses_share_is_a_404(self):
        theirs = self.store(user=self.other)
        share = services.create_share(self.other, theirs)

        response = self.client_for(self.owner).post(
            reverse("filemanager:revoke_share", args=[share.pk])
        )

        self.assertEqual(response.status_code, 404)
        share.refresh_from_db()
        self.assertTrue(share.is_active)

    def test_public_page_shows_file_facts_expiry_and_password_field(self):
        item = self.store("public.txt", b"12345")
        share = services.create_share(
            self.owner, item, expires_hours=24, max_downloads=5, password="pw-12345"
        )

        response = self.client.get(
            reverse("filemanager:shared_file", kwargs={"token": share.token})
        )

        self.assertContains(response, "public.txt")
        self.assertContains(response, "5 B")
        self.assertContains(response, "Expires")
        self.assertContains(response, "Downloads left")
        self.assertContains(response, 'type="password"')
        self.assertContains(response, "noindex")

    def test_public_page_for_a_revoked_link_is_a_friendly_404(self):
        item = self.store()
        share = services.create_share(self.owner, item)
        services.revoke_share(self.owner, share)

        response = self.client.get(
            reverse("filemanager:shared_file", kwargs={"token": share.token})
        )

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Link not available", status_code=404)


class AccessControlTests(FileUiTestCase):
    def test_new_post_routes_are_csrf_protected(self):
        item = self.store()
        trashed = self.store("t.txt")
        services.soft_delete_file(self.owner, trashed)
        folder = services.create_folder(self.owner, "F", None)
        client = self.client_for(self.owner, enforce_csrf_checks=True)
        routes = (
            reverse("filemanager:rename_folder", args=[folder.pk]),
            reverse("filemanager:delete_folder", args=[folder.pk]),
            reverse("filemanager:move_file", args=[item.pk]),
            reverse("filemanager:set_tags", args=[item.pk]),
            reverse("filemanager:restore_file", args=[trashed.pk]),
            reverse("filemanager:purge_file", args=[trashed.pk]),
            reverse("filemanager:empty_trash"),
            reverse("filemanager:upload_version", args=[item.pk]),
        )

        for url in routes:
            with self.subTest(url=url):
                self.assertEqual(client.post(url).status_code, 403)

    def test_state_changing_routes_reject_get(self):
        item = self.store()
        client = self.client_for(self.owner)
        routes = (
            reverse("filemanager:move_file", args=[item.pk]),
            reverse("filemanager:set_tags", args=[item.pk]),
            reverse("filemanager:restore_file", args=[item.pk]),
            reverse("filemanager:purge_file", args=[item.pk]),
            reverse("filemanager:empty_trash"),
        )

        for url in routes:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 405)

    def test_every_new_page_requires_login(self):
        item = self.store()
        for url in (
            reverse("filemanager:trash"),
            reverse("filemanager:shares"),
            reverse("filemanager:file_detail", args=[item.pk]),
            reverse("filemanager:thumbnail", args=[item.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response["Location"])

    def test_detail_page_of_someone_elses_file_is_a_404(self):
        item = self.store()

        response = self.client_for(self.other).get(
            reverse("filemanager:file_detail", args=[item.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_readers_see_the_detail_page_without_write_controls(self):
        shared = services.create_folder(self.owner, "Shared", None)
        FolderPermission.objects.create(folder=shared, user=self.other, can_read=True)
        item = self.store("shared.txt", folder=shared)

        response = self.client_for(self.other).get(
            reverse("filemanager:file_detail", args=[item.pk])
        )

        self.assertContains(response, "shared.txt")
        self.assertNotContains(
            response, reverse("filemanager:rename_file", args=[item.pk])
        )
        self.assertNotContains(
            response, reverse("filemanager:upload_version", args=[item.pk])
        )
        self.assertNotContains(
            response, reverse("filemanager:create_share", args=[item.pk])
        )

    def test_trashed_files_have_no_detail_page(self):
        item = self.store()
        services.soft_delete_file(self.owner, item)

        response = self.client_for(self.owner).get(
            reverse("filemanager:file_detail", args=[item.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_unverified_users_cannot_upload_versions_or_share(self):
        user = get_user_model().objects.create_user(
            username="unverified_fm",
            email="unverified-fm@example.com",
            password=PASSWORD,
        )
        item = services.store_upload(user, upload("u.txt", b"x"), None)
        client = self.client_for(user)

        version = client.post(
            reverse("filemanager:upload_version", args=[item.pk]),
            {"file": upload("u.txt", b"y")},
        )
        share = client.post(reverse("filemanager:create_share", args=[item.pk]))

        self.assertIn("Verify your email", " ".join(self.messages(version)))
        self.assertIn("Verify your email", " ".join(self.messages(share)))
        self.assertFalse(item.versions.exists())
        self.assertFalse(FileShare.objects.exists())


class DashboardTileTests(FileUiTestCase):
    def test_account_dashboard_shows_storage_usage(self):
        self.store("a.txt", b"12345")

        response = self.client_for(self.owner).get(reverse("users:dashboard"))

        self.assertContains(response, reverse("filemanager:browser"))
        self.assertContains(response, "File storage")
        self.assertContains(response, "5 B of")

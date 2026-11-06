from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.filemanager import services
from apps.filemanager.models import FileItem, FileShare
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    make_user,
    ready,
    session_context,
    visit_all,
    watch,
)


@pytest.fixture
def file_root(settings, tmp_path: Path) -> Path:
    settings.FILEMANAGER_ROOT = tmp_path / "filemanager"
    return settings.FILEMANAGER_ROOT


@pytest.mark.django_db(transaction=True)
def test_file_manager_pages_have_no_console_csp_or_overflow_problems(
    live_server, file_root
):
    owner = make_user("fm_pages_owner")
    folder = services.create_folder(owner, "Firmware backups", None)
    upload = _upload("backup.txt", b"backup contents")
    item = services.store_upload(owner, upload, folder)
    cookie = login_cookie(owner)
    paths = [
        reverse("filemanager:browser"),
        f"{reverse('filemanager:browser')}?folder={folder.pk}",
        reverse("filemanager:file_detail", kwargs={"pk": item.pk}),
        reverse("filemanager:shares"),
        reverse("filemanager:trash"),
    ]
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        visit_all(page, live_server, paths, failures)

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_upload_rename_move_trash_and_restore_through_the_interface(
    live_server, file_root
):
    owner = make_user("fm_flow_owner")
    services.create_folder(owner, "Archive", None)
    cookie = login_cookie(owner)
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('filemanager:browser')}")
        ready(page)
        assert page.get_by_text("No files here yet").is_visible()

        page.set_input_files(
            "#upload-files",
            {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello files"},
        )
        assert page.get_by_text("1 file(s) ready: notes.txt").is_visible()
        page.get_by_role("button", name="Upload", exact=True).click()
        page.get_by_text("Uploaded 1 file(s).").wait_for(timeout=10_000)
        page.get_by_role("link", name="notes.txt").first.click()
        page.wait_for_url("**/files/*/")

        page.get_by_label("File name").fill("renamed.txt")
        page.get_by_role("button", name="Rename", exact=True).click()
        assert page.get_by_role("heading", name="renamed.txt", level=1).is_visible()

        page.get_by_label("Destination folder").select_option(label="Archive")
        page.get_by_role("button", name="Move", exact=True).click()
        page.get_by_text("File moved.").wait_for(timeout=5_000)
        assert page.get_by_role("heading", name="Archive", level=1).is_visible()
        page.get_by_role("link", name="renamed.txt").first.click()
        page.wait_for_url("**/files/*/")

        page.get_by_role("button", name="Move to trash").click()
        page.get_by_text("File moved to the trash.").wait_for(timeout=5_000)
        page.goto(f"{live_server.url}{reverse('filemanager:trash')}")
        ready(page)
        assert page.get_by_role("cell", name="renamed.txt", exact=True).is_visible()
        page.get_by_role("button", name="Restore").click()
        page.get_by_text("File restored.").wait_for(timeout=5_000)
        page.wait_for_url("**/files/*/")
        assert page.get_by_role("heading", name="renamed.txt", level=1).is_visible()
        page.goto(f"{live_server.url}{reverse('filemanager:trash')}")
        assert page.get_by_text("The trash is empty").is_visible()

    assert not failures, "\n".join(failures)
    item = FileItem.objects.get()
    assert item.name == "renamed.txt"
    assert item.folder is not None and item.folder.name == "Archive"
    assert item.deleted_at is None


@pytest.mark.django_db(transaction=True)
def test_share_link_opens_in_a_signed_out_browser_and_revoking_ends_access(
    live_server, file_root
):
    owner = make_user("fm_share_owner")
    item = services.store_upload(owner, _upload("shared.txt", b"public bytes"), None)
    cookie = login_cookie(owner)
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        detail_url = reverse("filemanager:file_detail", kwargs={"pk": item.pk})
        page.goto(f"{live_server.url}{detail_url}")
        ready(page)
        page.get_by_role("button", name="Create link").click()
        link = page.locator(".fm-share-link").first
        link.wait_for(timeout=5_000)
        share_url = link.input_value()

        anonymous = browser.new_context().new_page()
        watch(anonymous, failures)
        anonymous.goto(share_url)
        assert anonymous.get_by_role("heading", name="shared.txt").is_visible()
        assert anonymous.get_by_role("button", name="Download").is_visible()

        page.get_by_role("button", name="Revoke", exact=True).first.click()
        page.get_by_role("button", name="Revoke link").click()
        page.get_by_text("No active share links").wait_for(timeout=5_000)
        anonymous.goto(share_url)
        assert anonymous.get_by_role("heading", name="Link not available").is_visible()

    assert not failures, "\n".join(failures)
    assert FileShare.objects.filter(file_item=item, is_active=True).count() == 0


@pytest.mark.django_db(transaction=True)
def test_anonymous_visitors_are_sent_to_sign_in_for_the_file_manager(live_server):
    with chromium() as browser:
        page = browser.new_page()
        page.goto(f"{live_server.url}{reverse('filemanager:browser')}")
        assert "/login" in page.url


def _upload(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="text/plain")

"""Browser checks for the staff tool pages of tags, comments and storage.

They render in the Unfold admin shell (sidebar, admin CSP) and open app-owned
dialogs: no console/CSP errors, no overflow, dialogs open, close with Escape and
confirm.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from playwright.sync_api import Page, expect

from apps.blog.models import Post, PostStatus
from apps.comments.models import Comment
from apps.tags.models import Tag
from tests.frontend.browser_support import (
    chromium,
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    ready,
    session_context,
    visit_all,
    watch,
)


def staff_user(username: str):
    user = make_user(username, staff=True)
    user.user_permissions.add(
        *Permission.objects.filter(
            codename__in=[
                "view_tag",
                "change_tag",
                "view_comment",
                "change_comment",
            ]
        )
    )
    return user


def seed_content(author) -> tuple[Tag, Tag, Tag, Comment, Comment]:
    suggestion = Tag.objects.create(name="Browser suggestion", ai_suggested=True)
    source = Tag.objects.create(name="Browser source", is_curated=True)
    target = Tag.objects.create(name="Browser target", is_curated=True)
    post = Post.objects.create(
        title="Browser moderated post",
        body="<p>A post with enough words for the browser moderation test.</p>",
        author=author,
        status=PostStatus.PUBLISHED,
    )
    post.tags.add(source)
    rejected = Comment.objects.create(
        post=post, user=author, body="Reject me please", status="pending"
    )
    spam = Comment.objects.create(
        post=post, user=author, body="Buy cheap watches", status="pending"
    )
    return suggestion, source, target, rejected, spam


@pytest.fixture(name="firmware_root")
def firmware_root_fixture(settings, tmp_path: Path) -> Path:
    settings.FIRMWARE_STORAGE_ROOT = tmp_path.resolve()
    return settings.FIRMWARE_STORAGE_ROOT


def open_dialog(page: Page, trigger_name: str):
    page.get_by_role("link", name=trigger_name).first.click()
    dialog = page.locator("dialog[data-staff-dialog]")
    expect(dialog).to_be_visible(timeout=10_000)
    return dialog


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("firmware_root")
def test_staff_pages_render_in_the_admin_shell_without_problems(live_server):
    user = staff_user("staffpages_shell")
    suggestion, source, target, _, _ = seed_content(user)
    cookie = login_cookie(user)
    paths = [
        reverse("tags:manage"),
        reverse("tags:duplicates_review"),
        reverse("tags:keyword_review"),
        f"{reverse('tags:merge')}?source={source.slug}&target={target.slug}",
        reverse("comments:moderation_queue"),
        reverse("storage:status"),
        reverse("tags:reject_dialog", kwargs={"tag_id": suggestion.pk}),
    ]
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        visit_all(page, live_server, paths, failures)
        page.set_viewport_size({"width": 1365, "height": 900})
        for path in paths:
            page.goto(f"{live_server.url}{path}")
            ready(page)
            if page.locator("nav a[href]").count() < 3:
                failures.append(f"{path}: admin sidebar is missing")
            if page.locator(".site-header, .site-container").count():
                failures.append(f"{path}: public shell markup rendered")

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_tag_dialogs_open_close_with_escape_and_confirm(live_server):
    user = staff_user("staffpages_tags")
    suggestion, source, target, _, _ = seed_content(user)
    cookie = login_cookie(user)
    failures: list[str] = []
    native: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, native)
        page.goto(f"{live_server.url}{reverse('tags:manage')}")
        ready(page)

        dialog = open_dialog(page, "Reject Browser suggestion")
        expect(dialog.get_by_role("heading")).to_have_text("Reject this suggested tag?")
        page.keyboard.press("Escape")
        expect(page.locator("dialog[data-staff-dialog]")).to_have_count(0)

        open_dialog(page, "Reject Browser suggestion")
        page.get_by_role("button", name="Reject tag").click()
        page.wait_for_url(f"**{reverse('tags:manage')}")
        expect(page.get_by_text("Rejected “Browser suggestion”.")).to_be_visible()
        expect(page.get_by_role("rowheader", name="Browser suggestion")).to_have_count(
            0
        )

        page.get_by_label("Source tag slug").fill(source.slug)
        page.get_by_label("Destination tag slug").fill(target.slug)
        page.get_by_role("button", name="Review merge").click()
        merge = page.locator("dialog[data-staff-dialog]")
        expect(merge).to_be_visible(timeout=10_000)
        expect(merge).to_contain_text("This will change 1 article")
        width = overflow(page)
        assert width["document"] <= width["viewport"] + 1
        merge.get_by_role("button", name="Cancel").click()
        expect(page.locator("dialog[data-staff-dialog]")).to_have_count(0)
        page.get_by_role("button", name="Review merge").click()
        page.get_by_role("button", name="Merge tags").click()
        page.wait_for_url(f"**{reverse('tags:manage')}")
        expect(
            page.get_by_text("Merged “Browser source” into “Browser target”.")
        ).to_be_visible()

    assert not failures, "\n".join(failures)
    assert not native
    suggestion.refresh_from_db()
    assert not suggestion.is_active
    source.refresh_from_db()
    assert source.merge_into_id == target.pk


@pytest.mark.django_db(transaction=True)
def test_comment_moderation_dialogs_record_the_decision(live_server):
    user = staff_user("staffpages_comments")
    _, _, _, rejected, spam = seed_content(user)
    cookie = login_cookie(user)
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('comments:moderation_queue')}")
        ready(page)

        article = page.locator("article", has_text="Reject me please")
        article.get_by_role("link", name="Reject").click()
        dialog = page.locator("dialog[data-staff-dialog]")
        expect(dialog).to_be_visible(timeout=10_000)
        expect(dialog).to_contain_text("Reject me please")
        dialog.get_by_label("Note for the record (optional)").fill("Off topic")
        dialog.get_by_role("button", name="Reject", exact=True).click()
        page.wait_for_url(f"**{reverse('comments:moderation_queue')}")
        expect(page.get_by_text("Decision recorded.")).to_be_visible()

        page.locator("article", has_text="Buy cheap watches").get_by_role(
            "link", name="Mark as spam"
        ).click()
        expect(page.locator("dialog[data-staff-dialog]")).to_be_visible(timeout=10_000)
        page.keyboard.press("Escape")
        expect(page.locator("dialog[data-staff-dialog]")).to_have_count(0)

    assert not failures, "\n".join(failures)
    rejected.refresh_from_db()
    spam.refresh_from_db()
    assert rejected.status == Comment.Status.REJECTED
    assert rejected.moderation_flags["decision"]["note"] == "Off topic"
    assert spam.status == "pending"

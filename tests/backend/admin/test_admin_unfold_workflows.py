"""Unfold row/detail actions for blog, comments, tags, devices and ads.

Each action link opens a dialog (GET) and changes state only on its CSRF-protected
POST, which answers with an ``HX-Redirect`` back to the page it was opened from.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ads.models import AdPlacement, AutoAdsScanResult, ScanDiscovery
from apps.blog.models import Post, PostStatus
from apps.comments.models import Comment
from apps.devices.models import Device, DeviceEvent
from apps.tags.models import Tag

DIALOG = {"_form_submitted": "on"}


def _user(username: str, *, staff: bool = False, perms: tuple[str, ...] = ()):
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.test",
        is_staff=staff,
        email_verified_at=timezone.now(),
    )
    for codename in perms:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def superuser(db):
    return get_user_model().objects.create_superuser(
        username="wf-super", email="wf-super@example.test"
    )


def _url(name: str, *args: object) -> str:
    return reverse(f"admin:{name}", args=args)


def _post(author, **extra) -> Post:
    return Post.objects.create(
        title="Workflow post",
        body="<p>Body long enough for the workflow.</p>",
        author=author,
        **extra,
    )


# -- blog ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_blog_action_opens_a_dialog_and_does_not_change_state_on_get(superuser):
    post = _post(superuser)

    response = _client(superuser).get(_url("blog_post_publish", post.pk))

    assert response.status_code == 200
    assert b"Publish this post?" in response.content
    post.refresh_from_db()
    assert post.status == PostStatus.DRAFT


@pytest.mark.django_db
def test_blog_publish_posts_through_the_dialog_and_redirects_back(superuser):
    post = _post(superuser)

    response = _client(superuser).post(_url("blog_post_publish", post.pk), DIALOG)

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == _url("blog_post_change", post.pk)
    post.refresh_from_db()
    assert post.status == PostStatus.PUBLISHED
    assert post.is_published


@pytest.mark.django_db
def test_blog_request_review_and_archive_follow_the_workflow(superuser):
    post = _post(superuser)
    client = _client(superuser)

    client.post(_url("blog_post_request_review", post.pk), DIALOG)
    post.refresh_from_db()
    assert post.status == PostStatus.IN_REVIEW

    client.post(_url("blog_post_archive", post.pk), DIALOG)
    post.refresh_from_db()
    assert post.status == PostStatus.ARCHIVED


@pytest.mark.django_db
def test_blog_schedule_requires_a_time_and_records_it(superuser):
    post = _post(superuser)
    client = _client(superuser)
    url = _url("blog_post_schedule", post.pk)

    missing = client.post(url, DIALOG)
    assert b"This field is required" in missing.content
    post.refresh_from_db()
    assert post.status == PostStatus.DRAFT

    when = timezone.now() + timedelta(days=2)
    response = client.post(
        url,
        {
            **DIALOG,
            "publish_at_0": when.strftime("%Y-%m-%d"),
            "publish_at_1": when.strftime("%H:%M:%S"),
        },
    )
    assert "HX-Redirect" in response.headers
    post.refresh_from_db()
    assert post.status == PostStatus.SCHEDULED
    assert post.publish_at is not None


@pytest.mark.django_db
def test_blog_actions_reject_staff_without_the_change_permission():
    viewer = _user("blog-viewer", staff=True, perms=("view_post",))
    post = _post(viewer)

    response = _client(viewer).post(_url("blog_post_publish", post.pk), DIALOG)

    assert response.status_code == 403
    post.refresh_from_db()
    assert post.status == PostStatus.DRAFT


@pytest.mark.django_db
def test_blog_invalid_transition_reports_an_error_message(superuser):
    post = _post(superuser, status=PostStatus.PUBLISHED)
    client = _client(superuser)

    response = client.post(_url("blog_post_publish", post.pk), DIALOG)

    assert "HX-Redirect" in response.headers
    follow = client.get(_url("blog_post_change", post.pk))
    assert b"cannot be published" in follow.content


@pytest.mark.django_db
def test_blog_actions_require_post_for_state_changes_with_csrf_enforced(superuser):
    post = _post(superuser)
    client = Client(enforce_csrf_checks=True)
    client.force_login(superuser)

    response = client.post(_url("blog_post_publish", post.pk), DIALOG)

    assert response.status_code == 403
    post.refresh_from_db()
    assert post.status == PostStatus.DRAFT


# -- comments -------------------------------------------------------------------------
@pytest.fixture
def pending_comment(superuser):
    post = _post(superuser, status=PostStatus.PUBLISHED)
    return Comment.objects.create(
        post=post, user=superuser, body="Please moderate me", status="pending"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("approve", Comment.Status.APPROVED),
        ("reject", Comment.Status.REJECTED),
        ("spam", Comment.Status.SPAM),
    ],
)
def test_comment_decision_dialogs_record_status_and_note(
    superuser, pending_comment, action, expected
):
    response = _client(superuser).post(
        _url(f"comments_comment_{action}", pending_comment.pk),
        {**DIALOG, "note": "Reviewed in admin"},
    )

    assert "HX-Redirect" in response.headers
    pending_comment.refresh_from_db()
    assert pending_comment.status == expected
    decision = pending_comment.moderation_flags["decision"]
    assert decision["note"] == "Reviewed in admin"
    assert decision["action"] == action


@pytest.mark.django_db
def test_comment_moderation_needs_the_change_permission(pending_comment):
    viewer = _user("comment-viewer", staff=True, perms=("view_comment",))

    response = _client(viewer).post(
        _url("comments_comment_approve", pending_comment.pk), DIALOG
    )

    assert response.status_code == 403
    pending_comment.refresh_from_db()
    assert pending_comment.status == Comment.Status.PENDING


@pytest.mark.django_db
def test_comment_bulk_soft_delete_stamps_who_and_when(superuser, pending_comment):
    _client(superuser).post(
        reverse("admin:comments_comment_changelist"),
        {
            "action": "soft_delete",
            "_selected_action": [str(pending_comment.pk)],
            "index": "0",
        },
    )

    pending_comment.refresh_from_db()
    assert pending_comment.is_deleted
    assert pending_comment.deleted_by_id == superuser.pk
    assert pending_comment.deleted_at is not None


# -- tags -----------------------------------------------------------------------------
@pytest.mark.django_db
def test_tag_detail_approve_and_reject(superuser):
    tag = Tag.objects.create(name="Suggested", ai_suggested=True, is_curated=False)
    client = _client(superuser)

    client.post(_url("tags_tag_approve", tag.pk), DIALOG)
    tag.refresh_from_db()
    assert tag.is_curated
    assert tag.is_active

    client.post(_url("tags_tag_reject", tag.pk), DIALOG)
    tag.refresh_from_db()
    assert not tag.is_curated
    assert not tag.is_active


@pytest.mark.django_db
def test_tag_merge_moves_posts_and_retires_the_source(superuser):
    source = Tag.objects.create(name="Dupe tag")
    target = Tag.objects.create(name="Kept tag")
    post = _post(superuser)
    post.tags.add(source)

    response = _client(superuser).post(
        _url("tags_tag_merge", source.pk), {**DIALOG, "target": target.slug}
    )

    assert "HX-Redirect" in response.headers
    source.refresh_from_db()
    assert source.merge_into_id == target.pk
    assert source.is_deleted
    assert list(post.tags.all()) == [target]


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["", "does-not-exist"])
def test_tag_merge_rejects_missing_or_unknown_targets(superuser, target):
    source = Tag.objects.create(name="Lonely tag")

    response = _client(superuser).post(
        _url("tags_tag_merge", source.pk), {**DIALOG, "target": target}
    )

    assert "HX-Redirect" not in response.headers
    source.refresh_from_db()
    assert source.merge_into_id is None


@pytest.mark.django_db
def test_tag_merge_rejects_merging_a_tag_into_itself(superuser):
    source = Tag.objects.create(name="Selfish tag")

    response = _client(superuser).post(
        _url("tags_tag_merge", source.pk), {**DIALOG, "target": source.slug}
    )

    assert b"cannot be merged into itself" in response.content
    source.refresh_from_db()
    assert source.merge_into_id is None


@pytest.mark.django_db
def test_tag_actions_need_the_change_permission():
    viewer = _user("tag-viewer", staff=True, perms=("view_tag",))
    tag = Tag.objects.create(name="Guarded", ai_suggested=True, is_curated=False)

    response = _client(viewer).post(_url("tags_tag_approve", tag.pk), DIALOG)

    assert response.status_code == 403
    tag.refresh_from_db()
    assert not tag.is_curated


# -- devices --------------------------------------------------------------------------
@pytest.fixture
def device(superuser):
    return Device.objects.create(
        user=superuser, browser_key_hash="k" * 43, display_name="Test browser"
    )


@pytest.mark.django_db
def test_device_block_unblock_and_revoke_dialogs_write_audit_events(superuser, device):
    client = _client(superuser)

    client.post(_url("devices_device_block", device.pk), DIALOG)
    device.refresh_from_db()
    assert device.is_blocked

    client.post(_url("devices_device_unblock", device.pk), DIALOG)
    device.refresh_from_db()
    assert not device.is_blocked

    client.post(_url("devices_device_revoke", device.pk), DIALOG)
    device.refresh_from_db()
    assert device.revoked_at is not None
    reasons = set(
        DeviceEvent.objects.filter(device=device).values_list("reason", flat=True)
    )
    assert {"blocked_by_admin", "unblocked_by_admin", "revoked_by_admin"} <= reasons


@pytest.mark.django_db
def test_device_actions_need_the_change_permission(device):
    viewer = _user("device-viewer", staff=True, perms=("view_device",))

    response = _client(viewer).post(_url("devices_device_block", device.pk), DIALOG)

    assert response.status_code == 403
    device.refresh_from_db()
    assert not device.is_blocked


@pytest.mark.django_db
def test_device_quota_and_config_stay_superuser_only(device):
    manager = _user(
        "quota-manager",
        staff=True,
        perms=("view_userdevicequota", "change_userdevicequota", "add_userdevicequota"),
    )
    client = _client(manager)

    assert client.get(_url("devices_userdevicequota_changelist")).status_code == 200
    assert client.get(_url("devices_userdevicequota_add")).status_code == 403


# -- ads ------------------------------------------------------------------------------
@pytest.fixture
def discovery(db):
    scan = AutoAdsScanResult.objects.create(template_path="blog/post_detail.html")
    return ScanDiscovery.objects.create(
        scan_result=scan,
        placement_code="blog-post-detail-sidebar-top",
        placement_name="Post sidebar top",
    )


@pytest.mark.django_db
def test_scan_discovery_approval_creates_the_placement(superuser, discovery):
    response = _client(superuser).post(
        _url("ads_scandiscovery_approve", discovery.pk), DIALOG
    )

    assert "HX-Redirect" in response.headers
    discovery.refresh_from_db()
    assert discovery.status == "approved"
    assert discovery.reviewed_by_id == superuser.pk
    assert AdPlacement.objects.filter(pk=discovery.placement_id).exists()


@pytest.mark.django_db
def test_scan_discovery_rejection_and_double_review(superuser, discovery):
    client = _client(superuser)

    client.post(_url("ads_scandiscovery_reject", discovery.pk), DIALOG)
    discovery.refresh_from_db()
    assert discovery.status == "rejected"
    assert discovery.placement_id is None

    client.post(_url("ads_scandiscovery_approve", discovery.pk), DIALOG)
    discovery.refresh_from_db()
    assert discovery.status == "rejected"


@pytest.mark.django_db
def test_scan_discovery_status_cannot_be_edited_directly(superuser, discovery):
    response = _client(superuser).get(reverse("admin:ads_scandiscovery_changelist"))

    assert response.status_code == 200
    assert b'name="form-0-status"' not in response.content

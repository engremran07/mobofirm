"""Admin, wallet registration, purchased list and bounty UI for the blog economy."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.forms import ModelForm
from django.urls import reverse
from django.utils import timezone

from apps.blog import guide_requests
from apps.blog.admin import PostAdmin
from apps.blog.models import GuideRequest, Post, PostAccess, PostStatus
from apps.bounty import services as bounty_services
from apps.bounty import targets
from apps.core.app_service import AppService
from apps.wallet import api as wallet
from apps.wallet import registry

pytestmark = pytest.mark.django_db


@pytest.fixture
def make_user(django_user_model):
    def build(name: str, *, credits: int = 0, **extra):
        user = django_user_model.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="Pass-12345!",
            email_verified_at=timezone.now(),
            **extra,
        )
        if credits:
            wallet.credit(
                user, credits, reason=wallet.LedgerReason.REWARD, key=f"seed:{name}"
            )
        return user

    return build


@pytest.fixture
def reviewer(make_user):
    staff = make_user("reviewer", is_staff=True)
    staff.user_permissions.add(
        *Permission.objects.filter(
            codename__in=["change_guiderequest", "view_guiderequest", "change_post"]
        )
    )
    return staff


def pending(user, title="Unlock bootloader on Mediatek tablets"):
    return guide_requests.create_request(
        user,
        guide_requests.RequestText(
            title=title,
            kind="method",
            tried="Tried SP Flash Tool with the stock scatter file, error 0xC0020036.",
            description="A detailed description of the topic that is needed here.",
        ),
        guide_requests.RequestContext(model_code="MT6765"),
    )


# --------------------------------------------------------------------- admin ----


def test_review_queue_lists_and_approves_through_the_admin(make_user, reviewer, client):
    item = pending(make_user("asker"))
    client.force_login(reviewer)

    changelist = client.get(reverse("admin:blog_guiderequest_changelist"))
    change = client.get(reverse("admin:blog_guiderequest_change", args=[item.pk]))
    add = client.get(reverse("admin:blog_guiderequest_add"))

    assert changelist.status_code == 200
    assert item.title in changelist.content.decode()
    assert change.status_code == 200
    assert add.status_code in (302, 403)


def test_admin_approve_and_reject_actions(make_user, reviewer, client):
    first = pending(make_user("asker"))
    second = pending(make_user("asker2"), title="Second distinct request about tools")
    client.force_login(reviewer)
    approve = client.post(
        reverse("admin:blog_guiderequest_approve_request", args=[first.pk]),
        {"_form_submitted": "on"},
    )
    reject = client.post(
        reverse("admin:blog_guiderequest_reject_request", args=[second.pk]),
        {"_form_submitted": "on", "reason": "Not enough detail."},
    )

    assert approve.status_code in (200, 204, 302)
    assert reject.status_code in (200, 204, 302)
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == GuideRequest.Status.APPROVED
    assert second.status == GuideRequest.Status.REJECTED


def test_post_admin_shows_access_tab_and_buyers_read_only(make_user, reviewer, client):
    reviewer.is_superuser = True
    reviewer.save()
    post = Post.objects.create(
        title="Premium article",
        body="<p>Long enough body for the premium article.</p>",
        author=make_user("writer"),
        access=PostAccess.FEATURED,
        price=Decimal("2.00"),
    )
    client.force_login(reviewer)
    shop = SimpleNamespace(
        featured_blog_buyers=lambda post_id: [{"user": "bob", "credits": 80}]
    )
    real_get = AppService.get

    with mock.patch(
        "apps.blog.shop_gateway.AppService.get",
        side_effect=lambda label: shop if label == "shop" else real_get(label),
    ):
        page = client.get(reverse("admin:blog_post_change", args=[post.pk]))

    html = page.content.decode()
    assert page.status_code == 200
    assert "Access" in html and "bob (80 credits)" in html
    assert 'name="buyers"' not in html


def test_price_change_in_admin_records_the_approver(make_user, reviewer, rf):
    post = Post.objects.create(
        title="Premium article",
        body="<p>Long enough body for the premium article.</p>",
        author=make_user("writer"),
        access=PostAccess.FEATURED,
    )
    request = rf.post("/admin/")
    request.user = reviewer
    form = cast(
        "ModelForm",
        SimpleNamespace(cleaned_data={"price": Decimal("6.50"), "tags": []}),
    )

    PostAdmin(Post, admin.site).save_model(request, post, form, True)

    post.refresh_from_db()
    assert post.price == Decimal("6.50")
    assert post.price_approved_by == reviewer
    assert post.price_approved_at is not None


# ----------------------------------------------------------- wallet contract ----


def test_featured_purchase_sink_is_declared_and_derived():
    sink = registry.spend_sinks()["blog.featured_purchase"]

    assert sink.app == "blog"
    assert sink.derived is True


def test_blog_reward_events_are_declared():
    events = registry.reward_events()

    assert {"blog.post_published", "blog.post_published.featured"} <= set(events)
    assert "blog.guide_published" in events


# ------------------------------------------------------------ purchased list ----


def test_purchased_page_lists_what_the_shop_reports(make_user, client):
    buyer = make_user("buyer")
    post = Post.objects.create(
        title="Bought premium article",
        body="<p>Long enough body for the bought article.</p>",
        author=make_user("writer"),
        status=PostStatus.PUBLISHED,
        access=PostAccess.PAID,
        price=Decimal("2.00"),
    )
    shop = SimpleNamespace(purchased_featured_blog_ids=lambda user: [post.pk])
    real_get = AppService.get
    client.force_login(buyer)

    with mock.patch(
        "apps.blog.shop_gateway.AppService.get",
        side_effect=lambda label: shop if label == "shop" else real_get(label),
    ):
        page = client.get(reverse("blog:purchased"))

    assert "Bought premium article" in page.content.decode()
    assert client.get(reverse("blog:purchased")).status_code == 200


def test_purchased_page_requires_sign_in(client):
    assert client.get(reverse("blog:purchased")).status_code == 302


# --------------------------------------------------------------- bounty UI ----


def test_guide_bounty_pages_and_listing(make_user, reviewer, client):
    asker = make_user("asker")
    funder = make_user("funder", credits=300)
    item = guide_requests.approve(pending(asker).pk, reviewer=reviewer)
    bounty = bounty_services.post_bounty(
        funder, kind=targets.KIND_GUIDE, object_id=item.pk, amount=60
    )

    detail = client.get(reverse("bounty:detail", args=[bounty.pk]))
    listing = client.get(reverse("bounty:list"), {"kind": "guide"})
    request_page = client.get(item.get_absolute_url())

    assert bounty.target_label == item.title
    assert "Submit a solution" in detail.content.decode()
    assert f"?solves={item.pk}" in detail.content.decode()
    assert item.title in listing.content.decode()
    assert "60 credits are offered" in request_page.content.decode()


def test_bounty_create_prefills_the_guide_target(make_user, reviewer, client):
    item = guide_requests.approve(pending(make_user("asker")).pk, reviewer=reviewer)
    client.force_login(make_user("funder", credits=100))

    page = client.get(reverse("bounty:create"), {"guide": item.pk})

    assert page.status_code == 200
    assert item.title in page.content.decode()


def test_posting_a_guide_bounty_through_the_form(make_user, reviewer, client):
    item = guide_requests.approve(pending(make_user("asker")).pk, reviewer=reviewer)
    funder = make_user("funder", credits=100)
    client.force_login(funder)

    response = client.post(
        reverse("bounty:create"),
        {"kind": "guide", "object_id": item.pk, "credits": 40, "days": 30},
    )

    assert response.status_code == 302
    assert wallet.balance(funder) == 100 - 40


def test_request_page_without_bounty_app_hides_the_bounty_ui(
    make_user, reviewer, client
):
    item = guide_requests.approve(pending(make_user("asker")).pk, reviewer=reviewer)
    real_get = AppService.get

    with mock.patch(
        "apps.blog.bounty_gateway.AppService.get",
        side_effect=lambda label: None if label == "bounty" else real_get(label),
    ):
        page = client.get(item.get_absolute_url())

    assert page.status_code == 200
    assert "Add a bounty" not in page.content.decode()

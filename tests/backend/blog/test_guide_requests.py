"""Guide requests: gating, state machine, throttling, duplicates, solutions, settlement."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest
from django.apps import apps
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.blog import guide_requests
from apps.blog.models import (
    BlogSettings,
    GuideRequest,
    Post,
    PostAccess,
    PostStatus,
)
from apps.blog.services import workflow
from apps.bounty import services as bounty_services
from apps.bounty import targets
from apps.bounty.models import Bounty
from apps.core.app_service import AppService
from apps.core.events.bus import event_bus
from apps.firmwares.models import Brand, Model
from apps.wallet import api as wallet

pytestmark = pytest.mark.django_db
Status = GuideRequest.Status
VALID = {
    "title": "How to bypass FRP on Acme Phone One",
    "kind": "method",
    "description": "The phone is locked after a reset and I need the exact steps.",
}


@pytest.fixture
def make_user(django_user_model):
    def build(name: str, *, verified: bool = True, credits: int = 0, **extra):
        user = django_user_model.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="Pass-12345!",
            email_verified_at=timezone.now() if verified else None,
            **extra,
        )
        if credits:
            wallet.credit(
                user, credits, reason=wallet.LedgerReason.REWARD, key=f"seed:{name}"
            )
        return user

    return build


@pytest.fixture
def requester(make_user):
    return make_user("requester")


@pytest.fixture
def reviewer(make_user):
    staff = make_user("reviewer", is_staff=True)
    staff.user_permissions.add(
        Permission.objects.get(codename="change_guiderequest"),
        Permission.objects.get(codename="change_post"),
    )
    return staff


@pytest.fixture
def notifications():
    with mock.patch(
        "apps.core.services.notifications.notification_service.send"
    ) as send:
        yield send


def file_request(user, **overrides):
    data = {**VALID, **overrides}
    return guide_requests.create_request(
        user,
        title=data["title"],
        description=data["description"],
        kind=data["kind"],
        brand=data.get("brand"),
        firmware_model=data.get("firmware_model"),
        chipset=data.get("chipset"),
    )


@pytest.fixture
def approved_request(requester, reviewer):
    item = file_request(requester)
    return guide_requests.approve(item.pk, reviewer=reviewer)


# ------------------------------------------------------------------ creation ----


def test_anonymous_cannot_open_the_form(client):
    response = client.get(reverse("blog:request_create"))

    assert response.status_code == 302
    assert "login" in response["Location"]


def test_unverified_member_cannot_request(make_user, client):
    user = make_user("unverified", verified=False)
    client.force_login(user)

    response = client.post(reverse("blog:request_create"), VALID)

    assert response.status_code == 200
    assert not GuideRequest.objects.exists()
    assert "Verify your email" in response.content.decode()


def test_verified_member_creates_a_pending_request(requester, client):
    client.force_login(requester)

    response = client.post(reverse("blog:request_create"), VALID)

    item = GuideRequest.objects.get()
    assert response.status_code == 302
    assert response["Location"] == item.get_absolute_url()
    assert item.status == Status.PENDING
    assert item.requester == requester


def test_model_selection_must_match_the_brand(requester):
    acme = Brand.objects.create(name="Acme", slug="acme")
    other = Brand.objects.create(name="Other", slug="other")
    model = Model.objects.create(brand=other, name="Phone", slug="phone")

    with pytest.raises(guide_requests.GuideRequestError):
        file_request(requester, brand=acme, firmware_model=model)


def test_brand_is_derived_from_the_model(requester):
    acme = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=acme, name="Phone", slug="phone")

    item = file_request(requester, firmware_model=model)

    assert item.brand == acme


def test_requests_can_be_switched_off(requester):
    config = BlogSettings.get_solo()
    config.allow_guide_requests = False
    config.save()

    with pytest.raises(guide_requests.GuideRequestError):
        file_request(requester)


# ------------------------------------------------------------------- privacy ----


def test_pending_request_is_private(requester, make_user, reviewer, client):
    item = file_request(requester)
    stranger = make_user("stranger")

    assert client.get(item.get_absolute_url()).status_code == 404
    client.force_login(stranger)
    assert client.get(item.get_absolute_url()).status_code == 404
    client.force_login(requester)
    assert client.get(item.get_absolute_url()).status_code == 200
    client.force_login(reviewer)
    assert client.get(item.get_absolute_url()).status_code == 200


def test_pending_request_is_not_listed_publicly(requester, client):
    item = file_request(requester)

    page = client.get(reverse("blog:request_list"))

    assert item.title not in page.content.decode()


def test_approved_request_is_public_and_listed(approved_request, client):
    page = client.get(reverse("blog:request_list"))
    detail = client.get(approved_request.get_absolute_url())

    assert approved_request.title in page.content.decode()
    assert detail.status_code == 200


def test_pending_request_is_not_bounty_eligible(requester, make_user):
    item = file_request(requester)
    funder = make_user("funder", credits=200)

    with pytest.raises(targets.TargetError):
        targets.resolve(targets.KIND_GUIDE, item.pk)
    with pytest.raises(bounty_services.BountyError):
        bounty_services.post_bounty(
            funder, kind=targets.KIND_GUIDE, object_id=item.pk, amount=50
        )
    assert not Bounty.objects.exists()


# ------------------------------------------------------------- state machine ----


def test_approve_is_idempotent_and_notifies_once(requester, reviewer, notifications):
    item = file_request(requester)

    first = guide_requests.approve(item.pk, reviewer=reviewer)
    again = guide_requests.approve(item.pk, reviewer=reviewer)

    assert first.status == again.status == Status.APPROVED
    assert first.reviewed_by == reviewer and first.reviewed_at is not None
    notifications.assert_called_once()
    assert notifications.call_args.args[0] == requester.pk


def test_only_reviewing_staff_can_decide(requester, make_user):
    item = file_request(requester)
    plain_staff = make_user("plain-staff", is_staff=True)

    for actor in (requester, plain_staff):
        with pytest.raises(guide_requests.GuideRequestError):
            guide_requests.approve(item.pk, reviewer=actor)
    item.refresh_from_db()
    assert item.status == Status.PENDING


def test_reject_needs_a_reason_and_tells_the_requester(
    requester, reviewer, notifications
):
    item = file_request(requester)

    with pytest.raises(guide_requests.GuideRequestError):
        guide_requests.reject(item.pk, reviewer=reviewer, reason="  ")
    rejected = guide_requests.reject(
        item.pk, reviewer=reviewer, reason="Too vague to answer."
    )

    assert rejected.status == Status.REJECTED
    assert rejected.decision_reason == "Too vague to answer."
    assert "Too vague" in notifications.call_args.args[2]


def test_terminal_states_cannot_change(requester, reviewer):
    item = file_request(requester)
    guide_requests.reject(item.pk, reviewer=reviewer, reason="No.")

    with pytest.raises(guide_requests.GuideRequestError):
        guide_requests.approve(item.pk, reviewer=reviewer)
    with pytest.raises(guide_requests.GuideRequestError):
        guide_requests.cancel(item.pk, actor=requester)


def test_requester_cancels_a_pending_request(requester, make_user):
    item = file_request(requester)

    with pytest.raises(guide_requests.GuideRequestError):
        guide_requests.cancel(item.pk, actor=make_user("other"))
    cancelled = guide_requests.cancel(item.pk, actor=requester)

    assert cancelled.status == Status.CANCELLED
    assert guide_requests.cancel(item.pk, actor=requester).status == Status.CANCELLED


def test_cancel_dialog_cancels_and_blocks_strangers(
    approved_request, requester, make_user, client
):
    url = reverse("blog:request_cancel_dialog", args=[approved_request.pk])
    client.force_login(make_user("meddler"))
    assert client.post(url).status_code == 404

    client.force_login(requester)
    response = client.post(url, HTTP_HX_REQUEST="true")

    assert response.status_code == 204
    approved_request.refresh_from_db()
    assert approved_request.status == Status.CANCELLED


def test_rejection_and_cancellation_publish_the_closed_event(
    requester, reviewer, django_capture_on_commit_callbacks
):
    seen: list[dict] = []
    event_bus.subscribe("blog.guide_request_closed", seen.append)
    try:
        item = file_request(requester)
        with django_capture_on_commit_callbacks(execute=True):
            guide_requests.reject(item.pk, reviewer=reviewer, reason="No.")
    finally:
        event_bus._handlers["blog.guide_request_closed"].remove(seen.append)

    assert seen and seen[0]["request_id"] == item.pk


# ---------------------------------------------------------------- throttling ----


def test_daily_limit(requester):
    config = BlogSettings.get_solo()
    config.guide_requests_per_day = 2
    config.guide_requests_open_limit = 10
    config.save()
    for index in range(2):
        file_request(requester, title=f"Unique topic number {index} zzz{index * 7}")

    with pytest.raises(guide_requests.GuideRequestError, match="per day"):
        file_request(requester, title="Completely different qqq topic here")


def test_daily_window_rolls_over(requester):
    config = BlogSettings.get_solo()
    config.guide_requests_per_day = 1
    config.save()
    old = file_request(requester, title="Old topic about widgets alpha")
    GuideRequest.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=2)
    )

    assert file_request(requester, title="Fresh topic about gadgets omega")


def test_open_limit(requester):
    config = BlogSettings.get_solo()
    config.guide_requests_per_day = 10
    config.guide_requests_open_limit = 1
    config.save()
    file_request(requester, title="First open topic alpha zeta")

    with pytest.raises(guide_requests.GuideRequestError, match="open requests"):
        file_request(requester, title="Second open topic gamma kappa")


# --------------------------------------------------------------- duplicates ----


def test_duplicate_of_a_live_post_is_blocked(requester, reviewer):
    Post.objects.create(
        title="How to bypass FRP on Acme Phone One",
        body="<p>Existing guide body text for duplicates.</p>",
        author=reviewer,
        status=PostStatus.PUBLISHED,
    )

    with pytest.raises(guide_requests.GuideRequestError, match="already covered"):
        file_request(requester)
    found = guide_requests.find_duplicates(VALID["title"])
    assert found and found[0].kind == "post"


def test_duplicate_of_a_public_request_is_blocked(approved_request, make_user):
    other = make_user("other-asker")

    with pytest.raises(guide_requests.GuideRequestError, match="already covered"):
        file_request(other, title="bypass FRP on Acme Phone One how to")


def test_duplicate_of_someones_pending_request_is_blocked_without_a_link(
    requester, make_user
):
    file_request(requester)
    other = make_user("other-asker")

    with pytest.raises(guide_requests.GuideRequestError, match="reviewing"):
        file_request(other)
    assert guide_requests.find_duplicates(VALID["title"]) == []


def test_form_lists_duplicate_links(approved_request, make_user, client):
    client.force_login(make_user("other-asker"))

    page = client.post(reverse("blog:request_create"), VALID)

    assert approved_request.get_absolute_url() in page.content.decode()
    assert GuideRequest.objects.count() == 1


def test_unrelated_title_is_not_a_duplicate(approved_request, make_user):
    assert file_request(
        make_user("curious"), title="Unlock bootloader on a Mediatek tablet"
    )


# ---------------------------------------------------------------- solutions ----


def submit(client, request_item, user, **extra):
    client.force_login(user)
    data = {
        "title": "Complete FRP bypass walkthrough",
        "summary": "A tested walkthrough.",
        "body": "<p>" + "Step by step instructions. " * 20 + "</p>",
        "access": "free",
        "solves": str(request_item.pk),
        **extra,
    }
    return client.post(reverse("blog:post_create"), data)


def test_member_submits_a_free_solution_for_review(approved_request, make_user, client):
    hunter = make_user("hunter")

    response = submit(client, approved_request, hunter)

    post = Post.objects.get(guide_request=approved_request)
    assert response.status_code == 302
    assert response["Location"] == approved_request.get_absolute_url()
    assert (post.author, post.status, post.access) == (
        hunter,
        PostStatus.IN_REVIEW,
        PostAccess.FREE,
    )
    assert post.price is None


def test_access_choice_is_mandatory_for_solutions(approved_request, make_user, client):
    response = submit(client, approved_request, make_user("hunter"), access="")

    assert response.status_code == 200
    assert not Post.objects.exists()


def test_normal_member_cannot_choose_featured_or_paid(
    approved_request, make_user, client
):
    hunter = make_user("hunter")
    for access in ("featured", "paid"):
        response = submit(
            client, approved_request, hunter, access=access, proposed_price="5.00"
        )
        assert response.status_code == 200
    assert not Post.objects.exists()


def test_trusted_author_can_propose_a_paid_solution(
    approved_request, make_user, client
):
    author = make_user("trusted", role="author")

    missing = submit(client, approved_request, author, access="paid")
    ok = submit(client, approved_request, author, access="paid", proposed_price="4.50")

    assert missing.status_code == 200
    assert ok.status_code == 302
    post = Post.objects.get(guide_request=approved_request)
    assert (post.access, post.proposed_price, post.price) == (
        PostAccess.PAID,
        Decimal("4.50"),
        None,
    )


def test_approved_marketplace_seller_may_choose_featured(
    approved_request, make_user, client
):
    seller = make_user("seller")
    fake = SimpleNamespace(is_active_seller=lambda user: user.pk == seller.pk)
    real_get = AppService.get

    def only_marketplace(label):
        return fake if label == "marketplace" else real_get(label)

    with mock.patch("apps.blog.access.AppService.get", side_effect=only_marketplace):
        response = submit(client, approved_request, seller, access="featured")

    assert response.status_code == 302
    assert Post.objects.get().access == PostAccess.FEATURED


def test_requester_cannot_answer_their_own_request(approved_request, requester, client):
    assert submit(client, approved_request, requester).status_code == 404


def test_unapproved_or_unverified_cannot_submit(requester, make_user, client):
    pending = file_request(requester)
    assert submit(client, pending, make_user("hunter")).status_code == 404
    approved = guide_requests.approve(
        pending.pk,
        reviewer=make_user("boss", is_staff=True, is_superuser=True),
    )
    assert (
        submit(client, approved, make_user("newbie", verified=False)).status_code == 404
    )


def test_request_page_shows_the_access_choice(approved_request, make_user, client):
    hunter = make_user("hunter")
    submit(client, approved_request, hunter)

    client.force_login(hunter)
    page = client.get(approved_request.get_absolute_url())

    assert "solution-access" in page.content.decode()
    assert "Free" in page.content.decode()


# -------------------------------------------------- publication & settlement ----


def publish_solution(post, reviewer, capture):
    with capture(execute=True):
        workflow.publish(post, user=reviewer)
    post.refresh_from_db()


def test_publishing_fulfils_the_request_pays_bounty_and_reward(
    approved_request,
    make_user,
    reviewer,
    client,
    notifications,
    django_capture_on_commit_callbacks,
):
    funder = make_user("funder", credits=500)
    hunter = make_user("hunter")
    bounty = bounty_services.post_bounty(
        funder, kind=targets.KIND_GUIDE, object_id=approved_request.pk, amount=120
    )
    submit(client, approved_request, hunter)
    post = Post.objects.get(guide_request=approved_request)
    announced: list[dict] = []
    event_bus.subscribe("blog.post_published", announced.append)
    try:
        publish_solution(post, reviewer, django_capture_on_commit_callbacks)
    finally:
        event_bus._handlers["blog.post_published"].remove(announced.append)

    approved_request.refresh_from_db()
    bounty.refresh_from_db()
    assert approved_request.status == Status.FULFILLED
    assert approved_request.fulfilled_by_post == post
    assert bounty.status == Bounty.Status.FULFILLED
    assert bounty.settled_to == hunter
    wallet_row = apps.get_model("wallet", "Wallet").objects.get(user=hunter)
    assert wallet_row.available >= 120 + 15
    assert announced and announced[0]["guide_request_id"] == approved_request.pk
    notified = [call.args[0] for call in notifications.call_args_list]
    assert requester_id(approved_request) in notified


def requester_id(item) -> int:
    return item.requester_id


def test_publication_is_idempotent(
    approved_request, make_user, reviewer, client, django_capture_on_commit_callbacks
):
    funder = make_user("funder", credits=300)
    hunter = make_user("hunter")
    bounty_services.post_bounty(
        funder, kind=targets.KIND_GUIDE, object_id=approved_request.pk, amount=100
    )
    submit(client, approved_request, hunter)
    post = Post.objects.get()
    publish_solution(post, reviewer, django_capture_on_commit_callbacks)
    before = apps.get_model("wallet", "Wallet").objects.get(user=hunter).available

    with django_capture_on_commit_callbacks(execute=True):
        guide_requests.on_post_published(post)
        guide_requests.reconcile_fulfilments()

    after = apps.get_model("wallet", "Wallet").objects.get(user=hunter).available
    assert before == after


def test_second_solution_after_fulfilment_pays_nothing_more(
    approved_request, make_user, reviewer, client, django_capture_on_commit_callbacks
):
    funder = make_user("funder", credits=300)
    first, second = make_user("first"), make_user("second")
    bounty_services.post_bounty(
        funder, kind=targets.KIND_GUIDE, object_id=approved_request.pk, amount=100
    )
    submit(client, approved_request, first, title="First complete walkthrough")
    submit(client, approved_request, second, title="Second complete walkthrough")
    a, b = Post.objects.order_by("pk")
    publish_solution(a, reviewer, django_capture_on_commit_callbacks)
    publish_solution(b, reviewer, django_capture_on_commit_callbacks)

    assert Bounty.objects.get().settled_to == first
    approved_request.refresh_from_db()
    assert approved_request.fulfilled_by_post == a


def test_cancelling_an_approved_request_refunds_its_bounties(
    approved_request, make_user, requester, django_capture_on_commit_callbacks
):
    funder = make_user("funder", credits=300)
    bounty = bounty_services.post_bounty(
        funder, kind=targets.KIND_GUIDE, object_id=approved_request.pk, amount=100
    )

    with django_capture_on_commit_callbacks(execute=True):
        guide_requests.cancel(approved_request.pk, actor=requester)

    bounty.refresh_from_db()
    assert bounty.status == Bounty.Status.CANCELLED
    assert apps.get_model("wallet", "Wallet").objects.get(user=funder).available == 300


def test_self_published_or_automation_posts_earn_nothing(
    make_user, reviewer, django_capture_on_commit_callbacks
):
    post = Post.objects.create(
        title="Staff written article",
        body="<p>Long enough body for the staff article.</p>",
        author=reviewer,
    )
    publish_solution(post, reviewer, django_capture_on_commit_callbacks)

    assert wallet.balance(reviewer) == 0


def test_member_post_published_by_staff_earns_the_reward(
    make_user, reviewer, django_capture_on_commit_callbacks
):
    writer = make_user("writer")
    post = Post.objects.create(
        title="Member written article",
        body="<p>Long enough body for the member article.</p>",
        author=writer,
    )
    publish_solution(post, reviewer, django_capture_on_commit_callbacks)

    assert wallet.balance(writer) == 10


def test_featured_post_earns_the_extra_reward(
    make_user, reviewer, django_capture_on_commit_callbacks
):
    writer = make_user("writer")
    post = Post.objects.create(
        title="Member featured article",
        body="<p>Long enough body for the featured article.</p>",
        author=writer,
        access=PostAccess.FEATURED,
    )
    publish_solution(post, reviewer, django_capture_on_commit_callbacks)

    assert wallet.balance(writer) == 10 + 25


# ------------------------------------------------------- price & workflow ----


def test_publishing_a_paid_post_needs_a_price(reviewer, make_user):
    post = Post.objects.create(
        title="Paid walkthrough",
        body="<p>Long enough body for the paid article.</p>",
        author=make_user("writer"),
        access=PostAccess.PAID,
    )

    with pytest.raises(ValidationError):
        workflow.publish(post, user=reviewer)
    post.refresh_from_db()
    assert post.status == PostStatus.DRAFT


def test_publishing_approves_the_proposed_price(reviewer, make_user):
    post = Post.objects.create(
        title="Paid walkthrough",
        body="<p>Long enough body for the paid article.</p>",
        author=make_user("writer"),
        access=PostAccess.PAID,
        proposed_price=Decimal("3.00"),
    )

    workflow.publish(post, user=reviewer)

    post.refresh_from_db()
    assert post.price == Decimal("3.00")
    assert post.price_approved_by == reviewer
    assert post.published_by == reviewer
    assert post.price_approved_at is not None


def test_a_staff_set_price_is_kept_on_publication(reviewer, make_user):
    post = Post.objects.create(
        title="Paid walkthrough",
        body="<p>Long enough body for the paid article.</p>",
        author=make_user("writer"),
        access=PostAccess.PAID,
        price=Decimal("8.00"),
        proposed_price=Decimal("3.00"),
    )

    workflow.publish(post, user=reviewer)

    post.refresh_from_db()
    assert post.price == Decimal("8.00")

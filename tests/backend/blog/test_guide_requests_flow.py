"""Guide-request flow: search first, required context, article button, follow, staff."""

from __future__ import annotations

from decimal import Decimal
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from apps.blog import guide_requests
from apps.blog import guide_requests_search as search
from apps.blog.models import (
    BlogSettings,
    GuideRequest,
    GuideRequestFollower,
    Post,
    PostAccess,
    PostStatus,
)
from apps.firmwares.models import Brand, Model

pytestmark = pytest.mark.django_db
SECRET = "GATEDSECRETSENTENCE"
TRIED = "Flashed the stock ROM with SP Flash Tool v5.2316, error 0xC0020036."
Status = GuideRequest.Status


@pytest.fixture
def make_user(django_user_model):
    def build(name: str, *, verified: bool = True, **extra):
        return django_user_model.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="pw-12345-abc",
            email_verified_at=timezone.now() if verified else None,
            **extra,
        )

    return build


@pytest.fixture
def asker(make_user):
    return make_user("asker")


@pytest.fixture
def reviewer(make_user):
    staff = make_user("reviewer", is_staff=True)
    staff.user_permissions.add(
        *Permission.objects.filter(
            codename__in=["change_guiderequest", "view_guiderequest", "change_post"]
        )
    )
    return staff


@pytest.fixture
def notifications():
    with mock.patch(
        "apps.core.services.notifications.notification_service.send"
    ) as send:
        yield send


@pytest.fixture
def phone():
    brand = Brand.objects.create(name="Infinix", slug="infinix")
    return Model.objects.create(
        brand=brand, name="Hot 40", slug="hot-40", model_code="X6836"
    )


@pytest.fixture
def article(make_user, phone):
    return Post.objects.create(
        title="Hot 40 FRP bypass method",
        body=f"<p>{SECRET} and the full steps of the method.</p>",
        author=make_user("writer"),
        status=PostStatus.PUBLISHED,
        access=PostAccess.PAID,
        price=Decimal("3.00"),
        firmware_model=phone,
        firmware_brand=phone.brand,
    )


def token_for(user, **query):
    return search.make_token(user.pk, search.DeviceQuery(**query), 0)


def form_post(user, **overrides):
    data = {
        "ctx": token_for(user, model_code="X6836"),
        "title": "Another way to bypass FRP on Hot 40",
        "kind": "method",
        "model_code": "X6836",
        "tried": TRIED,
        **overrides,
    }
    return {key: value for key, value in data.items() if value is not None}


# ---------------------------------------------------------------- search step ---


def test_search_step_needs_a_login(client):
    for name in ("request_create", "request_search", "request_form"):
        response = client.get(reverse(f"blog:{name}"))
        assert response.status_code == 302
        assert "login" in response["Location"]


def test_search_step_asks_for_a_device_code_first(asker, client):
    client.force_login(asker)

    page = client.get(reverse("blog:request_create"))
    partial = client.get(reverse("blog:request_search"), {"keywords": "frp"})

    assert page.status_code == 200
    assert "Step 1 of 2" in page.content.decode()
    assert 'name="model_code"' in page.content.decode()
    assert 'placeholder="e.g. SM-A145F or X6836"' in page.content.decode()
    assert "guide-continue" not in partial.content.decode()
    assert "guide-search-hint" in partial.content.decode()


def test_live_results_list_related_articles_with_access_badges(asker, article, client):
    free = Post.objects.create(
        title="Hot 40 free unlock note",
        body="<p>Free body text that everybody can read here.</p>",
        author=article.author,
        status=PostStatus.PUBLISHED,
        firmware_model=article.firmware_model,
    )
    client.force_login(asker)

    response = client.get(reverse("blog:request_search"), {"model_code": "X6836"})
    html = response.content.decode()

    assert article.get_absolute_url() in html
    assert free.get_absolute_url() in html
    assert "This solved it" in html
    assert 'data-testid="guide-result-access">Paid<' in html
    assert 'data-testid="guide-result-access">Free<' in html
    assert response["Cache-Control"].startswith("max-age=0")


def test_gated_body_text_is_never_in_the_results(asker, article, client):
    client.force_login(asker)

    page = client.get(reverse("blog:request_create"), {"model_code": "X6836"})
    partial = client.get(reverse("blog:request_search"), {"model_code": "X6836"})

    assert article.title in page.content.decode()
    assert SECRET not in page.content.decode()
    assert SECRET not in partial.content.decode()


def test_the_continue_link_appears_only_after_a_search(asker, article, client):
    client.force_login(asker)

    partial = client.get(reverse("blog:request_search"), {"model_code": "X6836"})
    html = partial.content.decode()

    assert "None of these solve it — request a new method" in html
    link = next(
        part.split('"')[0]
        for part in html.split('href="')
        if part.startswith(reverse("blog:request_form"))
    ).replace("&amp;", "&")
    token = parse_qs(urlparse(link).query)["ctx"][0]
    assert search.read_token(token, asker.pk).query.model_code == "X6836"


def test_search_with_no_matches_still_allows_a_request(asker, client):
    client.force_login(asker)

    partial = client.get(reverse("blog:request_search"), {"model_code": "ZZ9999"})

    assert "guide-no-results" in partial.content.decode()
    assert "guide-continue" in partial.content.decode()


def test_the_form_cannot_be_reached_without_searching(asker, make_user, client):
    other = make_user("other")
    client.force_login(asker)

    bare = client.get(reverse("blog:request_form"))
    forged = client.get(
        reverse("blog:request_form"), {"ctx": token_for(other, model_code="X1")}
    )
    posted = client.post(reverse("blog:request_form"), form_post(asker, ctx=""))

    for response in (bare, forged, posted):
        assert response.status_code == 302
        assert response["Location"] == reverse("blog:request_create")
    assert not GuideRequest.objects.exists()


# ----------------------------------------------------------------------- form ---


def test_form_fields_have_examples_and_help(asker, client):
    client.force_login(asker)

    html = client.get(
        reverse("blog:request_form"), {"ctx": token_for(asker, model_code="X6836")}
    ).content.decode()

    assert "Step 2 of 2" in html
    for text in (
        "e.g. Bypass FRP on Infinix Hot 40",
        "e.g. AI2401_A",
        "e.g. 100",
        "What have you tried?",
        "Tools used, steps, error messages and",
        "e.g. Phone boots to the setup wizard",
    ):
        assert text in html
    assert 'value="X6836"' in html


def test_what_have_you_tried_is_required(asker, client):
    client.force_login(asker)

    response = client.post(reverse("blog:request_form"), form_post(asker, tried=""))
    short = client.post(reverse("blog:request_form"), form_post(asker, tried="no"))

    assert response.status_code == 200
    assert short.status_code == 200
    assert "This field is required." in response.content.decode()
    assert not GuideRequest.objects.exists()


def test_a_model_or_variant_code_is_required(asker, client):
    client.force_login(asker)

    response = client.post(
        reverse("blog:request_form"), form_post(asker, model_code="")
    )

    assert response.status_code == 200
    assert "Enter the model code or the variant code." in response.content.decode()
    assert not GuideRequest.objects.exists()
    assert (
        client.post(
            reverse("blog:request_form"),
            form_post(asker, model_code="", variant_code="AI2401"),
        ).status_code
        == 302
    )


def test_the_request_stores_everything_the_member_entered(asker, phone, client):
    client.force_login(asker)

    response = client.post(
        reverse("blog:request_form"),
        form_post(
            asker,
            expected_outcome="Boots to the wizard",
            bounty_offer="150",
            description="Second-hand phone.",
            search_keywords="frp",
            variant_code="AI2401_A",
        ),
    )

    item = GuideRequest.objects.get()
    assert response.status_code == 302
    assert item.status == Status.PENDING
    assert (item.tried, item.expected_outcome, item.bounty_offer) == (
        f"<p>{TRIED}</p>",
        "Boots to the wizard",
        150,
    )
    assert item.description == "<p>Second-hand phone.</p>"
    assert item.firmware_model == phone and item.brand == phone.brand
    assert (item.model_code, item.variant_code) == ("X6836", "AI2401_A")
    assert item.search_keywords == "frp"


def test_service_rules_for_context_offer_and_device(asker):
    text = guide_requests.RequestText(
        title="Unlock the pattern on Hot 40", kind="pattern", tried=TRIED
    )
    context = guide_requests.RequestContext(model_code="X6836")

    with pytest.raises(guide_requests.GuideRequestError, match="already tried"):
        guide_requests.create_request(
            asker,
            guide_requests.RequestText(title=text.title, kind="trick", tried="short"),
            context,
        )
    with pytest.raises(guide_requests.GuideRequestError, match="model code"):
        guide_requests.create_request(asker, text, guide_requests.RequestContext())
    with pytest.raises(guide_requests.GuideRequestError, match="between 1"):
        guide_requests.create_request(asker, text, context, bounty_offer=0)
    with pytest.raises(guide_requests.GuideRequestError, match="kind of help"):
        guide_requests.create_request(
            asker,
            guide_requests.RequestText(title=text.title, kind="x", tried=TRIED),
            context,
        )
    assert not GuideRequest.objects.exists()


# --------------------------------------------------------------- article button ---


def test_every_article_offers_the_request_button(asker, article, client):
    page = client.get(article.get_absolute_url())

    assert (
        "Didn&#x27;t resolve your issue? Request a new method" in page.content.decode()
        or "Didn't resolve your issue? Request a new method" in page.content.decode()
    )
    assert reverse("blog:request_from_post", args=[article.slug]) in (
        page.content.decode()
    )


def test_the_button_hides_while_requests_are_switched_off(article, client):
    config = BlogSettings.get_solo()
    config.allow_guide_requests = False
    config.save()

    page = client.get(article.get_absolute_url())

    assert "request-new-method" not in page.content.decode()


def test_the_button_needs_a_login_and_prefills_the_form(asker, article, phone, client):
    url = reverse("blog:request_from_post", args=[article.slug])

    anonymous = client.get(url)
    client.force_login(asker)
    redirect = client.get(url)
    form = client.get(redirect["Location"])
    html = form.content.decode()

    assert anonymous.status_code == 302 and "login" in anonymous["Location"]
    assert redirect.status_code == 302
    assert redirect["Location"].startswith(reverse("blog:request_form"))
    assert article.title in html
    assert 'value="X6836"' in html
    assert f'name="related_post" value="{article.pk}"' in html
    assert "autofocus" in html.split('name="tried"')[1].split(">")[0] or (
        "autofocus" in html.split('id="id_tried"')[0].rsplit("<textarea", 1)[1]
    )


def test_a_request_from_an_article_keeps_the_article_and_skips_its_title(
    asker, article, client
):
    client.force_login(asker)
    token = client.get(reverse("blog:request_from_post", args=[article.slug]))[
        "Location"
    ].split("ctx=")[1]
    token = token.replace("%3A", ":")

    response = client.post(
        reverse("blog:request_form"),
        {
            "ctx": token,
            "title": article.title,
            "kind": "method",
            "model_code": "X6836",
            "tried": TRIED,
            "related_post": str(article.pk),
        },
    )

    item = GuideRequest.objects.get()
    assert response.status_code == 302
    assert item.related_post == article


def test_an_unpublished_article_cannot_be_the_related_one(asker, article, client):
    Post.objects.filter(pk=article.pk).update(status=PostStatus.DRAFT)
    client.force_login(asker)

    response = client.get(reverse("blog:request_from_post", args=[article.slug]))

    assert response.status_code == 404


# ------------------------------------------------------------ staff and hunters ---


@pytest.fixture
def from_article(asker, article, client):
    client.force_login(asker)
    token = (
        client.get(reverse("blog:request_from_post", args=[article.slug]))["Location"]
        .split("ctx=")[1]
        .replace("%3A", ":")
    )
    client.post(
        reverse("blog:request_form"),
        {
            "ctx": token,
            "title": "Another way to bypass FRP on Hot 40",
            "kind": "method",
            "model_code": "X6836",
            "tried": TRIED,
            "related_post": str(article.pk),
        },
    )
    client.logout()
    return GuideRequest.objects.get()


def test_staff_reviewers_see_the_article_and_what_was_tried(
    from_article, article, reviewer, client
):
    client.force_login(reviewer)

    detail = client.get(from_article.get_absolute_url()).content.decode()
    change = client.get(
        reverse("admin:blog_guiderequest_change", args=[from_article.pk])
    ).content.decode()

    for html in (detail, change):
        assert TRIED in html
        assert article.title in html
    assert "request-related-post" in detail


def test_hunters_see_the_context_only_once_the_request_is_approved(
    from_article, reviewer, make_user, client
):
    hunter = make_user("hunter")
    client.force_login(hunter)
    assert client.get(from_article.get_absolute_url()).status_code == 404

    guide_requests.approve(from_article.pk, reviewer=reviewer)
    approved = client.get(from_article.get_absolute_url()).content.decode()
    client.logout()
    anonymous = client.get(from_article.get_absolute_url()).content.decode()
    client.force_login(make_user("unverified", verified=False))
    unverified = client.get(from_article.get_absolute_url()).content.decode()

    assert TRIED in approved
    assert "Hot 40 FRP bypass method" in approved
    assert TRIED not in anonymous and "request-tried-locked" in anonymous
    assert TRIED not in unverified


def test_the_owner_sees_the_context_of_a_pending_request(from_article, asker, client):
    client.force_login(asker)

    assert TRIED in client.get(from_article.get_absolute_url()).content.decode()


# ------------------------------------------------------------ duplicates/follow ---


@pytest.fixture
def open_request(asker, reviewer, phone):
    item = guide_requests.create_request(
        asker,
        guide_requests.RequestText(
            title="Unlock the screen pattern on Hot 40", kind="pattern", tried=TRIED
        ),
        guide_requests.RequestContext(firmware_model=phone, model_code="X6836"),
    )
    return guide_requests.approve(item.pk, reviewer=reviewer)


def test_open_requests_for_the_same_device_and_topic_are_offered_to_follow(
    open_request, make_user, client
):
    other = make_user("second")
    client.force_login(other)

    page = client.post(
        reverse("blog:request_form"),
        form_post(other, title="Pattern lock removal for Hot 40", kind="pattern"),
    )
    html = page.content.decode()

    assert page.status_code == 200
    assert "guide-open-duplicates" in html
    assert reverse("blog:request_follow", args=[open_request.pk]) in html
    assert "guide-confirm-different" in html
    assert GuideRequest.objects.count() == 1


def test_confirming_that_the_request_differs_files_it_anyway(
    open_request, make_user, client
):
    other = make_user("second")
    client.force_login(other)

    response = client.post(
        reverse("blog:request_form"),
        form_post(
            other,
            title="Pattern lock removal for Hot 40",
            kind="pattern",
            confirm_different="1",
        ),
    )

    assert response.status_code == 302
    assert GuideRequest.objects.count() == 2


def test_other_devices_are_not_duplicates(open_request, make_user, client):
    other = make_user("second")
    client.force_login(other)

    response = client.post(
        reverse("blog:request_form"),
        form_post(other, title="Pattern lock removal", kind="pattern", model_code="Z1"),
    )

    assert response.status_code == 302


def test_following_is_idempotent_and_counted(open_request, make_user, client):
    follower = make_user("second")
    client.force_login(follower)
    url = reverse("blog:request_follow", args=[open_request.pk])

    client.post(url)
    client.post(url)

    assert GuideRequestFollower.objects.filter(request=open_request).count() == 1
    detail = client.get(open_request.get_absolute_url()).content.decode()
    assert "1 member follows this request" in detail
    assert "Stop following" in detail


def test_following_rules(open_request, asker, make_user, reviewer, client):
    with pytest.raises(guide_requests.GuideRequestError, match="yourself"):
        guide_requests.follow(open_request.pk, asker)
    with pytest.raises(guide_requests.GuideRequestError, match="Verify"):
        guide_requests.follow(open_request.pk, make_user("nomail", verified=False))
    pending = guide_requests.create_request(
        make_user("third"),
        guide_requests.RequestText(
            title="Something else entirely", kind="trick", tried=TRIED
        ),
        guide_requests.RequestContext(model_code="Q9"),
    )
    with pytest.raises(guide_requests.GuideRequestError, match="open public"):
        guide_requests.follow(pending.pk, make_user("fourth"))
    client.force_login(make_user("fifth"))
    assert (
        client.post(reverse("blog:request_follow", args=[pending.pk])).status_code
        == 404
    )
    assert (
        client.get(reverse("blog:request_follow", args=[open_request.pk])).status_code
        == 405
    )


def test_followers_are_notified_on_fulfilment_and_the_requester_on_milestones(
    open_request, make_user, notifications
):
    follower = make_user("second")
    guide_requests.follow(open_request.pk, follower)
    assert notifications.call_args.args[0] == open_request.requester_id
    assert "also need" in notifications.call_args.args[2]
    notifications.reset_mock()
    solution = Post.objects.create(
        title="Pattern unlock walkthrough",
        body="<p>" + "Steps to remove the pattern. " * 10 + "</p>",
        author=make_user("writer2"),
        status=PostStatus.PUBLISHED,
        guide_request=open_request,
    )

    guide_requests.on_post_published(solution)

    notified = {call.args[0] for call in notifications.call_args_list}
    assert notified == {open_request.requester_id, follower.pk}


def test_unfollow_removes_the_follow(open_request, make_user):
    follower = make_user("second")
    guide_requests.follow(open_request.pk, follower)

    assert guide_requests.unfollow(open_request.pk, follower) is True
    assert guide_requests.unfollow(open_request.pk, follower) is False


def test_the_approval_notice_mentions_the_planned_bounty(
    asker, reviewer, notifications
):
    item = guide_requests.create_request(
        asker,
        guide_requests.RequestText(
            title="Unlock the screen pattern on Hot 40", kind="pattern", tried=TRIED
        ),
        guide_requests.RequestContext(model_code="X6836"),
        bounty_offer=75,
    )
    notifications.reset_mock()

    guide_requests.approve(item.pk, reviewer=reviewer)

    assert "75-credit bounty" in notifications.call_args.args[2]


def test_the_request_list_searches_typo_tolerantly(open_request, client):
    page = client.get(reverse("blog:request_list"), {"q": "patern"})

    assert open_request.title in page.content.decode()

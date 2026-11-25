"""Playwright checks for the journal: routes, console, overflow and key interactions."""

from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

from apps.blog.models import (
    BlogSettings,
    Category,
    GuideRequest,
    Post,
    PostAccess,
    PostSource,
    PostStatus,
    PostUpdateProposalStatus,
)
from apps.bounty import services as bounty_services
from apps.comments.models import Comment
from apps.firmwares.blog_automation import FirmwareBlogService
from apps.firmwares.chipsets import create_chipset_record
from apps.firmwares.models import Brand, Model
from apps.tags.models import Tag
from apps.wallet import api as wallet
from tests.frontend.editor_support import fill_rich_text

VIEWPORTS = ((390, 844), (768, 1024), (1024, 768), (1365, 900))
ARTICLE_BODY = (
    "<h2>Preparing the device</h2><p>"
    + "Charge the battery above fifty percent and back up your data. " * 6
    + "</p><h2>Flashing the firmware</h2><p>"
    + "Use the original cable and install the matching USB drivers. " * 6
    + "</p><h3>Verification</h3><p>Confirm the build number after the first boot.</p>"
    "<table><thead><tr><th>Step</th><th>Tool</th><th>Mode</th></tr></thead>"
    "<tbody><tr><td>One</td><td>Odin</td><td>Download</td></tr></tbody></table>"
    "<h2>Troubleshooting</h2><p>If flashing fails, retry with another port.</p>"
)


class Monitor:
    """Collects console errors, warnings and uncaught page errors."""

    def __init__(self, page):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on("console", self._console)

    def _console(self, message):
        if message.type == "error":
            self.errors.append(message.text)
        elif message.type == "warning":
            self.warnings.append(message.text)


def launch(playwright):
    try:
        return playwright.chromium.launch()
    except PlaywrightError as error:
        pytest.fail(
            "Chromium is required for journal browser checks. Install it with "
            f"`.venv\\Scripts\\python.exe -m playwright install chromium`.\n{error}"
        )


def logged_in_cookie(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session.pop("device_registration_prompt", None)
    session.save()
    return client.cookies[settings.SESSION_COOKIE_NAME].value


def assert_no_horizontal_overflow(page, label):
    result = page.evaluate(
        """() => {
            const limit = document.documentElement.clientWidth;
            return {
                viewport: limit,
                document: document.documentElement.scrollWidth,
                offenders: Array.from(document.querySelectorAll("body *"))
                    .filter(el => el.getBoundingClientRect().right > limit + 1)
                    .filter(el => !el.closest("[role=region][tabindex='0'], .table-scroll, .visually-hidden, .hp-field"))
                    .slice(0, 8)
                    .map(el => `${el.tagName.toLowerCase()}.${el.className}`),
            };
        }"""
    )
    assert result["document"] <= result["viewport"] + 1, f"Overflow {label}: {result}"
    assert not result["offenders"], f"Overflowing elements {label}: {result}"


@pytest.fixture(name="journal")
def seeded_journal(request):
    request.getfixturevalue("transactional_db")
    user_model = get_user_model()
    author = user_model.objects.create_user(
        username="browser-journal-author",
        email="browser-journal-author@example.test",
        full_name="Browser Writer",
    )
    reader = user_model.objects.create_user(
        username="browser-journal-reader",
        email="browser-journal-reader@example.test",
        full_name="Browser Reader",
    )
    guides = Category.objects.create(name="Guides")
    news = Category.objects.create(name="News")
    topic = Tag.objects.create(name="Flashing", is_curated=True)
    now = timezone.now()
    posts = []
    for index in range(16):
        post = Post.objects.create(
            title=f"Journal article {index:02d}",
            summary=f"Summary of journal article {index:02d}.",
            body=ARTICLE_BODY if index == 0 else "<p>A short article body here.</p>",
            author=author,
            category=guides if index % 2 else news,
            status=PostStatus.PUBLISHED,
            publish_at=now - timedelta(hours=index + 1),
            published_at=now - timedelta(hours=index + 1),
            views_count=index,
            featured=index == 3,
            access=(
                PostAccess.FEATURED
                if index == 3
                else PostAccess.PAID
                if index == 4
                else PostAccess.FREE
            ),
        )
        post.tags.add(topic)
        posts.append(post)
    return {"author": author, "reader": reader, "posts": posts, "topic": topic}


def hub_urls():
    brand = Brand.objects.create(name="Browserphone", slug="browserphone")
    chipset = create_chipset_record(
        vendor="MediaTek", name="Helio G88", slug="mediatek-helio-g88"
    )
    model = Model.objects.create(brand=brand, name="B1", slug="b1", chipset_ref=chipset)
    FirmwareBlogService.sync_model_post(model)
    FirmwareBlogService.sync_brand_post(brand)
    FirmwareBlogService.sync_chipset_post(chipset)
    return {
        "brand": reverse("blog:brand_posts", kwargs={"brand_slug": brand.slug}),
        "model": reverse(
            "blog:model_posts",
            kwargs={"brand_slug": brand.slug, "model_slug": model.slug},
        ),
        "chipset": reverse("blog:chipset_posts", kwargs={"slug": chipset.slug}),
        "model_post": Post.objects.get(firmware_model=model).get_absolute_url(),
    }


@pytest.mark.django_db(transaction=True)
def test_journal_routes_render_cleanly_at_every_width_and_theme(live_server, journal):
    hubs = hub_urls()
    first = journal["posts"][0]
    routes = {
        "journal": reverse("blog:post_list"),
        "filtered": f"{reverse('blog:post_list')}?category=guides&sort=title",
        "search": f"{reverse('blog:post_list')}?q=journal",
        "page two": f"{reverse('blog:post_list')}?page=2",
        "empty search": f"{reverse('blog:post_list')}?q=zzzzzz",
        "article": first.get_absolute_url(),
        "automated model post": hubs["model_post"],
        "brand hub": hubs["brand"],
        "model hub": hubs["model"],
        "chipset hub": hubs["chipset"],
        "archive": reverse(
            "blog:post_archive_year", kwargs={"year": first.published_at.year}
        ),
        "topics": reverse("tags:list"),
        "topic": reverse("tags:detail", kwargs={"slug": journal["topic"].slug}),
    }
    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        monitor = Monitor(page)
        for label, path in routes.items():
            response = page.goto(f"{live_server.url}{path}", wait_until="load")
            assert response.status == 200, f"{label}: HTTP {response.status}"
            assert page.locator("h1").count() == 1, f"{label}: expected one h1"
            for theme in ("light", "dark"):
                page.evaluate(
                    "theme => { document.documentElement.dataset.bsTheme = theme; }",
                    theme,
                )
                for width, height in VIEWPORTS:
                    page.set_viewport_size({"width": width, "height": height})
                    assert_no_horizontal_overflow(page, f"{label} {theme} {width}px")
        assert not monitor.errors, f"Browser errors: {monitor.errors}"


@pytest.mark.django_db(transaction=True)
def test_editorial_workspace_filters_posts_by_source(live_server):
    author = get_user_model().objects.create_user(
        username="browser-editorial-author",
        email="browser-editorial-author@example.test",
        role="author",
    )
    BlogSettings.objects.update_or_create(
        pk=1, defaults={"enable_blog": True, "allow_user_blog_posts": True}
    )
    Post.objects.create(
        title="Automated browser catalog post",
        body="<p>Automatically refreshed catalog content.</p>",
        author=author,
        content_source=PostSource.AUTOMATED,
    )
    Post.objects.create(
        title="Editorial browser draft",
        body="<p>Human authored editorial content.</p>",
        author=author,
        content_source=PostSource.EDITORIAL,
    )
    cookie = logged_in_cookie(author)
    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": cookie,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        monitor = Monitor(page)
        response = page.goto(
            f"{live_server.url}{reverse('blog:manage_posts')}?source=automated",
            wait_until="load",
        )

        assert response.status == 200
        expect(page.locator("#editorial-source")).to_have_value("automated")
        expect(page.get_by_text("Automated browser catalog post")).to_be_visible()
        expect(page.get_by_text("Editorial browser draft")).to_have_count(0)
        assert_no_horizontal_overflow(page, "editorial workspace at 390px")
        page.set_viewport_size({"width": 1365, "height": 900})
        assert_no_horizontal_overflow(page, "editorial workspace at 1365px")
        assert not monitor.errors, f"Browser errors: {monitor.errors}"
        assert not monitor.warnings, f"Browser warnings: {monitor.warnings}"
        context.close()
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_contributor_can_choose_an_open_bounty_or_write_a_new_post(live_server):
    now = timezone.now()
    user_model = get_user_model()
    asker = user_model.objects.create_user(
        username="browser-bounty-asker",
        email="browser-bounty-asker@example.test",
        email_verified_at=now,
    )
    contributor = user_model.objects.create_user(
        username="browser-bounty-contributor",
        email="browser-bounty-contributor@example.test",
        email_verified_at=now,
    )
    wallet.credit(
        asker,
        100,
        reason=wallet.LedgerReason.REWARD,
        key="browser-blog-contribution-bounty",
    )
    guide = GuideRequest.objects.create(
        requester=asker,
        title="Browser guide contribution request",
        status=GuideRequest.Status.APPROVED,
    )
    guide_category = Category.objects.create(
        name="Browser Guides", slug="browser-guides"
    )
    Post.objects.create(
        title="Pixel recovery walkthrough guide",
        body="<p>A published guide already covers this title.</p>",
        author=asker,
        category=guide_category,
        status=PostStatus.PUBLISHED,
        publish_at=now,
    )
    bounty_services.post_bounty(asker, kind="guide", object_id=guide.pk, amount=40)
    blog_settings = BlogSettings.get_solo()
    blog_settings.enable_blog = True
    blog_settings.allow_user_blog_posts = True
    blog_settings.save()
    cookie = logged_in_cookie(contributor)

    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": cookie,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        monitor = Monitor(page)
        response = page.goto(
            f"{live_server.url}{reverse('blog:contribute')}", wait_until="load"
        )

        assert response.status == 200
        expect(page.get_by_role("heading", name="Open guide bounties")).to_be_visible()
        expect(
            page.get_by_role("heading", name="Write a new blog post")
        ).to_be_visible()
        page.locator('input[name="title"]').fill("Pixel recovery walkthrough guide")
        expect(page.locator("#contribution-title-matches")).to_contain_text(
            "Possible related public content", timeout=10_000
        )
        expect(
            page.locator("#contribution-title-matches").get_by_role(
                "link", name="Pixel recovery walkthrough guide"
            )
        ).to_be_visible()
        expect(page.get_by_role("button", name="Submit for review")).to_be_enabled()
        assert_no_horizontal_overflow(page, "contribution page at 390px")
        page.set_viewport_size({"width": 1365, "height": 900})
        assert_no_horizontal_overflow(page, "contribution page at 1365px")
        page.get_by_role("link", name="View request and contribute").click()
        expect(page.get_by_role("link", name="Submit a solution")).to_be_visible()
        assert not monitor.errors, f"Browser errors: {monitor.errors}"
        assert not monitor.warnings, f"Browser warnings: {monitor.warnings}"
        context.close()
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_member_article_update_proposal_stays_private_until_staff_approval(live_server):
    now = timezone.now()
    user_model = get_user_model()
    author = user_model.objects.create_user(
        username="browser-update-post-author",
        email="browser-update-post-author@example.test",
        email_verified_at=now,
        full_name="Article Author",
    )
    contributor = user_model.objects.create_user(
        username="browser-update-proposal-member",
        email="browser-update-proposal-member@example.test",
        email_verified_at=now,
        full_name="Proposal Member",
    )
    reviewer = user_model.objects.create_user(
        username="browser-update-proposal-reviewer",
        email="browser-update-proposal-reviewer@example.test",
        is_staff=True,
        full_name="Proposal Reviewer",
    )
    reviewer.user_permissions.add(Permission.objects.get(codename="change_post"))
    category = Category.objects.create(name="Update Proposal Guides")
    post = Post.objects.create(
        title="Original public guide title",
        summary="Original public guide summary.",
        body=ARTICLE_BODY,
        author=author,
        category=category,
        status=PostStatus.PUBLISHED,
        publish_at=now,
        published_at=now,
    )
    member_cookie = logged_in_cookie(contributor)
    reviewer_cookie = logged_in_cookie(reviewer)

    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": member_cookie,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        monitor = Monitor(page)
        page.goto(f"{live_server.url}{post.get_absolute_url()}", wait_until="load")
        page.get_by_role("link", name="Suggest an update").click()
        page.get_by_role("heading", name="Suggest an update").wait_for()
        page.locator('input[name="title"]').fill("Member suggested public title")
        page.get_by_role("button", name="Send update for review").click()
        page.wait_for_url(f"**{reverse('blog:my_update_proposals')}")
        expect(page.get_by_text("Pending")).to_be_visible()
        assert page.get_by_text("Original public guide title").count() == 1
        assert_no_horizontal_overflow(page, "member proposal status at 390px")

        page.set_viewport_size({"width": 1365, "height": 900})
        assert_no_horizontal_overflow(page, "member proposal status at 1365px")
        context.close()

        review_context = browser.new_context(viewport={"width": 1365, "height": 900})
        review_context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": reviewer_cookie,
                    "url": live_server.url,
                }
            ]
        )
        review_page = review_context.new_page()
        review_monitor = Monitor(review_page)
        review_url = reverse("blog:update_proposal_queue")
        review_page.goto(f"{live_server.url}{review_url}", wait_until="load")
        review_page.get_by_role("link", name="Original public guide title").click()
        review_page.locator('input[name="title"]').fill(
            "Reviewer corrected public title"
        )
        review_page.locator('textarea[name="reason"]').fill("Corrected for accuracy.")
        review_page.get_by_role("button", name="Approve update").click()
        review_page.wait_for_url(f"**{review_url}")
        assert_no_horizontal_overflow(review_page, "review queue at 1365px")
        assert not monitor.errors, f"Member browser errors: {monitor.errors}"
        assert not monitor.warnings, f"Member browser warnings: {monitor.warnings}"
        assert not review_monitor.errors, (
            f"Reviewer browser errors: {review_monitor.errors}"
        )
        assert not review_monitor.warnings, (
            f"Reviewer browser warnings: {review_monitor.warnings}"
        )
        review_context.close()
        browser.close()

    post.refresh_from_db()
    proposal = post.update_proposals.get()
    assert post.title == "Reviewer corrected public title"
    assert post.version == 2
    assert proposal.status == PostUpdateProposalStatus.APPROVED
    assert proposal.reviewed_by == reviewer


@pytest.mark.django_db(transaction=True)
def test_journal_search_chips_and_paging_update_without_a_reload(live_server, journal):
    Post.objects.filter(pk=journal["posts"][7].pk).update(views_count=999)
    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        monitor = Monitor(page)
        page.goto(f"{live_server.url}{reverse('blog:post_list')}", wait_until="load")
        page.evaluate("window.__journalMarker = 'same-document'")

        sort = page.locator("#journal-sort")
        expect(sort.locator("option")).to_have_count(4)
        option_labels = sort.locator("option").all_text_contents()
        assert option_labels == ["Newest", "Most popular", "Oldest", "Title A–Z"]
        expect(sort).to_have_value("")
        expect(page.get_by_text("Featured story")).to_be_visible()
        page.locator("#journal-access").select_option(PostAccess.FEATURED)
        expect(page.locator("#journal-results .post-card")).to_have_count(1)
        expect(page.locator("#journal-results .post-card")).to_contain_text(
            "Journal article 03"
        )
        assert "access=featured" in page.url
        page.locator("#journal-access").select_option(PostAccess.PAID)
        expect(page.locator("#journal-results .post-card")).to_have_count(1)
        expect(page.locator("#journal-results .post-card")).to_contain_text(
            "Journal article 04"
        )
        assert "access=paid" in page.url

        page.goto(f"{live_server.url}{reverse('blog:post_list')}", wait_until="load")
        page.evaluate("window.__journalMarker = 'same-document'")
        page.locator("#journal-query").fill("article 07")
        expect(page.locator("#journal-results .post-card")).to_have_count(1)
        assert "q=article+07" in page.url or "q=article%2007" in page.url
        assert page.evaluate("window.__journalMarker") == "same-document"

        page.goto(f"{live_server.url}{reverse('blog:post_list')}", wait_until="load")
        page.evaluate(
            "document.getElementById('journal-filters').scrollIntoView({block: 'center'})"
        )
        page.locator("#chip-guides").click()
        expect(page.locator("#journal-results .results-summary")).to_contain_text(
            "in Guides"
        )
        assert "category=guides" in page.url

        sort.select_option("popular")
        page.wait_for_url("**sort=popular**")
        expect(page.locator("#journal-results .post-card").first).to_contain_text(
            "Journal article 07"
        )
        sort.select_option("")
        expect(page.locator("#journal-results .post-card").first).to_contain_text(
            "Journal article 01"
        )
        sort.select_option("oldest")
        page.wait_for_url("**sort=oldest**")
        expect(page.locator("#journal-results .post-card").first).to_contain_text(
            "Journal article 15"
        )
        sort.select_option("title")
        page.wait_for_url("**sort=title**")
        expect(page.locator("#journal-results .post-card").first).to_contain_text(
            "Journal article"
        )
        assert "category=guides" in page.url

        page.get_by_role("link", name="Clear filters").click()
        page.wait_for_load_state("load")
        page.locator("#journal-results .pagination a[rel=next]").click()
        page.wait_for_url("**page=2**")
        assert page.locator("#journal-results .post-card").count() > 0
        assert not monitor.errors, f"Browser errors: {monitor.errors}"
        browser.close()
    assert not monitor.warnings, f"Browser warnings: {monitor.warnings}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("journal")
def test_journal_and_guide_filters_scroll_with_the_document(live_server):
    user_model = get_user_model()
    reader = user_model.objects.get(username="browser-journal-reader")
    BlogSettings.objects.update_or_create(
        pk=1, defaults={"enable_blog": True, "allow_guide_requests": True}
    )
    now = timezone.now()
    GuideRequest.objects.bulk_create(
        GuideRequest(
            requester=reader,
            title=f"Guide request {index:02d} about a device",
            status=GuideRequest.Status.APPROVED,
            created_at=now - timedelta(minutes=index),
        )
        for index in range(20)
    )
    routes = (
        (reverse("blog:post_list"), "#journal-filters"),
        (reverse("blog:request_list"), ".guide-filters"),
    )
    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page(viewport={"width": 1365, "height": 700})
        for path, selector in routes:
            response = page.goto(f"{live_server.url}{path}", wait_until="load")
            assert response is not None and response.status == 200
            filters = page.locator(selector)
            filters.wait_for(state="visible")
            assert (
                filters.evaluate("element => getComputedStyle(element).position")
                == "static"
            )
            initial_box = filters.bounding_box()
            assert initial_box is not None
            page.evaluate("window.scrollTo(0, 700)")
            page.wait_for_function(
                "selector => document.querySelector(selector).getBoundingClientRect().top < 0",
                arg=selector,
            )
            after_scroll = filters.bounding_box()
            assert after_scroll is not None and after_scroll["y"] < initial_box["y"]
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_comment_external_links_open_in_new_tabs_but_same_site_links_do_not(
    live_server,
):
    user_model = get_user_model()
    author = user_model.objects.create_user(
        username="browser-comment-author",
        email="browser-comment-author@example.test",
    )
    post = Post.objects.create(
        title="Comment link behavior guide",
        body=ARTICLE_BODY,
        author=author,
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now() - timedelta(minutes=1),
    )
    external = Comment.objects.create(
        post=post,
        user=author,
        status=Comment.Status.APPROVED,
        body='<p><a href="https://outside.example/path">External reference</a></p>',
    )
    same_site = Comment.objects.create(
        post=post,
        user=author,
        status=Comment.Status.APPROVED,
        body=f'<p><a href="{live_server.url}/help/">Mobofirm help</a></p>',
    )
    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        monitor = Monitor(page)
        response = page.goto(
            f"{live_server.url}{post.get_absolute_url()}", wait_until="load"
        )
        assert response is not None and response.status == 200
        external_link = page.locator(f"#comment-{external.pk} .comment__body a")
        same_site_link = page.locator(f"#comment-{same_site.pk} .comment__body a")
        expect(external_link).to_have_attribute("target", "_blank")
        expect(external_link).to_have_attribute("rel", "nofollow noopener noreferrer")
        expect(same_site_link).not_to_have_attribute("target", "_blank")
        assert not monitor.errors, f"Browser errors: {monitor.errors}"
        browser.close()
    assert not monitor.warnings, f"Browser warnings: {monitor.warnings}"


@pytest.mark.django_db(transaction=True)
def test_article_toc_share_comments_and_keyboard_access(live_server, journal):
    article = journal["posts"][0]
    reader = journal["reader"]
    cookie = logged_in_cookie(reader)
    Comment.objects.create(
        post=article,
        user=journal["author"],
        body="First published comment",
        status=Comment.Status.APPROVED,
    )
    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 1365, "height": 900})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": cookie,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        monitor = Monitor(page)
        page.goto(f"{live_server.url}{article.get_absolute_url()}", wait_until="load")

        toc = page.get_by_role("navigation", name="Table of contents")
        expect(toc).to_be_visible()
        toc.get_by_role("link", name="Flashing the firmware").click()
        assert page.url.endswith("#flashing-the-firmware")
        assert page.locator("#flashing-the-firmware").is_visible()
        expect(page.locator(".article-table")).to_have_attribute("role", "region")
        for name in ("X", "Facebook", "LinkedIn", "WhatsApp", "Telegram", "Email"):
            assert page.get_by_role("link", name=name).first.is_visible(), name
        expect(page.get_by_text("Written by")).to_be_visible()

        fill_rich_text(page, "#comment-form-body", "A helpful browser comment")
        page.get_by_role("button", name="Post comment").click()
        page.wait_for_load_state("load")
        expect(page.get_by_text("Your comment is published.")).to_be_visible()
        expect(
            page.locator(
                "#comments .comment__body", has_text="A helpful browser comment"
            )
        ).to_be_visible()

        fill_rich_text(
            page,
            "#comment-form-body",
            "buy now click here free money http://x.example",
        )
        page.get_by_role("button", name="Post comment").click()
        page.wait_for_load_state("load")
        expect(page.get_by_text("waiting for review")).to_be_visible()

        page.locator(".comment__panel > summary", has_text="Reply").first.click()
        fill_rich_text(
            page, ".comment__panel[open] textarea[name='body']", "A threaded reply"
        )
        page.get_by_role("button", name="Post reply").first.click()
        page.wait_for_load_state("load")
        expect(page.locator(".comment-list--children")).to_contain_text(
            "A threaded reply"
        )

        page.get_by_role("button", name="Upvote").first.click()
        page.wait_for_load_state("load")
        expect(
            page.get_by_role("button", name="Remove your upvote").first
        ).to_be_visible()

        page.goto(f"{live_server.url}{article.get_absolute_url()}", wait_until="load")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.className") == "skip-link"
        for _ in range(8):
            page.keyboard.press("Tab")
        focused = page.evaluate(
            "() => getComputedStyle(document.activeElement).outlineStyle"
        )
        assert focused != "none"
        assert not monitor.errors, f"Browser errors: {monitor.errors}"
        browser.close()
    assert not monitor.warnings, f"Browser warnings: {monitor.warnings}"


@pytest.mark.django_db(transaction=True)
def test_honeypot_is_not_reachable_by_keyboard_or_assistive_technology(
    live_server, journal
):
    article = journal["posts"][0]
    cookie = logged_in_cookie(journal["reader"])
    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": cookie,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        page.goto(f"{live_server.url}{article.get_absolute_url()}", wait_until="load")

        honeypot = page.locator("#comment-form-hp")
        assert honeypot.evaluate("el => el.closest('[inert]') !== null")
        for _ in range(12):
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement.id") != "comment-form-hp"
        browser.close()

"""My contributions: hub, tabs, empty states, wording and the legacy "My files" redirects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogSettings, Category, Post, PostStatus
from apps.blog.services import contributions, workflow
from apps.core.models import AppRegistry
from apps.firmwares.models import Brand, Model, PendingFirmware, Variant

pytestmark = pytest.mark.django_db

LEGACY_FILES_TEXT = ("My files", "My uploads", "Upload firmware", "Upload a firmware")


@pytest.fixture(name="make_user")
def make_user_fixture(django_user_model: Any) -> Callable[..., Any]:
    def build(username: str, *, staff: bool = False) -> Any:
        user = django_user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="Pass-12345!",
            email_verified_at=timezone.now(),
            is_staff=staff,
        )
        user.profile_completed = True
        user.save(update_fields=["profile_completed"])
        return user

    return build


@pytest.fixture(name="member")
def member_fixture(make_user: Callable[..., Any]) -> Any:
    return make_user("hub_member")


@pytest.fixture(name="member_client")
def member_client_fixture(member: Any) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture(name="open_blog")
def open_blog_fixture() -> BlogSettings:
    config = BlogSettings.get_solo()
    config.enable_blog = True
    config.allow_user_blog_posts = True
    config.save()
    return config


def text(response: Any) -> str:
    return response.content.decode()


class TestLegacyRedirects:
    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ("/files/", "/contributions/"),
            ("/files/uploads/", "/contributions/firmware/"),
            ("/files/quota/", "/contributions/quota/"),
        ],
    )
    def test_old_addresses_redirect_permanently(
        self, client: Client, old: str, new: str
    ) -> None:
        response = client.get(old)

        assert response.status_code == 301
        assert response["Location"] == new

    def test_the_old_names_still_reverse_to_the_old_paths(self) -> None:
        assert reverse("library:my_files") == "/files/"
        assert reverse("library:my_uploads") == "/files/uploads/"
        assert reverse("library:my_quota") == "/files/quota/"

    def test_library_filters_keep_their_query_on_the_downloads_tab(
        self, client: Client
    ) -> None:
        response = client.get("/files/?tab=history&q=pixel&tier=paid")

        assert response.status_code == 301
        assert (
            response["Location"]
            == "/contributions/downloads/?tab=history&q=pixel&tier=paid"
        )

    def test_the_upload_status_filter_survives_the_redirect(
        self, client: Client
    ) -> None:
        response = client.get("/files/uploads/?status=rejected")

        assert response["Location"] == "/contributions/firmware/?status=rejected"

    def test_following_a_redirect_reaches_the_hub_for_a_member(
        self, member_client: Client
    ) -> None:
        response = member_client.get("/files/", follow=True)

        assert response.redirect_chain == [("/contributions/", 301)]
        assert "My contributions" in text(response)

    def test_anonymous_visitors_end_at_sign_in(self, client: Client) -> None:
        response = client.get("/files/", follow=True)

        assert response.redirect_chain[0] == ("/contributions/", 301)
        assert "login" in response.redirect_chain[-1][0]

    def test_post_methods_are_not_redirected(self, member_client: Client) -> None:
        assert member_client.post("/files/").status_code == 405


class TestWording:
    def test_signed_in_navigation_says_my_contributions(
        self, member_client: Client
    ) -> None:
        for name in ("home", "firmwares:browse", "contributions:hub"):
            page = text(member_client.get(reverse(name)))
            assert 'href="/contributions/"' in page, name
            assert "My contributions" in page, name
            assert "Contribute firmware" in page, name
            for old in LEGACY_FILES_TEXT:
                assert old not in page, (name, old)

    def test_anonymous_navigation_offers_contribute_but_no_account_link(
        self, client: Client
    ) -> None:
        page = text(client.get(reverse("home")))

        assert "Contribute firmware" in page
        assert "Share a build for review." in page
        assert 'href="/firmwares/contribute/"' in page
        assert "My contributions" not in page
        for old in LEGACY_FILES_TEXT:
            assert old not in page, old

    def test_the_account_dashboard_tile_is_renamed(self, member_client: Client) -> None:
        page = text(member_client.get(reverse("users:dashboard")))

        assert 'href="/contributions/"' in page
        assert "My contributions" in page
        assert "My files" not in page

    @pytest.mark.usefixtures("open_blog")
    def test_the_command_palette_lists_the_contribute_actions(
        self, member_client: Client
    ) -> None:
        page = text(member_client.get(reverse("home")))

        assert "Contribute a firmware" in page
        assert "Contribute a blog post" in page

    def test_the_footer_link_is_renamed(self, member_client: Client) -> None:
        page = text(member_client.get(reverse("firmwares:library_hub")))

        assert ">My contributions</a>" in page


class TestHub:
    @pytest.mark.usefixtures("open_blog")
    def test_an_empty_hub_explains_every_section(self, member_client: Client) -> None:
        page = text(member_client.get(reverse("contributions:hub")))

        assert "You have not contributed any firmware yet" in page
        assert "You have not contributed a blog post yet" in page
        assert 'href="/firmwares/contribute/"' in page
        assert 'href="/blog/contribute/"' in page
        assert "0 files ready to download again" in page
        assert 'aria-label="Breadcrumb"' in page

    @pytest.mark.usefixtures("open_blog")
    def test_the_hub_counts_firmware_and_blog_contributions_by_status(
        self,
        member: Any,
        member_client: Client,
        make_user: Callable[..., Any],
    ) -> None:
        brand = Brand.objects.create(name="Acme", slug="acme")
        model = Model.objects.create(brand=brand, name="Phone", slug="phone")
        variant = Variant.objects.create(model=model, name="Global", slug="global")
        PendingFirmware.objects.create(
            original_file_name="pending-build.zip",
            stored_file_path="/firmware/pending-build.zip",
            uploader=member,
            uploaded_brand=brand,
            uploaded_model=model,
            uploaded_variant=variant,
        )
        PendingFirmware.objects.create(
            original_file_name="approved-build.zip",
            stored_file_path="/firmware/approved-build.zip",
            uploader=member,
            admin_decision="approved",
        )
        category = Category.objects.create(name="Guides", slug="guides")
        reviewer = make_user("hub_reviewer", staff=True)
        for title in ("Waiting post", "Live post", "Returned post"):
            contributions.submit(
                member,
                contributions.ContributionData(
                    title=title,
                    body="<p>Enough words here to make this a real contribution.</p>",
                    category=category,
                ),
            )
        live = Post.objects.get(title="Live post")
        reviewer.user_permissions.add(Permission.objects.get(codename="change_post"))
        workflow.publish(live, user=reviewer)
        contributions.reject(
            reviewer, Post.objects.get(title="Returned post"), "Add detail."
        )

        page = text(member_client.get(reverse("contributions:hub")))

        assert "Firmware contributions" in page
        assert "Awaiting review: 1" in page and "Approved: 1" in page
        assert "Awaiting review: 1" in page
        assert "Published: 1" in page and "Sent back for changes: 1" in page
        assert 'data-contribution-card="blog"' in page

    def test_the_firmware_tab_lists_the_review_status(
        self, member: Any, member_client: Client
    ) -> None:
        PendingFirmware.objects.create(
            original_file_name="rejected-build.zip",
            stored_file_path="/firmware/rejected-build.zip",
            uploader=member,
            admin_decision="rejected",
            admin_notes="Wrong device.",
        )

        page = text(member_client.get(reverse("contributions:firmware")))

        assert "rejected-build.zip" in page
        assert "Rejected" in page and "Wrong device." in page
        assert "Contribute a firmware" in page

    def test_the_firmware_tab_shows_its_empty_state(
        self, member_client: Client
    ) -> None:
        page = text(member_client.get(reverse("contributions:firmware")))

        assert "You have not contributed any firmware" in page
        assert 'href="/firmwares/contribute/"' in page

    @pytest.mark.usefixtures("open_blog")
    def test_the_blog_tab_shows_its_empty_state_and_the_cta(
        self, member_client: Client
    ) -> None:
        page = text(member_client.get(reverse("contributions:blog")))

        assert "You have not contributed a blog post" in page
        assert 'href="/blog/contribute/"' in page

    @pytest.mark.usefixtures("open_blog")
    def test_the_blog_tab_lists_actions_for_a_pending_post(
        self, member: Any, member_client: Client
    ) -> None:
        category = Category.objects.create(name="Guides", slug="guides")
        result = contributions.submit(
            member,
            contributions.ContributionData(
                title="Pending tab post",
                body="<p>Enough words here to make this a real contribution.</p>",
                category=category,
            ),
        )

        page = text(member_client.get(reverse("contributions:blog")))

        assert "Pending tab post" in page and "Awaiting review" in page
        assert (
            reverse("blog:contribute_edit", kwargs={"slug": result.post.slug}) in page
        )
        assert (
            reverse("blog:contribute_withdraw", kwargs={"slug": result.post.slug})
            in page
        )
        assert "csrfmiddlewaretoken" in page

    @pytest.mark.usefixtures("open_blog")
    def test_other_members_never_see_my_contributions(
        self, make_user: Callable[..., Any], member: Any
    ) -> None:
        category = Category.objects.create(name="Guides", slug="guides")
        contributions.submit(
            member,
            contributions.ContributionData(
                title="Private to the author",
                body="<p>Enough words here to make this a real contribution.</p>",
                category=category,
            ),
        )
        other = Client()
        other.force_login(make_user("hub_other"))

        assert "Private to the author" not in text(
            other.get(reverse("contributions:blog"))
        )
        assert Post.objects.get().status == PostStatus.IN_REVIEW

    def test_the_blog_tab_says_so_when_the_blog_app_is_off(
        self, member_client: Client
    ) -> None:
        registry = AppRegistry.get_solo()
        registry.blog_enabled = False
        registry.save()

        page = text(member_client.get(reverse("contributions:blog")))

        assert "Blog contributions are not available" in page

    def test_the_contribute_call_to_action_is_hidden_while_posting_is_closed(
        self, member_client: Client
    ) -> None:
        config = BlogSettings.get_solo()
        config.allow_user_blog_posts = False
        config.save()

        page = text(member_client.get(reverse("contributions:hub")))

        card = page.split('data-contribution-card="blog"')[1].split("</section>")[0]
        assert "/blog/contribute/" not in card
        assert "Contribute a blog post" not in card

"""Tag staff tool pages render inside the Unfold admin shell, not the public shell."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.blog.models import Post, PostStatus
from apps.tags.models import Tag
from apps.tags.models_keyword import KeywordProvider, KeywordSuggestion

PUBLIC_SHELL_MARKERS = ("site-container", "site-header", "bootstrap")


class TagStaffPageShellTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="ts-staff", email="ts-staff@example.test", is_staff=True
        )
        self.staff.user_permissions.add(
            Permission.objects.get(codename="change_tag"),
            Permission.objects.get(codename="view_tag"),
        )
        self.author = user_model.objects.create_user(
            username="ts-author", email="ts-author@example.test"
        )
        self.source = Tag.objects.create(name="Boot loader", is_curated=True)
        self.target = Tag.objects.create(name="Bootloader", is_curated=True)
        self.suggestion = Tag.objects.create(name="Odd idea", ai_suggested=True)
        provider = KeywordProvider.objects.create(name="Shell manual")
        KeywordSuggestion.objects.create(
            provider=provider, keyword="odin flash", score=0.8
        )
        post = Post.objects.create(
            title="Shell tagged",
            body="<p>Enough words to publish this post.</p>",
            author=self.author,
            status=PostStatus.PUBLISHED,
        )
        post.tags.add(self.source)
        self.client.force_login(self.staff)

    def pages(self) -> dict[str, str]:
        return {
            "Tag management": reverse("tags:manage"),
            "Review similar tags": reverse("tags:duplicates_review"),
            "Keyword suggestions": reverse("tags:keyword_review"),
            "Confirm tag merge": (
                f"{reverse('tags:merge')}?source={self.source.slug}"
                f"&target={self.target.slug}"
            ),
        }

    def test_every_staff_page_renders_in_the_admin_shell(self):
        for title, url in self.pages().items():
            with self.subTest(page=title):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "admin/staff_page.html")
                self.assertTemplateNotUsed(response, "base.html")
                self.assertContains(response, 'class="mf-ui-title"')
                self.assertContains(response, title)
                self.assertContains(response, "unfold")
                self.assertContains(response, reverse("admin:index"))
                html = response.content.decode()
                for marker in PUBLIC_SHELL_MARKERS:
                    self.assertNotIn(marker, html)
                self.assertNotRegex(
                    html, r'class="(?:[^"]*\s)?(?:btn|card|d-flex)(?:\s[^"]*)?"'
                )

    def test_manage_page_carries_the_dialog_slot_and_decision_forms(self):
        response = self.client.get(reverse("tags:manage"))
        self.assertContains(response, 'id="dialog-slot"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, "Odd idea")

    def test_dialog_fallback_page_is_an_admin_page_with_the_same_form(self):
        url = reverse("tags:reject_dialog", kwargs={"tag_id": self.suggestion.pk})

        response = self.client.get(url)

        self.assertTemplateUsed(response, "admin/staff_dialog_page.html")
        self.assertTemplateUsed(response, "admin/staff_page.html")
        self.assertContains(response, "Reject this suggested tag?")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertNotContains(response, "site-container")

    def test_staff_pages_are_denied_without_the_permission(self):
        member = Client()
        member.force_login(
            get_user_model().objects.create_user(
                username="ts-member", email="ts-member@example.test", is_staff=True
            )
        )
        anonymous = Client()
        for url in self.pages().values():
            with self.subTest(url=url):
                self.assertEqual(member.get(url).status_code, 403)
                denied = anonymous.get(url)
                self.assertEqual(denied.status_code, 302)
                self.assertIn("login", denied["Location"])

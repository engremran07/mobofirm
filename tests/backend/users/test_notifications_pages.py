"""The notification list is paginated, scoped to its owner and constant-cost."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.users.models import Notification
from apps.users.views_notifications import NOTIFICATIONS_PAGE_SIZE


def make_user(name: str):
    return get_user_model().objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password="Pass-12345!",
        profile_completed=True,
    )


class NotificationListTests(TestCase):
    def setUp(self):
        self.user = make_user("reader")
        self.other = make_user("bystander")
        self.client.force_login(self.user)

    def create(self, count: int, recipient=None) -> None:
        Notification.objects.bulk_create(
            Notification(
                recipient=recipient or self.user, title=f"Note {n}", message="body"
            )
            for n in range(count)
        )

    def test_one_page_holds_at_most_the_page_size(self):
        self.create(NOTIFICATIONS_PAGE_SIZE + 5)

        first = self.client.get(reverse("users:notifications"))
        second = self.client.get(reverse("users:notifications"), {"page": 2})

        self.assertEqual(len(first.context["notifications"]), NOTIFICATIONS_PAGE_SIZE)
        self.assertEqual(len(second.context["notifications"]), 5)
        self.assertContains(first, "Notification pages")

    def test_only_the_owners_notifications_are_listed(self):
        self.create(2)
        self.create(3, recipient=self.other)

        response = self.client.get(reverse("users:notifications"))

        self.assertEqual(len(response.context["notifications"]), 2)

    def test_the_unread_scope_filters_and_survives_paging(self):
        self.create(NOTIFICATIONS_PAGE_SIZE + 2)
        Notification.objects.filter(recipient=self.user).update(is_read=True)
        Notification.objects.filter(recipient=self.user, title="Note 1").update(
            is_read=False
        )

        response = self.client.get(reverse("users:notifications"), {"scope": "unread"})

        self.assertEqual(
            [n.title for n in response.context["notifications"]], ["Note 1"]
        )

    def test_the_cost_does_not_grow_with_the_number_of_notifications(self):
        self.client.get(reverse("users:notifications"))  # warm per-process caches
        self.create(3)
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("users:notifications"))
        self.create(NOTIFICATIONS_PAGE_SIZE * 2)
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse("users:notifications"))

        self.assertEqual(len(many), len(few))

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        self.client.logout()

        response = self.client.get(reverse("users:notifications"))

        self.assertEqual(response.status_code, 302)

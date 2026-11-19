"""Authorization of the users REST endpoints: staff are not write access to other people."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from rest_framework.test import APIClient

from apps.users.profile_models import Profile


def make_user(name: str, **fields):
    return get_user_model().objects.create_user(
        username=name, email=f"{name}@example.com", password="Pass-12345!", **fields
    )


def with_permissions(user, *codenames: str):
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return get_user_model().objects.get(pk=user.pk)


class ProfileApiAuthorizationTests(TestCase):
    def setUp(self):
        self.member = make_user("member")
        self.member_profile = Profile.objects.create(user=self.member, bio="mine")
        self.staff = make_user("staffer", is_staff=True)
        self.staff_profile = Profile.objects.create(user=self.staff, bio="staff bio")

    def client_for(self, user) -> APIClient:
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_plain_staff_see_only_their_own_profile(self):
        response = self.client_for(self.staff).get("/users/api/profiles/")

        self.assertEqual(
            [row["id"] for row in response.json()], [self.staff_profile.pk]
        )

    def test_staff_with_the_view_permission_can_read_every_profile(self):
        viewer = with_permissions(self.staff, "view_profile")

        response = self.client_for(viewer).get("/users/api/profiles/")

        self.assertEqual(len(response.json()), 2)

    def test_staff_cannot_change_or_delete_another_profile_even_with_view(self):
        viewer = with_permissions(self.staff, "view_profile", "change_profile")
        client = self.client_for(viewer)
        url = f"/users/api/profiles/{self.member_profile.pk}/"

        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(
            client.patch(url, {"bio": "defaced"}, format="json").status_code, 404
        )
        self.assertEqual(client.delete(url).status_code, 404)
        self.member_profile.refresh_from_db()
        self.assertEqual(self.member_profile.bio, "mine")

    def test_social_links_must_be_an_object(self):
        client = self.client_for(self.member)
        url = f"/users/api/profiles/{self.member_profile.pk}/"

        rejected = client.patch(
            url, {"social_links": ["not", "an", "object"]}, format="json"
        )
        accepted = client.patch(url, {"social_links": {"site": "x"}}, format="json")

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(accepted.status_code, 200)


class NotificationStatsAuthorizationTests(TestCase):
    URL = "/users/admin/users/notification/stats/"

    def test_members_and_plain_staff_are_refused_but_permitted_staff_get_counts(self):
        member = make_user("plain")
        staff = make_user("stats-staff", is_staff=True)
        client = Client()

        client.force_login(member)
        self.assertEqual(client.get(self.URL).status_code, 403)
        client.force_login(staff)
        self.assertEqual(client.get(self.URL).status_code, 403)
        client.force_login(with_permissions(staff, "view_notification"))
        response = client.get(self.URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {"unread", "active_users", "push_subscriptions", "today_notifications"},
        )

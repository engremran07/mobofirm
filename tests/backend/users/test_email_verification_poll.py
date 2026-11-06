from __future__ import annotations

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from apps.users.forms import TellUsAboutYouForm
from apps.users.services.email_verification import SESSION_KEY


def _pending_address(*, verified: bool) -> EmailAddress:
    user = get_user_model().objects.create_user(
        username="poller", email="poller@example.test", password="Str0ng-Passw0rd!"
    )
    return EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=verified
    )


class VerificationStatusPollTests(TestCase):
    def _hold_pending(self, address: EmailAddress) -> None:
        session = self.client.session
        session[SESSION_KEY] = {"email_address_id": address.pk}
        session.save()

    def test_anonymous_poll_reports_pending_then_verified(self) -> None:
        address = _pending_address(verified=False)
        self._hold_pending(address)

        pending = self.client.get(reverse("users:verify_email_status"))
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json(), {"verified": False, "redirect": None})

        address.verified = True
        address.save(update_fields=["verified"])

        done = self.client.get(reverse("users:verify_email_status"))
        self.assertTrue(done.json()["verified"])
        self.assertEqual(done.json()["redirect"], "/users/login/")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_htmx_poll_is_silent_until_verified_then_redirects(self) -> None:
        address = _pending_address(verified=False)
        self._hold_pending(address)
        url = reverse("users:verify_email_status")

        waiting = self.client.get(url, headers={"HX-Request": "true"})
        self.assertEqual(waiting.status_code, 204)
        self.assertNotIn("HX-Redirect", waiting.headers)

        address.verified = True
        address.save(update_fields=["verified"])
        finished = self.client.get(url, headers={"HX-Request": "true"})
        self.assertEqual(finished.status_code, 204)
        self.assertEqual(finished.headers["HX-Redirect"], "/users/login/")

    def test_poll_never_reports_another_accounts_state(self) -> None:
        _pending_address(verified=True)

        response = self.client.get(reverse("users:verify_email_status"))

        self.assertEqual(response.json(), {"verified": False, "redirect": None})

    def test_signed_in_user_is_sent_to_the_dashboard_once_verified(self) -> None:
        address = _pending_address(verified=False)
        user = address.user
        self.client.force_login(user)
        url = reverse("users:verify_email_status")
        self.assertFalse(self.client.get(url).json()["verified"])

        user.mark_email_verified()
        body = self.client.get(url).json()

        self.assertTrue(body["verified"])
        self.assertEqual(body["redirect"], reverse("users:dashboard"))

    def test_verification_pages_include_the_poller(self) -> None:
        address = _pending_address(verified=False)
        self._hold_pending(address)

        page = self.client.get(reverse("users:verify_email"))

        self.assertContains(page, reverse("users:verify_email_status"))
        self.assertContains(page, 'hx-trigger="every 5s"')

    def test_verified_pending_session_moves_on_to_sign_in(self) -> None:
        address = _pending_address(verified=True)
        self._hold_pending(address)

        response = self.client.get(reverse("users:verify_email"))

        self.assertRedirects(response, "/users/login/", fetch_redirect_response=False)


class ChoiceGroupRenderingTests(TestCase):
    def test_interest_checkboxes_render_as_a_bootstrap_check_group(self) -> None:
        form = TellUsAboutYouForm()
        html = Template("{% load mf_ui %}{{ field|mf_widget }}").render(
            Context({"field": form["interests"]})
        )

        self.assertIn('class="mf-choice-group"', html)
        self.assertEqual(html.count('class="form-check"'), 8)
        self.assertEqual(html.count('type="checkbox"'), 8)
        self.assertIn('for="id_interests_0"', html)

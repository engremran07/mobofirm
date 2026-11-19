"""Account erasure / anonymisation in place (audit F-041)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token

from apps.users.models import (
    Announcement,
    CustomUser,
    MFADevice,
    Notification,
    NotificationPreferences,
    PushSubscription,
    SecurityQuestion,
)
from apps.users.models_erasure import AccountErasure
from apps.users.profile_models import ActivityFeed, Following, Profile, Reputation
from apps.users.services import erasure
from apps.users.tasks import process_account_erasures

PASSWORD = "Strong-pass-4271!"
EMAIL = "subject@example.test"
EXPORT = "apps.users.services.erasure.AppService.export"


def make_member(
    email: str = EMAIL,
    username: str = "subject",
    phone: str | None = "+15551230000",
    **fields,
) -> CustomUser:
    user = get_user_model().objects.create_user(
        email=email,
        username=username,
        password=PASSWORD,
        full_name="Sam Subject",
        phone=phone,
        country="US",
        bio="About me",
        profile_completed=True,
        email_verified_at=timezone.now(),
        **fields,
    )
    EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)
    return user


def make_operator(*, permitted: bool = True, name: str = "operator") -> CustomUser:
    operator = get_user_model().objects.create_user(
        email=f"{name}@example.test",
        username=name,
        password=PASSWORD,
        is_staff=True,
    )
    if permitted:
        operator.user_permissions.add(
            Permission.objects.get(codename="anonymize_account")
        )
    return get_user_model().objects.get(pk=operator.pk)


def populate(user: CustomUser) -> CustomUser:
    """Give ``user`` personal data in every users-owned table."""
    other = get_user_model().objects.create_user(
        email="neighbour@example.test", username="neighbour", password=PASSWORD
    )
    Profile.objects.create(user=user, bio="Bio", website="https://example.test")
    Reputation.objects.create(user=user, total_score=10)
    Following.objects.create(follower=user, following=other)
    Following.objects.create(follower=other, following=user)
    ActivityFeed.objects.create(user=user, action_type="upload", metadata={"n": 1})
    Notification.objects.create(recipient=user, title="Hello", message="m")
    Notification.objects.create(
        recipient=other, title="From subject", message="m", actor=user
    )
    NotificationPreferences.objects.create(user=user)
    PushSubscription.objects.create(
        user=user, endpoint="https://push.example.test/x", p256dh="k", auth="a"
    )
    question = SecurityQuestion(user=user)
    question.set_answer("rex")
    question.save()
    MFADevice.objects.create(user=user, device_type="totp", name="App")
    SocialAccount.objects.create(user=user, provider="google", uid="g-1")
    Token.objects.create(user=user)
    Announcement.objects.create(title="News", message="m", created_by=user)
    LogEntry.objects.create(
        user=other,
        content_type=ContentType.objects.get_for_model(CustomUser),
        object_id=str(user.pk),
        object_repr=user.email,
        action_flag=CHANGE,
        change_message="edited Sam Subject",
    )
    return user


def full_handlers() -> dict[str, Mock]:
    """A handler for every installed app, as the other apps will export."""
    return {
        label: Mock(return_value={"rows_anonymised": 2})
        for label in erasure.installed_app_labels()
    }


def export_for(handlers: dict[str, Mock], blockers: dict[str, Mock] | None = None):
    """Stand-in for ``AppService.export``: each app's anonymiser and blocker check."""

    def export(label: str, name: str):
        if name == erasure.HANDLER_EXPORT:
            return handlers.get(label)
        if name == erasure.BLOCKERS_EXPORT:
            return (blockers or {}).get(label)
        return None

    return export


class StaffErasureTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = populate(make_member())
        self.operator = make_operator()

    def erase(self, handlers: dict[str, Mock] | None = None) -> AccountErasure:
        with patch(EXPORT, side_effect=export_for(handlers or {})):
            return erasure.anonymize_account(
                self.user, actor=self.operator, reason="Subject request #1"
            )

    def test_identity_credentials_and_personal_rows_are_gone(self):
        browser = Client()
        browser.force_login(self.user)
        original_pk = self.user.pk

        result = self.erase(full_handlers())

        user = get_user_model().objects.get(pk=original_pk)
        self.assertEqual(result.status, AccountErasure.Status.COMPLETED)
        self.assertEqual(user.email, f"erased-{original_pk}@erased.invalid")
        self.assertEqual(user.username, f"erased-{original_pk}")
        for field in ("full_name", "bio", "country", "phone_country_code"):
            self.assertEqual(getattr(user, field), "", field)
        self.assertIsNone(user.phone)
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertIsNotNone(user.anonymized_at)
        self.assertEqual(user.public_display_name, "Former member")
        for model in (
            Profile,
            Reputation,
            ActivityFeed,
            NotificationPreferences,
            PushSubscription,
            SecurityQuestion,
            MFADevice,
            EmailAddress,
            SocialAccount,
            Token,
        ):
            self.assertFalse(model.objects.filter(user=user).exists(), model.__name__)
        self.assertFalse(
            Following.objects.filter(follower=user).exists()
            or Following.objects.filter(following=user).exists()
        )
        self.assertFalse(Notification.objects.filter(recipient=user).exists())
        detached = Notification.objects.get(title="From subject")
        self.assertIsNone(detached.actor)
        self.assertIsNone(Announcement.objects.get(title="News").created_by)
        entry = LogEntry.objects.get(object_id=str(original_pk))
        self.assertNotIn("Sam", entry.change_message)
        self.assertNotIn("example.test", entry.object_repr)
        self.assertEqual(
            browser.get(reverse("users:dashboard")).status_code, 302
        )  # the old session no longer signs anyone in
        self.assertFalse(
            Session.objects.filter(session_key=browser.session.session_key).exists()
        )

    def test_the_audit_record_is_the_tombstone_and_holds_no_personal_data(self):
        result = self.erase(full_handlers())

        self.assertEqual(result.user_pk, self.user.pk)
        self.assertEqual(result.source, AccountErasure.Source.STAFF)
        self.assertEqual(result.requested_by, self.operator)
        self.assertEqual(result.reason, "Subject request #1")
        self.assertIsNotNone(result.executed_at)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.receipt["gaps"], [])
        self.assertEqual(result.receipt["apps"]["devices"], {"rows_anonymised": 2})
        self.assertGreater(result.receipt["users"]["notifications"], 0)
        serialized = str(result.receipt) + result.reason + result.failure_reason
        self.assertNotIn("example.test", serialized)

    def test_every_app_handler_is_called_once_with_the_account(self):
        handlers = full_handlers()

        result = self.erase(handlers)

        for label, handler in handlers.items():
            handler.assert_called_once()
            self.assertEqual(handler.call_args.args[0].pk, self.user.pk)
            self.assertIn(label, result.receipt["apps"])

    def test_a_handler_may_report_with_a_dataclass_like_the_wallet_does(self):
        @dataclass(frozen=True)
        class Report:
            ledger_notes: int = 4
            reconciled: bool = True
            label: str = "ignored"

        handlers = full_handlers()
        handlers["wallet"] = Mock(return_value=Report())

        result = self.erase(handlers)

        self.assertEqual(
            result.receipt["apps"]["wallet"], {"ledger_notes": 4, "reconciled": 1}
        )

    def test_apps_without_a_handler_are_reported_as_gaps_not_hidden(self):
        handlers = full_handlers()
        del handlers["wallet"]
        del handlers["blog"]

        result = self.erase(handlers)

        self.assertEqual(result.status, AccountErasure.Status.PARTIAL)
        self.assertEqual(result.receipt["gaps"], ["blog", "wallet"])
        self.assertIsNotNone(
            get_user_model().objects.get(pk=self.user.pk).anonymized_at
        )

    def test_a_second_run_completes_the_gaps_and_leaves_the_users_part_alone(self):
        handlers = full_handlers()
        wallet = handlers.pop("wallet")
        first = self.erase(handlers)
        anonymized_at = get_user_model().objects.get(pk=self.user.pk).anonymized_at

        handlers["wallet"] = wallet
        second = self.erase(handlers)

        self.assertEqual(first.status, AccountErasure.Status.PARTIAL)
        self.assertEqual(second.status, AccountErasure.Status.COMPLETED)
        self.assertNotIn("users", second.receipt)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(
            get_user_model().objects.get(pk=self.user.pk).anonymized_at, anonymized_at
        )

    def test_replaying_a_finished_request_runs_nothing_again(self):
        handlers = full_handlers()
        result = self.erase(handlers)

        with patch(EXPORT, side_effect=export_for(handlers)):
            replay = erasure.execute_erasure(result.pk)

        self.assertEqual(replay.status, AccountErasure.Status.COMPLETED)
        self.assertEqual(replay.attempts, 1)
        for handler in handlers.values():
            handler.assert_called_once()

    def test_without_the_permission_nothing_happens(self):
        outsider = make_operator(permitted=False, name="outsider")

        with self.assertRaises(PermissionDenied):
            erasure.anonymize_account(self.user, actor=outsider, reason="because")

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, EMAIL)
        self.assertFalse(AccountErasure.objects.exists())

    def test_a_reason_is_required(self):
        with self.assertRaises(erasure.ErasureRefused) as raised:
            erasure.anonymize_account(self.user, actor=self.operator, reason="  ")

        self.assertEqual(raised.exception.code, "reason_required")
        self.assertFalse(AccountErasure.objects.exists())

    def test_privileged_accounts_must_be_demoted_first(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

        with self.assertRaises(erasure.ErasureRefused) as raised:
            erasure.anonymize_account(self.user, actor=self.operator, reason="why")

        self.assertEqual(raised.exception.code, "privileged_account")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, EMAIL)
        self.assertTrue(Profile.objects.filter(user=self.user).exists())


class RollbackAndRetryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = populate(make_member())
        self.operator = make_operator()

    def failing_handlers(self) -> dict[str, Mock]:
        handlers = full_handlers()
        handlers["wallet"] = Mock(
            side_effect=RuntimeError("wallet says sam@example.test")
        )
        return handlers

    def run_staff_erasure(self, handlers):
        with patch(EXPORT, side_effect=export_for(handlers)):
            return erasure.anonymize_account(
                self.user, actor=self.operator, reason="request"
            )

    def test_a_failing_app_rolls_the_whole_erasure_back(self):
        result = self.run_staff_erasure(self.failing_handlers())

        user = get_user_model().objects.get(pk=self.user.pk)
        self.assertEqual(user.email, EMAIL)
        self.assertTrue(user.is_active)
        self.assertTrue(user.has_usable_password())
        self.assertIsNone(user.anonymized_at)
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertTrue(EmailAddress.objects.filter(user=user).exists())
        self.assertTrue(Notification.objects.filter(recipient=user).exists())
        self.assertEqual(result.status, AccountErasure.Status.SCHEDULED)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.failure_reason, "RuntimeError")
        self.assertNotIn("example.test", result.failure_reason)

    def test_attempts_are_bounded_and_the_last_failure_is_terminal(self):
        handlers = self.failing_handlers()
        first = self.run_staff_erasure(handlers)

        with patch(EXPORT, side_effect=export_for(handlers)):
            second = erasure.execute_erasure(first.pk)
            third = erasure.execute_erasure(first.pk)
            fourth = erasure.execute_erasure(first.pk)

        self.assertEqual(second.status, AccountErasure.Status.SCHEDULED)
        self.assertEqual(third.status, AccountErasure.Status.FAILED)
        self.assertEqual(third.attempts, 3)
        self.assertEqual(fourth.attempts, 3)  # no further attempt is ever made
        self.assertEqual(handlers["wallet"].call_count, 3)
        self.assertIsNone(get_user_model().objects.get(pk=self.user.pk).anonymized_at)

    def test_a_handler_returning_junk_is_a_failure_not_a_pass(self):
        handlers = full_handlers()
        handlers["blog"] = Mock(return_value="done")

        result = self.run_staff_erasure(handlers)

        self.assertEqual(result.status, AccountErasure.Status.SCHEDULED)
        self.assertEqual(result.failure_reason, "TypeError")
        self.assertIsNone(get_user_model().objects.get(pk=self.user.pk).anonymized_at)

    def test_a_failed_request_can_be_started_again_by_staff(self):
        handlers = self.failing_handlers()
        first = self.run_staff_erasure(handlers)
        with patch(EXPORT, side_effect=export_for(handlers)):
            erasure.execute_erasure(first.pk)
            erasure.execute_erasure(first.pk)

        fixed = self.run_staff_erasure(full_handlers())

        self.assertEqual(fixed.status, AccountErasure.Status.COMPLETED)
        self.assertEqual(AccountErasure.objects.count(), 2)

    def test_the_account_is_refused_if_promoted_before_it_runs(self):
        result = self.run_staff_erasure(self.failing_handlers())
        get_user_model().objects.filter(pk=self.user.pk).update(is_superuser=True)

        with patch(EXPORT, side_effect=export_for(full_handlers())):
            final = erasure.execute_erasure(result.pk)

        self.assertEqual(final.status, AccountErasure.Status.FAILED)
        self.assertEqual(final.failure_reason, "privileged_account")


@override_settings(ACCOUNT_ERASURE_GRACE_DAYS=7)
class SelfServiceErasureTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.user = populate(make_member())
        self.browser = Client()
        self.browser.force_login(self.user)
        session = self.browser.session
        session["mfa_verified_user_id"] = str(self.user.pk)
        session.save()

    def request_erasure(self, password: str = PASSWORD):
        with self.captureOnCommitCallbacks(execute=True):
            return self.browser.post(
                reverse("users:account_erasure"),
                {"action": "request", "password": password},
            )

    def emailed_token(self) -> str:
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        marker = "/users/account/erasure/confirm/"
        start = body.index(marker) + len(marker)
        return body[start : body.index("/", start)]

    def confirm(self, token: str):
        return self.browser.post(reverse("users:account_erasure_confirm", args=[token]))

    def test_the_page_renders_the_request_form_with_csrf(self):
        response = self.browser.get(reverse("users:account_erasure"))

        self.assertContains(response, "Email me a confirmation link")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertNotContains(response, "confirm(")

    def test_a_wrong_password_starts_nothing(self):
        response = self.request_erasure("not-the-password")

        self.assertRedirects(
            response, reverse("users:account_erasure"), fetch_redirect_response=False
        )
        self.assertFalse(AccountErasure.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_an_unverified_address_cannot_request_erasure(self):
        EmailAddress.objects.filter(user=self.user).update(verified=False)

        self.request_erasure()

        self.assertFalse(AccountErasure.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_privileged_accounts_are_refused(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

        self.request_erasure()

        self.assertFalse(AccountErasure.objects.exists())

    def test_request_emails_a_link_and_changes_nothing_yet(self):
        response = self.request_erasure()

        self.assertRedirects(
            response, reverse("users:account_erasure"), fetch_redirect_response=False
        )
        record = AccountErasure.objects.get()
        self.assertEqual(record.status, AccountErasure.Status.AWAITING_CONFIRMATION)
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        self.assertNotIn(self.emailed_token(), record.token_hash)  # only a hash is kept
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, EMAIL)

    def test_asking_again_refreshes_the_link_and_invalidates_the_old_one(self):
        self.request_erasure()
        old = self.emailed_token()
        mail.outbox.clear()

        self.request_erasure()
        new = self.emailed_token()

        self.assertNotEqual(old, new)
        self.assertEqual(AccountErasure.objects.count(), 1)
        self.confirm(old)
        self.assertEqual(
            AccountErasure.objects.get().status,
            AccountErasure.Status.AWAITING_CONFIRMATION,
        )

    def test_opening_the_link_changes_nothing_only_the_post_confirms(self):
        self.request_erasure()
        token = self.emailed_token()

        page = self.browser.get(reverse("users:account_erasure_confirm", args=[token]))

        self.assertContains(page, "Confirm erasure")
        self.assertEqual(
            AccountErasure.objects.get().status,
            AccountErasure.Status.AWAITING_CONFIRMATION,
        )

    def test_confirming_schedules_the_erasure_after_the_grace_period(self):
        self.request_erasure()
        token = self.emailed_token()

        response = self.confirm(token)

        self.assertRedirects(
            response, reverse("users:account_erasure"), fetch_redirect_response=False
        )
        record = AccountErasure.objects.get()
        self.assertEqual(record.status, AccountErasure.Status.SCHEDULED)
        assert record.scheduled_for is not None
        self.assertAlmostEqual(
            record.scheduled_for,
            timezone.now() + timedelta(days=7),
            delta=timedelta(minutes=1),
        )
        self.assertEqual(record.token_hash, "")
        self.assertEqual(erasure.run_due_erasures()["processed"], 0)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, EMAIL)
        self.assertContains(
            self.browser.get(reverse("users:account_erasure")), "scheduled"
        )

    def test_a_confirmation_link_works_once_and_only_for_its_owner(self):
        self.request_erasure()
        token = self.emailed_token()
        stranger = Client()
        stranger.force_login(make_member("other@example.test", "other", phone=None))
        stranger.post(reverse("users:account_erasure_confirm", args=[token]))
        self.assertEqual(
            AccountErasure.objects.get().status,
            AccountErasure.Status.AWAITING_CONFIRMATION,
        )

        self.confirm(token)
        scheduled_for = AccountErasure.objects.get().scheduled_for
        self.confirm(token)

        self.assertEqual(AccountErasure.objects.get().scheduled_for, scheduled_for)

    def test_an_expired_link_is_refused(self):
        self.request_erasure()
        token = self.emailed_token()
        AccountErasure.objects.update(
            token_expires_at=timezone.now() - timedelta(minutes=1)
        )

        self.confirm(token)

        self.assertEqual(
            AccountErasure.objects.get().status,
            AccountErasure.Status.AWAITING_CONFIRMATION,
        )

    def test_cancelling_during_the_grace_period_keeps_the_account(self):
        self.request_erasure()
        self.confirm(self.emailed_token())

        response = self.browser.post(
            reverse("users:account_erasure"), {"action": "cancel"}
        )

        self.assertRedirects(
            response, reverse("users:account_erasure"), fetch_redirect_response=False
        )
        self.assertEqual(
            AccountErasure.objects.get().status, AccountErasure.Status.CANCELLED
        )
        AccountErasure.objects.update(scheduled_for=timezone.now() - timedelta(days=1))
        self.assertEqual(erasure.run_due_erasures()["processed"], 0)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, EMAIL)
        self.request_erasure()  # a new request can be opened afterwards
        self.assertEqual(AccountErasure.objects.count(), 2)

    def test_when_the_grace_period_ends_the_task_erases_and_tells_the_former_holder(
        self,
    ):
        self.request_erasure()
        self.confirm(self.emailed_token())
        mail.outbox.clear()
        AccountErasure.objects.update(
            scheduled_for=timezone.now() - timedelta(minutes=1)
        )

        with (
            patch(EXPORT, side_effect=export_for(full_handlers())),
            self.captureOnCommitCallbacks(execute=True),
        ):
            outcome = process_account_erasures()

        self.assertEqual(outcome["completed"], 1)
        record = AccountErasure.objects.get()
        self.assertEqual(record.source, AccountErasure.Source.SELF_SERVICE)
        self.assertEqual(record.status, AccountErasure.Status.COMPLETED)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email.endswith("@erased.invalid"))
        self.assertEqual([message.to for message in mail.outbox], [[EMAIL]])
        self.assertEqual(process_account_erasures()["processed"], 0)

    def test_the_management_command_runs_due_requests_and_prints_counts_only(self):
        self.request_erasure()
        self.confirm(self.emailed_token())
        AccountErasure.objects.update(
            scheduled_for=timezone.now() - timedelta(minutes=1)
        )
        out = StringIO()

        with patch(EXPORT, side_effect=export_for(full_handlers())):
            call_command("process_account_erasures", stdout=out)

        self.assertIn("processed=1", out.getvalue())
        self.assertNotIn("example.test", out.getvalue())

    def test_only_one_open_request_can_exist_per_account(self):
        from django.db import IntegrityError, transaction

        self.request_erasure()

        with self.assertRaises(IntegrityError), transaction.atomic():
            AccountErasure.objects.create(
                user_pk=self.user.pk,
                source=AccountErasure.Source.SELF_SERVICE,
                status=AccountErasure.Status.SCHEDULED,
            )

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        response = Client().get(reverse("users:account_erasure"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("users:account_login"), response["Location"])


class StaffAdminActionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = populate(make_member())

    def post_action(
        self,
        operator: CustomUser,
        reason: str = "Verified request",
        confirmation: str = "ERASE",
    ):
        client = Client()
        client.force_login(operator)
        with patch(EXPORT, side_effect=export_for(full_handlers())):
            return client.post(
                reverse("admin:users_customuser_anonymize", args=[self.user.pk]),
                {
                    "reason": reason,
                    "confirmation": confirmation,
                    "_form_submitted": "1",
                },
            )

    def test_an_authorised_operator_erases_the_account_from_the_console(self):
        operator = make_operator()
        operator.user_permissions.add(
            Permission.objects.get(codename="view_customuser"),
            Permission.objects.get(codename="change_customuser"),
        )
        operator = get_user_model().objects.get(pk=operator.pk)

        response = self.post_action(operator)

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.anonymized_at)
        self.assertEqual(AccountErasure.objects.get().reason, "Verified request")

    def test_the_typed_phrase_is_required(self):
        operator = make_operator()
        operator.user_permissions.add(
            Permission.objects.get(codename="view_customuser"),
            Permission.objects.get(codename="change_customuser"),
        )
        operator = get_user_model().objects.get(pk=operator.pk)

        response = self.post_action(operator, confirmation="erase maybe")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.anonymized_at)

    def test_an_operator_without_the_permission_cannot_reach_the_action(self):
        operator = make_operator(permitted=False)
        operator.user_permissions.add(
            Permission.objects.get(codename="view_customuser"),
            Permission.objects.get(codename="change_customuser"),
        )
        operator = get_user_model().objects.get(pk=operator.pk)

        response = self.post_action(operator)

        self.assertIn(response.status_code, {403, 404})
        self.user.refresh_from_db()
        self.assertIsNone(self.user.anonymized_at)
        self.assertFalse(AccountErasure.objects.exists())


class BlockerTests(TestCase):
    """An app that still needs the personal data (open payout, escrow) holds erasure."""

    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.user = populate(make_member())
        self.operator = make_operator()
        self.handlers = full_handlers()
        self.open_payout = Mock(return_value=("1 payout request(s) still open",))
        self.blockers = {"wallet": self.open_payout}

    def staff_erase(self):
        with patch(EXPORT, side_effect=export_for(self.handlers, self.blockers)):
            return erasure.anonymize_account(
                self.user, actor=self.operator, reason="Subject request"
            )

    def scheduled_request(self) -> AccountErasure:
        return AccountErasure.objects.create(
            user=self.user,
            user_pk=self.user.pk,
            source=AccountErasure.Source.SELF_SERVICE,
            status=AccountErasure.Status.SCHEDULED,
            confirmed_at=timezone.now() - timedelta(days=2),
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )

    def test_staff_are_refused_with_the_reasons_and_nothing_changes(self):
        with self.assertRaises(erasure.ErasureBlocked) as raised:
            self.staff_erase()

        self.assertEqual(
            raised.exception.blockers, {"wallet": ["1 payout request(s) still open"]}
        )
        self.assertIn(
            "wallet: 1 payout request(s) still open", raised.exception.summary
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, EMAIL)
        self.assertFalse(AccountErasure.objects.exists())
        for handler in self.handlers.values():
            handler.assert_not_called()

    def test_the_self_service_request_is_refused_while_blocked(self):
        with (
            patch(EXPORT, side_effect=export_for(self.handlers, self.blockers)),
            self.assertRaises(erasure.ErasureBlocked),
        ):
            erasure.start_self_service_erasure(
                self.user, build_link=lambda token: f"https://x.test/{token}"
            )

        self.assertFalse(AccountErasure.objects.exists())

    def test_a_blocker_after_confirmation_makes_the_request_wait_without_failing(self):
        record = self.scheduled_request()

        with patch(EXPORT, side_effect=export_for(self.handlers, self.blockers)):
            waiting = erasure.execute_erasure(record.pk)

        self.assertEqual(waiting.status, AccountErasure.Status.SCHEDULED)
        self.assertEqual(waiting.attempts, 0)  # waiting is not a failed attempt
        self.assertEqual(waiting.failure_reason, "blocked:wallet")
        self.assertEqual(
            waiting.receipt,
            {"blockers": {"wallet": ["1 payout request(s) still open"]}},
        )
        assert waiting.scheduled_for is not None
        self.assertGreater(waiting.scheduled_for, timezone.now() + timedelta(hours=23))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, EMAIL)
        self.assertEqual(
            erasure.run_due_erasures()["processed"], 0
        )  # not due again yet

    def test_waiting_never_uses_up_the_attempts(self):
        record = self.scheduled_request()
        for _ in range(erasure.max_attempts() + 2):
            AccountErasure.objects.filter(pk=record.pk).update(
                scheduled_for=timezone.now() - timedelta(minutes=1)
            )
            with patch(EXPORT, side_effect=export_for(self.handlers, self.blockers)):
                waiting = erasure.execute_erasure(record.pk)

        self.assertEqual(waiting.status, AccountErasure.Status.SCHEDULED)
        self.assertEqual(waiting.attempts, 0)

    def test_the_request_completes_once_the_blocker_clears(self):
        record = self.scheduled_request()
        with patch(EXPORT, side_effect=export_for(self.handlers, self.blockers)):
            erasure.execute_erasure(record.pk)
        self.open_payout.return_value = ()
        AccountErasure.objects.filter(pk=record.pk).update(
            scheduled_for=timezone.now() - timedelta(minutes=1)
        )

        with patch(EXPORT, side_effect=export_for(self.handlers, self.blockers)):
            done = erasure.execute_erasure(record.pk)

        self.assertEqual(done.status, AccountErasure.Status.COMPLETED)
        self.assertEqual(done.failure_reason, "")
        self.assertIsNotNone(
            get_user_model().objects.get(pk=self.user.pk).anonymized_at
        )

    @override_settings(ACCOUNT_ERASURE_BLOCKED_MAX_DAYS=1)
    def test_a_blocker_that_never_clears_ends_in_a_failure_for_staff(self):
        record = self.scheduled_request()  # confirmed two days ago

        with patch(EXPORT, side_effect=export_for(self.handlers, self.blockers)):
            final = erasure.execute_erasure(record.pk)

        self.assertEqual(final.status, AccountErasure.Status.FAILED)
        self.assertEqual(final.failure_reason, "blocked_timeout")
        self.assertIsNone(get_user_model().objects.get(pk=self.user.pk).anonymized_at)

    def test_the_account_page_names_what_blocks_a_request(self):
        browser = Client()
        browser.force_login(self.user)
        session = browser.session
        session["mfa_verified_user_id"] = str(self.user.pk)
        session.save()

        with patch(EXPORT, side_effect=export_for(self.handlers, self.blockers)):
            browser.post(
                reverse("users:account_erasure"),
                {"action": "request", "password": PASSWORD},
            )
            page = browser.get(reverse("users:account_erasure"))

        self.assertContains(page, "payout request(s) still open")
        self.assertFalse(AccountErasure.objects.exists())

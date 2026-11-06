from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.seo.models import ChangeLedger, CrawlRun, Issue, Redirect, SEOJob

DIALOG = {"_form_submitted": "on"}


def make_entry(key: str) -> ChangeLedger:
    return ChangeLedger.objects.create(
        idempotency_key=key,
        kind="redirect_create",
        action="create",
        target_model="seo.redirect",
        after={
            "source": f"/{key}/",
            "target": "/dest/",
            "is_permanent": True,
            "is_active": True,
        },
        reason="admin test",
        source="test",
    )


class SeoAdminActionTestCase(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.root = user_model.objects.create_superuser(
            username="seo-root", email="seo-root@example.test"
        )
        self.client = Client()
        self.client.force_login(self.root)

    def limited_client(self, *codenames: str) -> Client:
        user = get_user_model().objects.create_user(
            username=f"seo-limited-{len(codenames)}-{codenames[0]}",
            email=f"seo-limited-{codenames[0]}@example.test",
            is_staff=True,
        )
        user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        client = Client()
        client.force_login(user)
        return client


class ChangeLedgerRowActionTests(SeoAdminActionTestCase):
    def url(self, name: str, entry: ChangeLedger) -> str:
        return reverse(f"admin:seo_changeledger_{name}", args=[entry.pk])

    def test_approve_dialog_applies_the_proposal_then_rollback_reverts_it(self) -> None:
        entry = make_entry("approve-me")

        opened = self.client.get(self.url("approve_change", entry))
        done = self.client.post(self.url("approve_change", entry), DIALOG)

        self.assertEqual(opened.status_code, 200)
        self.assertIn("HX-Redirect", done.headers)
        entry.refresh_from_db()
        self.assertEqual(entry.status, ChangeLedger.Status.APPLIED)
        self.assertEqual(entry.decided_by, self.root)
        self.assertTrue(Redirect.objects.filter(source="/approve-me/").exists())

        self.client.post(self.url("rollback_change", entry), DIALOG)

        entry.refresh_from_db()
        self.assertEqual(entry.status, ChangeLedger.Status.ROLLED_BACK)
        self.assertFalse(Redirect.objects.filter(source="/approve-me/").exists())

    def test_reject_dialog_closes_the_proposal_without_applying(self) -> None:
        entry = make_entry("reject-me")

        self.client.post(self.url("reject_change", entry), DIALOG)

        entry.refresh_from_db()
        self.assertEqual(entry.status, ChangeLedger.Status.REJECTED)
        self.assertFalse(Redirect.objects.exists())

    def test_rolling_back_an_unapplied_change_surfaces_the_ledger_error(self) -> None:
        entry = make_entry("not-applied")

        response = self.client.post(self.url("rollback_change", entry), DIALOG)

        self.assertIn("HX-Redirect", response.headers)
        entry.refresh_from_db()
        self.assertEqual(entry.status, ChangeLedger.Status.PROPOSED)

    def test_dialog_and_bulk_actions_require_the_ledger_permissions(self) -> None:
        entry = make_entry("needs-permission")
        viewer = self.limited_client("view_changeledger")

        dialog = viewer.post(self.url("approve_change", entry), DIALOG)
        rollback = viewer.post(self.url("rollback_change", entry), DIALOG)
        bulk = viewer.post(
            reverse("admin:seo_changeledger_changelist"),
            {"action": "approve_selected", "_selected_action": [entry.pk]},
        )

        self.assertEqual(dialog.status_code, 403)
        self.assertEqual(rollback.status_code, 403)
        self.assertEqual(bulk.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.status, ChangeLedger.Status.PROPOSED)

    def test_approver_without_rollback_permission_cannot_roll_back(self) -> None:
        entry = make_entry("approver-only")
        approver = self.limited_client("view_changeledger", "approve_changeledger")

        approved = approver.post(self.url("approve_change", entry), DIALOG)
        rollback = approver.post(self.url("rollback_change", entry), DIALOG)

        self.assertEqual(approved.status_code, 200)
        self.assertEqual(rollback.status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.status, ChangeLedger.Status.APPLIED)

    def test_read_only_pages_render_without_an_add_form(self) -> None:
        entry = make_entry("view-me")

        change = self.client.get(
            reverse("admin:seo_changeledger_change", args=[entry.pk])
        )
        add = self.client.get(reverse("admin:seo_changeledger_add"))

        self.assertEqual(change.status_code, 200)
        self.assertEqual(add.status_code, 403)


class IssueAndJobActionTests(SeoAdminActionTestCase):
    def test_issue_can_be_ignored_and_reopened_from_the_row_actions(self) -> None:
        issue = Issue.objects.create(code="missing_title", url="/x/", message="m")

        self.client.post(
            reverse("admin:seo_issue_ignore_issue", args=[issue.pk]), DIALOG
        )
        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.Status.IGNORED)

        self.client.post(
            reverse("admin:seo_issue_reopen_issue", args=[issue.pk]), DIALOG
        )
        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.Status.OPEN)

    def test_issue_actions_need_the_change_permission(self) -> None:
        issue = Issue.objects.create(code="missing_title", url="/y/", message="m")
        viewer = self.limited_client("view_issue")

        response = viewer.post(
            reverse("admin:seo_issue_ignore_issue", args=[issue.pk]), DIALOG
        )

        self.assertEqual(response.status_code, 403)
        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.Status.OPEN)

    def test_only_failed_jobs_are_retried(self) -> None:
        failed = SEOJob.objects.create(
            kind="x", job_key="failed", status=SEOJob.Status.FAILED, attempts=3
        )
        done = SEOJob.objects.create(
            kind="x", job_key="done", status=SEOJob.Status.SUCCEEDED
        )

        self.client.post(
            reverse("admin:seo_seojob_retry_job", args=[failed.pk]), DIALOG
        )
        self.client.post(reverse("admin:seo_seojob_retry_job", args=[done.pk]), DIALOG)

        failed.refresh_from_db()
        done.refresh_from_db()
        self.assertEqual(failed.status, SEOJob.Status.PENDING)
        self.assertEqual(failed.attempts, 0)
        self.assertEqual(done.status, SEOJob.Status.SUCCEEDED)

    def test_process_jobs_list_action_uses_the_job_runner(self) -> None:
        with mock.patch(
            "apps.seo.admin.process_pending", return_value={"succeeded": 2}
        ) as runner:
            response = self.client.post(
                reverse("admin:seo_seojob_process_jobs"), DIALOG
            )

        self.assertIn("HX-Redirect", response.headers)
        runner.assert_called_once_with()

    def test_job_actions_are_refused_without_change_permission(self) -> None:
        viewer = self.limited_client("view_seojob")

        with mock.patch("apps.seo.admin.process_pending") as runner:
            response = viewer.post(reverse("admin:seo_seojob_process_jobs"), DIALOG)

        self.assertEqual(response.status_code, 403)
        runner.assert_not_called()


class CrawlActionTests(SeoAdminActionTestCase):
    def test_start_crawl_runs_through_the_crawler_service(self) -> None:
        run = CrawlRun.objects.create(base_url="https://example.org")
        with mock.patch("apps.seo.admin.run_crawl", return_value=run) as crawl:
            response = self.client.post(
                reverse("admin:seo_crawlrun_start_crawl"), DIALOG
            )

        self.assertIn("HX-Redirect", response.headers)
        crawl.assert_called_once_with(user=self.root)

    def test_crawl_unavailable_is_reported_not_raised(self) -> None:
        from apps.seo.crawler import CrawlUnavailable

        with mock.patch(
            "apps.seo.admin.run_crawl", side_effect=CrawlUnavailable("switched off")
        ):
            response = self.client.post(
                reverse("admin:seo_crawlrun_start_crawl"), DIALOG
            )

        self.assertIn("HX-Redirect", response.headers)

    def test_start_crawl_needs_the_crawl_permission(self) -> None:
        viewer = self.limited_client("view_crawlrun")

        with mock.patch("apps.seo.admin.run_crawl") as crawl:
            response = viewer.post(reverse("admin:seo_crawlrun_start_crawl"), DIALOG)

        self.assertEqual(response.status_code, 403)
        crawl.assert_not_called()


class SeoAdminPagesTests(SeoAdminActionTestCase):
    def test_every_seo_changelist_and_settings_form_render(self) -> None:
        from django.contrib import admin

        for model in admin.site._registry:
            if model._meta.app_label != "seo":
                continue
            url = reverse(f"admin:seo_{model._meta.model_name}_changelist")
            with self.subTest(model=model._meta.label):
                self.assertEqual(self.client.get(url).status_code, 200)

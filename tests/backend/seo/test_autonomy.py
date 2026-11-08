from __future__ import annotations

import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.firmwares.models import OfficialFirmware
from apps.seo import autonomy, entity_graph, indexnow, jobs, ledger
from apps.seo.fetcher import BlockedDestination, FetchError, FetchResult
from apps.seo.models import ChangeLedger, Redirect, SEOJob, SEOSettings
from tests.backend.seo.seo_helpers import SITE, make_catalog

Mode = SEOSettings.AutonomyMode


def set_mode(mode, **extra):
    row = SEOSettings.get_solo()
    row.autonomy_mode = mode
    for key, value in extra.items():
        setattr(row, key, value)
    row.save()
    return row


def submit_redirect(source="/old/", target="/new/", key=None, kind="redirect_create"):
    return autonomy.submit_change(
        kind=kind,
        action=ChangeLedger.Action.CREATE,
        target_model="seo.redirect",
        target_label=f"{source} -> {target}",
        before={},
        after={
            "source": source,
            "target": target,
            "is_permanent": True,
            "is_active": True,
        },
        reason="test",
        source="test",
        idempotency_key=key or f"test:{source}:{target}",
    )


class PolicyGateTests(TestCase):
    def test_off_mode_records_and_applies_nothing(self):
        set_mode(Mode.OFF)

        self.assertIsNone(submit_redirect())
        self.assertEqual(ChangeLedger.objects.count(), 0)
        self.assertEqual(Redirect.objects.count(), 0)

    def test_suggest_mode_only_proposes(self):
        set_mode(Mode.SUGGEST)

        entry = submit_redirect()

        assert entry is not None
        self.assertEqual(entry.status, ChangeLedger.Status.PROPOSED)
        self.assertTrue(entry.requires_approval)
        self.assertEqual(Redirect.objects.count(), 0)

    def test_safe_autopilot_applies_low_risk_and_holds_the_rest(self):
        set_mode(Mode.SAFE_AUTOPILOT)

        low = submit_redirect()
        high = submit_redirect("/a/", "/b/", kind="redirect_delete")
        protected = submit_redirect("/c/", "/d/", kind="settings_change")

        assert low is not None and high is not None and protected is not None
        self.assertEqual(low.status, ChangeLedger.Status.APPLIED)
        self.assertIsNotNone(low.verified_at)
        self.assertEqual(high.status, ChangeLedger.Status.PROPOSED)
        self.assertEqual(protected.status, ChangeLedger.Status.PROPOSED)
        self.assertEqual(
            list(Redirect.objects.values_list("source", flat=True)), ["/old/"]
        )

    def test_full_mode_applies_high_risk_but_never_protected_policies(self):
        set_mode(Mode.FULL)

        high = submit_redirect("/a/", "/b/", kind="redirect_retarget")
        protected = submit_redirect("/c/", "/d/", kind="settings_change")
        unknown = submit_redirect("/e/", "/f/", kind="something_new")

        assert high and protected and unknown
        self.assertEqual(high.status, ChangeLedger.Status.APPLIED)
        self.assertEqual(protected.status, ChangeLedger.Status.PROPOSED)
        self.assertEqual(unknown.status, ChangeLedger.Status.PROPOSED)

    def test_disabled_seo_never_records(self):
        row = set_mode(Mode.FULL)
        row.seo_enabled = False
        row.save()

        self.assertIsNone(submit_redirect())

    def test_changes_are_idempotent_by_key(self):
        set_mode(Mode.SAFE_AUTOPILOT)

        first = submit_redirect()
        second = submit_redirect()

        assert first and second
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ChangeLedger.objects.count(), 1)
        self.assertEqual(Redirect.objects.count(), 1)

    def test_invalid_changes_fail_visibly_instead_of_applying(self):
        set_mode(Mode.FULL)
        Redirect.objects.create(source="/b/", target="/a/")

        loop = submit_redirect("/a/", "/b/")
        live = submit_redirect("bad-source", "/x/")
        unsafe = submit_redirect("/u/", "//evil.invalid/")

        for entry in (loop, live, unsafe):
            assert entry is not None
            self.assertEqual(entry.status, ChangeLedger.Status.FAILED)
            self.assertTrue(entry.error)
        self.assertEqual(Redirect.objects.count(), 1)

    def test_a_failed_verification_rolls_the_write_back(self):
        set_mode(Mode.SAFE_AUTOPILOT)

        with mock.patch.object(
            ledger, "_verify", side_effect=ledger.LedgerError("state mismatch")
        ):
            entry = submit_redirect()

        assert entry is not None
        self.assertEqual(entry.status, ChangeLedger.Status.FAILED)
        self.assertIn("state mismatch", entry.error)
        self.assertEqual(Redirect.objects.count(), 0)


class ApprovalAndRollbackTests(TestCase):
    def setUp(self):
        set_mode(Mode.SUGGEST)
        self.user = get_user_model().objects.create_user(
            username="approver", email="a@example.com", password="pw-123456-x"
        )

    def test_approval_applies_and_records_the_decider(self):
        entry = submit_redirect()
        assert entry is not None

        applied = ledger.approve(entry, self.user)

        self.assertEqual(applied.status, ChangeLedger.Status.APPLIED)
        self.assertEqual(applied.decided_by, self.user)
        self.assertTrue(Redirect.objects.filter(source="/old/").exists())

    def test_rejection_leaves_state_untouched_and_blocks_reapproval(self):
        entry = submit_redirect()
        assert entry is not None

        ledger.reject(entry, self.user)

        self.assertEqual(entry.status, ChangeLedger.Status.REJECTED)
        self.assertEqual(Redirect.objects.count(), 0)
        with self.assertRaises(ledger.LedgerError):
            ledger.approve(entry, self.user)

    def test_rollback_of_a_create_removes_the_row(self):
        proposed = submit_redirect()
        assert proposed is not None
        entry = ledger.approve(proposed, self.user)

        ledger.rollback(entry, self.user)

        self.assertEqual(entry.status, ChangeLedger.Status.ROLLED_BACK)
        self.assertEqual(Redirect.objects.count(), 0)
        with self.assertRaises(ledger.LedgerError):
            ledger.rollback(entry, self.user)

    def test_rollback_of_an_update_restores_the_previous_value(self):
        redirect = Redirect.objects.create(source="/one/", target="/two/")
        entry = autonomy.submit_change(
            kind="redirect_flatten",
            action=ChangeLedger.Action.UPDATE,
            target_model="seo.redirect",
            target_pk=str(redirect.pk),
            before={"target": "/two/"},
            after={"target": "/three/"},
            reason="test",
            source="test",
            idempotency_key="update-1",
        )
        assert entry is not None
        ledger.approve(entry, self.user)
        redirect.refresh_from_db()
        self.assertEqual(redirect.target, "/three/")

        ledger.rollback(entry, self.user)

        redirect.refresh_from_db()
        self.assertEqual(redirect.target, "/two/")

    def test_rollback_refuses_when_the_target_changed_since(self):
        redirect = Redirect.objects.create(source="/one/", target="/two/")
        entry = autonomy.submit_change(
            kind="redirect_flatten",
            action=ChangeLedger.Action.UPDATE,
            target_model="seo.redirect",
            target_pk=str(redirect.pk),
            before={"target": "/two/"},
            after={"target": "/three/"},
            reason="test",
            source="test",
            idempotency_key="update-2",
        )
        assert entry is not None
        ledger.approve(entry, self.user)
        Redirect.objects.filter(pk=redirect.pk).update(target="/edited-by-human/")

        with self.assertRaises(ledger.LedgerError):
            ledger.rollback(entry, self.user)

        self.assertEqual(
            Redirect.objects.get(pk=redirect.pk).target, "/edited-by-human/"
        )

    def test_manual_edits_are_ledgered_and_reversible(self):
        redirect = Redirect.objects.create(source="/m/", target="/n/")
        before = ledger.serialize(redirect)
        redirect.target = "/o/"
        redirect.save()

        entry = ledger.record_manual(
            user=self.user,
            action=ChangeLedger.Action.UPDATE,
            instance=redirect,
            before=before,
            after=ledger.serialize(redirect),
        )

        assert entry is not None
        self.assertEqual(entry.before, {"target": "/n/"})
        ledger.rollback(entry, self.user)
        redirect.refresh_from_db()
        self.assertEqual(redirect.target, "/n/")

    def test_deleted_rows_can_be_restored(self):
        redirect = Redirect.objects.create(source="/gone/", target="/there/")
        before = ledger.serialize(redirect)
        pk = redirect.pk
        redirect.delete()
        redirect.pk = pk
        entry = ledger.record_manual(
            user=self.user,
            action=ChangeLedger.Action.DELETE,
            instance=redirect,
            before=before,
            after={},
        )
        assert entry is not None

        ledger.rollback(entry, self.user)

        restored = Redirect.objects.get(pk=pk)
        self.assertEqual((restored.source, restored.target), ("/gone/", "/there/"))

    def test_only_listed_models_can_be_changed(self):
        entry = ChangeLedger.objects.create(
            idempotency_key="bad",
            kind="redirect_create",
            action=ChangeLedger.Action.DELETE,
            target_model="auth.user",
            target_pk="1",
            reason="x",
            source="test",
        )

        applied = ledger.apply_change(entry)

        self.assertEqual(applied.status, ChangeLedger.Status.FAILED)


class FakeIndexNowFetcher:
    def __init__(self, status=200, error=None):
        self.status, self.error, self.calls = status, error, []

    def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return FetchResult(url, url, self.status, {}, b"")


@override_settings(SITE_URL=SITE)
class IndexNowTests(TestCase):
    def enable(self):
        return set_mode(
            Mode.SAFE_AUTOPILOT, indexnow_enabled=True, indexnow_key="abc123def4567890"
        )

    def test_submission_uses_the_safe_fetcher_and_only_own_urls(self):
        self.enable()
        fake = FakeIndexNowFetcher(202)

        with mock.patch("apps.seo.fetcher.default_fetcher", return_value=fake):
            result = indexnow.submit(
                [f"{SITE}/a/", "https://other.example/b/", f"{SITE}/a/"]
            )

        self.assertEqual((result.submitted, result.status), (1, 202))
        url, kwargs = fake.calls[0]
        self.assertEqual(url, indexnow.ENDPOINT)
        self.assertEqual(kwargs["method"], "POST")
        self.assertFalse(kwargs["follow_redirects"])
        self.assertEqual(kwargs["allowed_hosts"], indexnow.ENDPOINT_HOSTS)
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["urlList"], [f"{SITE}/a/"])
        self.assertEqual(payload["host"], "example.org")
        self.assertEqual(payload["keyLocation"], f"{SITE}/abc123def4567890.txt")

    def test_disabled_or_unconfigured_submission_is_an_error_not_a_success(self):
        with self.assertRaises(indexnow.IndexNowError):
            indexnow.submit([f"{SITE}/a/"])
        self.enable()
        with override_settings(SITE_URL=""), self.assertRaises(indexnow.IndexNowError):
            indexnow.submit([f"{SITE}/a/"])

    def test_rejections_and_network_failures_are_classified(self):
        self.enable()
        cases = [
            (FakeIndexNowFetcher(403), False),
            (FakeIndexNowFetcher(429), True),
            (FakeIndexNowFetcher(503), True),
            (FakeIndexNowFetcher(error=FetchError("boom")), True),
            (FakeIndexNowFetcher(error=BlockedDestination("blocked")), False),
        ]
        for fake, retryable in cases:
            with (
                self.subTest(status=fake.status, error=fake.error),
                mock.patch("apps.seo.fetcher.default_fetcher", return_value=fake),
                self.assertRaises(indexnow.IndexNowError) as raised,
            ):
                indexnow.submit([f"{SITE}/a/"])
            self.assertEqual(raised.exception.retryable, retryable)


@override_settings(SITE_URL=SITE)
class ContentEventJobTests(TestCase):
    def publish_post(self):
        return make_catalog().post

    def test_publishing_records_one_idempotent_job_after_commit(self):
        set_mode(Mode.SAFE_AUTOPILOT)

        with self.captureOnCommitCallbacks(execute=True):
            post = self.publish_post()
        with self.captureOnCommitCallbacks(execute=True):
            post.save()

        keys = list(
            SEOJob.objects.filter(
                job_key__startswith="content_changed:blog.post:"
            ).values_list("job_key", flat=True)
        )
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(keys)
        job = SEOJob.objects.filter(job_key__contains=f":{post.pk}:").first()
        assert job is not None
        self.assertEqual(job.payload["url"], post.get_absolute_url())
        self.assertTrue(job.payload["public"])

    def test_no_job_is_recorded_before_commit_or_when_off(self):
        set_mode(Mode.OFF)
        with self.captureOnCommitCallbacks(execute=True):
            self.publish_post()
        self.assertEqual(SEOJob.objects.count(), 0)

    def test_duplicate_delivery_collapses_on_the_job_key(self):
        set_mode(Mode.SAFE_AUTOPILOT)
        moment = timezone.now()
        first = jobs.record_content_change(
            "blog.post", "1", url="/blog/x/", public=True, previous=None, now=moment
        )
        second = jobs.record_content_change(
            "blog.post", "1", url="/blog/x/", public=True, previous=None, now=moment
        )

        assert first and second
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(SEOJob.objects.count(), 1)

    def job_for(self, post, **payload):
        return SEOJob.objects.filter(
            payload__label="blog.post",
            payload__pk=str(post.pk),
            **{f"payload__{key}": value for key, value in payload.items()},
        ).get()

    def test_a_moved_public_url_gets_a_redirect_and_an_indexnow_ping(self):
        set_mode(
            Mode.SAFE_AUTOPILOT, indexnow_enabled=True, indexnow_key="abc123def4567890"
        )
        with self.captureOnCommitCallbacks(execute=True):
            post = self.publish_post()
        SEOJob.objects.all().delete()
        old_path = post.get_absolute_url()

        with self.captureOnCommitCallbacks(execute=True):
            post.slug = "renamed-guide"
            post.save()
        fake = FakeIndexNowFetcher(200)
        with mock.patch("apps.seo.fetcher.default_fetcher", return_value=fake):
            jobs.process_pending()

        job = self.job_for(post, previous_url=old_path)
        self.assertEqual(job.status, SEOJob.Status.SUCCEEDED, job.last_error)
        redirect = Redirect.objects.get(source=old_path)
        self.assertEqual(redirect.target, "/blog/renamed-guide/")
        self.assertEqual(self.client.get(old_path).status_code, 301)
        submitted = [
            url
            for _url, kwargs in fake.calls
            for url in json.loads(kwargs["body"])["urlList"]
        ]
        self.assertIn(f"{SITE}/blog/renamed-guide/", submitted)
        entry = ChangeLedger.objects.get(
            kind="redirect_create", target_label__contains=old_path
        )
        self.assertEqual(entry.status, ChangeLedger.Status.APPLIED)
        self.assertEqual(entry.before, {})
        self.assertEqual(entry.after["target"], "/blog/renamed-guide/")

    def test_suggest_mode_proposes_the_redirect_without_applying_or_pinging(self):
        set_mode(Mode.SUGGEST)
        with self.captureOnCommitCallbacks(execute=True):
            post = self.publish_post()
        SEOJob.objects.all().delete()
        old_path = post.get_absolute_url()
        with self.captureOnCommitCallbacks(execute=True):
            post.slug = "renamed-again"
            post.save()

        jobs.process_pending()

        job = self.job_for(post, previous_url=old_path)
        self.assertEqual(job.status, SEOJob.Status.SUCCEEDED, job.last_error)
        self.assertFalse(Redirect.objects.filter(source=old_path).exists())
        entry = ChangeLedger.objects.get(
            kind="redirect_create", target_label__contains=old_path
        )
        self.assertEqual(entry.status, ChangeLedger.Status.PROPOSED)
        self.assertEqual(entry.after["source"], old_path)

    def test_unpublishing_notifies_without_inventing_a_redirect(self):
        set_mode(Mode.SAFE_AUTOPILOT)
        with self.captureOnCommitCallbacks(execute=True):
            post = self.publish_post()
        SEOJob.objects.all().delete()
        with self.captureOnCommitCallbacks(execute=True):
            post.noindex = True
            post.save()

        jobs.process_pending()

        job = self.job_for(post, public=False)
        self.assertEqual(job.status, SEOJob.Status.SKIPPED)
        self.assertEqual(Redirect.objects.count(), 0)

    def test_deleting_published_content_records_a_job(self):
        set_mode(Mode.SAFE_AUTOPILOT)
        with self.captureOnCommitCallbacks(execute=True):
            post = self.publish_post()
        SEOJob.objects.all().delete()
        with self.captureOnCommitCallbacks(execute=True):
            post.delete()

        job = SEOJob.objects.get()
        self.assertFalse(job.payload["public"])
        self.assertTrue(job.payload["previous_public"])

    def test_firmware_publication_is_observed_through_its_registered_source(self):
        set_mode(Mode.SAFE_AUTOPILOT)
        data = make_catalog()
        SEOJob.objects.all().delete()

        with self.captureOnCommitCallbacks(execute=True):
            OfficialFirmware.objects.filter(pk=data.firmware.pk).first()
            data.firmware.is_verified = False
            data.firmware.save()
            data.firmware.is_verified = True
            data.firmware.save()

        labels = set(SEOJob.objects.values_list("payload__label", flat=True))
        self.assertIn("firmwares.officialfirmware", labels)

    def test_edits_that_do_not_change_publication_state_do_not_spam_firmware_jobs(self):
        set_mode(Mode.SAFE_AUTOPILOT)
        data = make_catalog()
        SEOJob.objects.all().delete()

        with self.captureOnCommitCallbacks(execute=True):
            data.firmware.download_count += 1
            data.firmware.save()

        self.assertFalse(
            SEOJob.objects.filter(payload__label="firmwares.officialfirmware").exists()
        )


@override_settings(SITE_URL=SITE)
class JobExecutionTests(TestCase):
    def moved_job(self, **extra):
        set_mode(
            Mode.SAFE_AUTOPILOT,
            indexnow_enabled=True,
            indexnow_key="abc123def4567890",
        )
        return jobs.record_content_change(
            "blog.post",
            "9",
            url="/blog/new/",
            public=True,
            previous=("/blog/old/", True),
            **extra,
        )

    def test_transient_failures_back_off_and_eventually_fail_visibly(self):
        job = self.moved_job()
        assert job is not None
        fake = FakeIndexNowFetcher(503)
        now = timezone.now()

        with mock.patch("apps.seo.fetcher.default_fetcher", return_value=fake):
            jobs.process_pending(now=now)
            job.refresh_from_db()
            self.assertEqual(job.status, SEOJob.Status.PENDING)
            self.assertEqual(job.attempts, 1)
            self.assertGreater(job.available_at, now)
            self.assertIn("503", job.last_error)

            self.assertEqual(jobs.process_pending(now=now), {"reclaimed": 0})

            for attempt in range(2, job.max_attempts + 1):
                jobs.process_pending(now=now + timedelta(hours=attempt))
        job.refresh_from_db()

        self.assertEqual(job.status, SEOJob.Status.FAILED)
        self.assertIn("Retries exhausted", job.last_error)
        self.assertEqual(job.attempts, job.max_attempts)

    def test_permanent_rejections_fail_immediately_and_are_never_success(self):
        job = self.moved_job()
        assert job is not None

        with mock.patch(
            "apps.seo.fetcher.default_fetcher", return_value=FakeIndexNowFetcher(403)
        ):
            jobs.process_pending()
        job.refresh_from_db()

        self.assertEqual(job.status, SEOJob.Status.FAILED)
        self.assertEqual(job.attempts, 1)

    def test_a_job_with_nothing_to_do_is_skipped_not_succeeded(self):
        set_mode(Mode.SUGGEST)
        job = jobs.record_content_change(
            "blog.post",
            "10",
            url="/blog/same/",
            public=True,
            previous=("/blog/same/", True),
        )
        assert job is not None

        jobs.process_pending()
        job.refresh_from_db()

        self.assertEqual(job.status, SEOJob.Status.SKIPPED)

    def test_a_failed_redirect_marks_the_job_failed(self):
        set_mode(Mode.SAFE_AUTOPILOT)
        Redirect.objects.create(source="/blog/new/", target="/blog/old/")
        job = jobs.record_content_change(
            "blog.post",
            "11",
            url="/blog/new/",
            public=True,
            previous=("/blog/old/", True),
        )
        assert job is not None

        jobs.process_pending()
        job.refresh_from_db()

        self.assertEqual(job.status, SEOJob.Status.FAILED)
        self.assertIn("failed", job.last_error)

    def test_unknown_job_kinds_and_stale_running_jobs(self):
        unknown = SEOJob.objects.create(kind="mystery", job_key="mystery:1")
        stale = SEOJob.objects.create(
            kind=jobs.CONTENT_CHANGED,
            job_key="stale:1",
            status=SEOJob.Status.RUNNING,
            started_at=timezone.now() - timedelta(hours=1),
            payload={"label": "blog.post", "pk": "1", "url": "/x/", "public": False},
        )
        set_mode(Mode.SAFE_AUTOPILOT)

        counts = jobs.process_pending()

        unknown.refresh_from_db()
        stale.refresh_from_db()
        self.assertEqual(counts["reclaimed"], 1)
        self.assertEqual(unknown.status, SEOJob.Status.FAILED)
        self.assertIn("Unknown job kind", unknown.last_error)
        self.assertIn(stale.status, {SEOJob.Status.SKIPPED, SEOJob.Status.SUCCEEDED})

    def test_celery_tasks_and_beat_schedule_reference_real_tasks(self):
        from django.conf import settings

        from apps.seo import tasks

        scheduled = {
            entry["task"]
            for name, entry in settings.CELERY_BEAT_SCHEDULE.items()
            if name.startswith("seo-")
        }
        self.assertEqual(
            scheduled,
            {
                tasks.process_pending_jobs.name,
                tasks.scheduled_crawl.name,
                tasks.scheduled_scan.name,
            },
        )
        self.assertEqual(tasks.process_pending_jobs.delay().get(), {"reclaimed": 0})
        with override_settings(SITE_URL=""):
            skipped = tasks.scheduled_crawl.delay().get()
        self.assertEqual(skipped["status"], "skipped")


class EntityGraphTests(TestCase):
    def test_graph_is_built_only_from_real_relations_and_is_rebuildable(self):
        data = make_catalog()

        counts = entity_graph.rebuild()

        self.assertGreaterEqual(counts["entities"], 5)
        brand = entity_graph.neighbors(f"brand-{data.brand.pk}")
        names = {entity.entity_type for entity in brand}
        self.assertTrue({"model", "tool", "post"} <= names)
        self.assertEqual(entity_graph.rebuild(), counts)

        data.tool.delete()
        entity_graph.rebuild()
        self.assertNotIn(
            "tool",
            {e.entity_type for e in entity_graph.neighbors(f"brand-{data.brand.pk}")},
        )
        self.assertEqual(entity_graph.neighbors("missing"), [])

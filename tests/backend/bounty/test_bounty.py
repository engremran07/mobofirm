"""Bounty: escrow, settlement via firmware.approved, refunds, disputes, pages."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.bounty import api, services, targets
from apps.bounty.models import Bounty, BountyClaim, BountyDispute, BountyLog
from apps.core.events.bus import event_bus
from apps.core.models import AppRegistry
from apps.firmwares.models import Brand, Model, Variant
from apps.firmwares.tracking_models import FirmwareRequest
from apps.wallet import api as wallet

pytestmark = pytest.mark.django_db
PASSWORD = "Pass-12345!"


@pytest.fixture
def make_user(django_user_model):
    def build(name: str, *, verified: bool = True, credits: int = 0, **extra):
        user = django_user_model.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password=PASSWORD,
            email_verified_at=timezone.now() if verified else None,
            **extra,
        )
        if credits:
            wallet.credit(
                user,
                credits,
                reason=wallet.LedgerReason.REWARD,
                key=f"seed:{name}",
            )
        return user

    return build


@pytest.fixture
def poster(make_user):
    return make_user("poster", credits=500)


@pytest.fixture
def uploader(make_user):
    return make_user("uploader")


@pytest.fixture
def catalogue(poster):
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone One", slug="phone-one")
    variant = Variant.objects.create(model=model, name="Global", slug="global")
    request = FirmwareRequest.objects.create(
        brand=brand, model=model, firmware_type="official", requested_by=poster
    )
    return SimpleNamespace(brand=brand, model=model, variant=variant, request=request)


def available(user) -> int:
    return apps.get_model("wallet", "Wallet").objects.get(user=user).available


def post(user, kind="request", object_id=None, credits=100, **extra):
    return services.post_bounty(
        user, kind=kind, object_id=object_id, amount=credits, **extra
    )


def approved(
    uploader, *, request_ids=(), model_id=None, variant_id=None, uploaded_at=None
):
    pending = SimpleNamespace(created_at=uploaded_at) if uploaded_at else None
    return {
        "uploader": uploader,
        "firmware_type": "official",
        "firmware_id": "abc",
        "fulfilled_request_ids": list(request_ids),
        "model_id": model_id,
        "variant_id": variant_id,
        "pending": pending,
    }


def test_posting_escrows_credits_and_logs(poster, catalogue):
    bounty = post(poster, "request", catalogue.request.pk, 120, note="  Need EU build ")
    assert bounty.status == Bounty.Status.OPEN
    assert bounty.target_label.startswith("Acme Phone One")
    assert bounty.note == "Need EU build"
    assert available(poster) == 500 - 120
    assert list(bounty.log.values_list("kind", flat=True)) == ["posted"]


def test_posting_needs_enough_credits_and_leaves_nothing_behind(make_user, catalogue):
    broke = make_user("broke", credits=5)
    with pytest.raises(services.NotEnoughCredits) as caught:
        post(broke, "request", catalogue.request.pk, 50)
    assert (caught.value.needed, caught.value.available) == (50, 5)
    assert not Bounty.objects.exists()


@pytest.mark.parametrize(
    ("credits", "days"),
    [(9, 30), (100_001, 30), (50, 0), (50, 91)],
)
def test_posting_validates_amount_and_duration(poster, catalogue, credits, days):
    with pytest.raises(services.BountyError):
        post(poster, "request", catalogue.request.pk, credits, days=days)
    assert not Bounty.objects.exists()


def test_posting_requires_verified_email_valid_target_and_open_request(
    make_user, poster, catalogue
):
    with pytest.raises(services.BountyError, match="Verify"):
        post(
            make_user("unverified", verified=False, credits=500),
            "request",
            catalogue.request.pk,
        )
    with pytest.raises(services.BountyError):
        post(poster, "request", 999999)
    with pytest.raises(services.BountyError):
        post(poster, "bogus", 1)
    FirmwareRequest.objects.filter(pk=catalogue.request.pk).update(status="fulfilled")
    with pytest.raises(services.BountyError, match="no longer open"):
        post(poster, "request", catalogue.request.pk)


def test_open_bounty_limit(poster, catalogue, monkeypatch):
    monkeypatch.setattr(services, "MAX_OPEN_PER_USER", 2)
    post(poster, "model", catalogue.model.pk, 10)
    post(poster, "variant", catalogue.variant.pk, 10)
    with pytest.raises(services.BountyError, match="at most 2"):
        post(poster, "request", catalogue.request.pk, 10)


def test_approved_request_upload_pays_the_uploader_once(poster, uploader, catalogue):
    bounty = post(poster, "request", catalogue.request.pk, 100)
    payload = approved(uploader, request_ids=[catalogue.request.pk])
    event_bus.publish("firmware.approved", payload)
    event_bus.publish("firmware.approved", payload)
    bounty.refresh_from_db()
    assert bounty.status == Bounty.Status.FULFILLED
    assert bounty.settled_to == uploader
    assert wallet.balance(uploader) == 100
    assert wallet.balance(poster) == 500 - 100
    assert available(poster) == 400
    assert list(bounty.log.values_list("kind", flat=True)) == ["posted", "settled"]


def test_model_and_variant_bounties_settle_on_matching_approval_only(
    poster, uploader, catalogue
):
    on_model = post(poster, "model", catalogue.model.pk, 30)
    on_variant = post(poster, "variant", catalogue.variant.pk, 40)
    other_variant = Variant.objects.create(
        model=catalogue.model, name="India", slug="india"
    )
    event_bus.publish(
        "firmware.approved",
        approved(uploader, model_id=catalogue.model.pk, variant_id=other_variant.pk),
    )
    on_model.refresh_from_db()
    on_variant.refresh_from_db()
    assert on_model.status == Bounty.Status.FULFILLED
    assert on_variant.status == Bounty.Status.OPEN
    event_bus.publish(
        "firmware.approved",
        approved(
            uploader, model_id=catalogue.model.pk, variant_id=catalogue.variant.pk
        ),
    )
    on_variant.refresh_from_db()
    assert on_variant.status == Bounty.Status.FULFILLED
    assert wallet.balance(uploader) == 70


def test_unrelated_or_anonymous_approvals_change_nothing(poster, uploader, catalogue):
    bounty = post(poster, "request", catalogue.request.pk, 100)
    event_bus.publish("firmware.approved", approved(uploader, request_ids=[424242]))
    event_bus.publish(
        "firmware.approved", approved(None, request_ids=[catalogue.request.pk])
    )
    bounty.refresh_from_db()
    assert bounty.status == Bounty.Status.OPEN
    assert wallet.balance(uploader) == 0


def test_uploads_submitted_before_the_bounty_do_not_qualify(
    poster, uploader, catalogue
):
    bounty = post(poster, "request", catalogue.request.pk, 100)
    old = bounty.created_at - timedelta(days=1)
    event_bus.publish(
        "firmware.approved",
        approved(uploader, request_ids=[catalogue.request.pk], uploaded_at=old),
    )
    bounty.refresh_from_db()
    assert bounty.status == Bounty.Status.OPEN


def test_poster_fulfilling_their_own_bounty_gets_credits_back(poster, catalogue):
    bounty = post(poster, "request", catalogue.request.pk, 100)
    event_bus.publish(
        "firmware.approved", approved(poster, request_ids=[catalogue.request.pk])
    )
    bounty.refresh_from_db()
    assert bounty.status == Bounty.Status.CANCELLED
    assert available(poster) == 500
    assert "own bounty" in bounty.closed_reason


def test_refused_payout_goes_to_staff_review_with_the_reason(
    make_user, poster, catalogue
):
    unverified = make_user("unverified-up", verified=False)
    bounty = post(poster, "request", catalogue.request.pk, 100)
    event_bus.publish(
        "firmware.approved", approved(unverified, request_ids=[catalogue.request.pk])
    )
    bounty.refresh_from_db()
    assert bounty.status == Bounty.Status.REVIEW
    assert "refused" in bounty.review_reason
    assert available(poster) == 400
    assert wallet.balance(unverified) == 0


def test_cancel_refunds_once_and_enforces_permissions(poster, uploader, catalogue):
    bounty = post(poster, "request", catalogue.request.pk, 100)
    with pytest.raises(services.BountyError):
        services.cancel(bounty, actor=uploader)
    services.cancel(bounty, actor=poster, reason="No longer needed")
    bounty.refresh_from_db()
    assert bounty.status == Bounty.Status.CANCELLED
    assert available(poster) == 500
    with pytest.raises(services.BountyError):
        services.cancel(bounty, actor=poster)
    assert available(poster) == 500


def test_expiry_refunds_only_due_open_bounties(poster, catalogue):
    due = post(poster, "model", catalogue.model.pk, 10)
    later = post(poster, "variant", catalogue.variant.pk, 10)
    Bounty.objects.filter(pk=due.pk).update(
        expires_at=timezone.now() - timedelta(hours=1)
    )
    assert services.expire_due() == 1
    assert services.expire_due() == 0
    due.refresh_from_db()
    later.refresh_from_db()
    assert (due.status, later.status) == (Bounty.Status.EXPIRED, Bounty.Status.OPEN)
    assert available(poster) == 490


def test_claims_are_idempotent_and_restricted(make_user, poster, uploader, catalogue):
    bounty = post(poster, "request", catalogue.request.pk, 50)
    services.claim(bounty, uploader, "on it")
    services.claim(bounty, uploader, "again")
    assert BountyClaim.objects.filter(bounty=bounty).count() == 1
    with pytest.raises(services.BountyError, match="own"):
        services.claim(bounty, poster)
    with pytest.raises(services.BountyError):
        services.claim(bounty, make_user("nov", verified=False))


def test_dispute_flow_refund_pay_and_dismiss(make_user, poster, uploader, catalogue):
    staff = make_user("staffer", is_staff=True)
    refund = post(poster, "request", catalogue.request.pk, 100)
    paid = post(poster, "model", catalogue.model.pk, 60)
    dismissed = post(poster, "variant", catalogue.variant.pk, 20)
    first = services.report_problem(refund, uploader, "The file is not what was asked")
    with pytest.raises(services.BountyError):
        services.report_problem(refund, uploader, "The file is not what was asked")
    with pytest.raises(services.BountyError, match="10 characters"):
        services.report_problem(paid, uploader, "bad")
    services.resolve_dispute(first, outcome="refunded", staff=staff, note="Refund")
    refund.refresh_from_db()
    assert refund.status == Bounty.Status.CANCELLED
    second = services.report_problem(paid, uploader, "Another contributor did the work")
    with pytest.raises(services.BountyError, match="Choose who"):
        services.resolve_dispute(second, outcome="paid", staff=staff)
    second.payee = uploader
    second.save()
    services.resolve_dispute(second, outcome="paid", staff=staff, payee=uploader)
    paid.refresh_from_db()
    assert paid.status == Bounty.Status.FULFILLED
    assert wallet.balance(uploader) == 60
    third = services.report_problem(dismissed, uploader, "Looks suspicious to me")
    services.resolve_dispute(third, outcome="dismissed", staff=staff)
    dismissed.refresh_from_db()
    assert dismissed.status == Bounty.Status.OPEN
    assert BountyDispute.objects.filter(status="open").count() == 0
    again = services.resolve_dispute(third, outcome="refunded", staff=staff)
    assert again.outcome == "dismissed"
    with pytest.raises(services.BountyError):
        services.report_problem(refund, uploader, "Reporting a closed bounty now")


def test_bounties_pause_honestly_when_credits_are_off(poster, catalogue):
    registry = AppRegistry.get_solo()
    registry.wallet_enabled = False
    registry.save()
    ok, reason = services.availability()
    assert not ok and "credits" in reason
    with pytest.raises(services.BountyUnavailable):
        post(poster, "request", catalogue.request.pk)
    assert api.availability()[0] is False


def test_api_summary_and_cta_tag(poster, catalogue):
    post(poster, "request", catalogue.request.pk, 40)
    post(poster, "request", catalogue.request.pk, 60)
    assert api.bounty_summary("request", catalogue.request.pk) == (2, 100)
    request = RequestFactory().get("/")
    request.user = poster
    html = Template('{% load bounty_tags %}{% bounty_cta "request" target %}').render(
        Context({"request": request, "target": catalogue.request})
    )
    assert "100 credits" in html
    assert f"/bounty/new/?request={catalogue.request.pk}" in html
    assert (
        Template('{% load bounty_tags %}{% bounty_cta "bogus" target %}')
        .render(Context({"request": request, "target": catalogue.request}))
        .strip()
        == ""
    )


def test_target_kind_lookup_roundtrip(catalogue):
    target = targets.resolve("variant", catalogue.variant.pk)
    assert targets.kind_of(target.content_type) == "variant"
    assert "Global" in target.label
    assert target.url == "/firmwares/acme/phone-one/"


def test_public_pages_and_the_posting_flow(poster, uploader, catalogue):
    anon = Client()
    assert anon.get("/bounty/").status_code == 200
    assert "No bounties here" in anon.get("/bounty/").content.decode()
    assert anon.get("/bounty/new/").status_code == 302
    client = Client()
    client.force_login(poster)
    form = client.get(f"/bounty/new/?request={catalogue.request.pk}")
    assert form.status_code == 200
    assert "Acme Phone One" in form.content.decode()
    response = client.post(
        "/bounty/new/",
        {
            "kind": "request",
            "object_id": catalogue.request.pk,
            "credits": 80,
            "days": 14,
            "note": "Thanks",
        },
    )
    bounty = Bounty.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("bounty:detail", kwargs={"pk": bounty.pk})
    assert "80 credits" in client.get(f"/bounty/{bounty.pk}/").content.decode()
    listing = anon.get("/bounty/?kind=request&q=acme").content.decode()
    assert "Acme Phone One" in listing
    assert "No bounties here" in anon.get("/bounty/?kind=model").content.decode()
    over = client.post(
        "/bounty/new/",
        {
            "kind": "request",
            "object_id": catalogue.request.pk,
            "credits": 5000,
            "days": 5,
        },
    )
    assert over.status_code == 200
    assert "need 5000 credits" in over.content.decode().replace("You ", "")
    assert Bounty.objects.count() == 1


def test_detail_actions_claim_report_and_cancel_dialog(poster, uploader, catalogue):
    bounty = post(poster, "request", catalogue.request.pk, 100)
    other = Client()
    other.force_login(uploader)
    assert (
        other.post(f"/bounty/{bounty.pk}/claim/", {"note": "mine"}).status_code == 302
    )
    assert BountyClaim.objects.filter(bounty=bounty, user=uploader).exists()
    page = other.get(f"/bounty/{bounty.pk}/").content.decode()
    assert "You are listed" in page
    assert "Cancel and get credits back" not in page
    assert (
        other.get(reverse("bounty:cancel_dialog", kwargs={"pk": bounty.pk})).status_code
        == 403
    )
    report = other.post(
        f"/bounty/{bounty.pk}/report/", {"reason": "This looks like a duplicate."}
    )
    assert report.status_code == 302
    assert BountyDispute.objects.filter(bounty=bounty, reporter=uploader).exists()

    owner = Client()
    owner.force_login(poster)
    assert (
        "Cancel and get credits back"
        in owner.get(f"/bounty/{bounty.pk}/").content.decode()
    )
    url = reverse("bounty:cancel_dialog", kwargs={"pk": bounty.pk})
    assert owner.get(url).status_code == 200
    done = owner.post(url, {})
    assert done.status_code == 302
    bounty.refresh_from_db()
    assert bounty.status == Bounty.Status.CANCELLED
    assert available(poster) == 500
    assert Client().post(f"/bounty/{bounty.pk}/claim/").status_code == 302
    assert (
        Client(enforce_csrf_checks=True).post(f"/bounty/{bounty.pk}/claim/").status_code
        == 403
    )


def test_my_bounties_lists_posted_claimed_and_won(poster, uploader, catalogue):
    bounty = post(poster, "request", catalogue.request.pk, 50)
    services.claim(bounty, uploader)
    event_bus.publish(
        "firmware.approved", approved(uploader, request_ids=[catalogue.request.pk])
    )
    client = Client()
    client.force_login(uploader)
    page = client.get("/bounty/mine/").content.decode()
    assert page.count("Acme Phone One") == 2
    assert "50 credits on" in page


def test_admin_screens(poster, catalogue):
    admin = get_user_model().objects.create_superuser(
        username="b-admin", email="b-admin@example.com", password=PASSWORD
    )
    client = Client()
    client.force_login(admin)
    bounty = post(poster, "request", catalogue.request.pk, 50)
    BountyLog.objects.create(bounty=bounty, kind="note")
    assert client.get(reverse("admin:bounty_bounty_changelist")).status_code == 200
    assert client.get(reverse("admin:bounty_bounty_add")).status_code == 403
    change = client.get(reverse("admin:bounty_bounty_change", args=[bounty.pk]))
    assert change.status_code == 200
    assert (
        client.post(
            reverse("admin:bounty_bounty_delete", args=[bounty.pk]), {"post": "yes"}
        ).status_code
        == 403
    )
    assert (
        client.get(reverse("admin:bounty_bountydispute_changelist")).status_code == 200
    )

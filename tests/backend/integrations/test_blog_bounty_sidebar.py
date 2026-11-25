"""The public blog sidebar lists only live bounties on approved guide requests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogSettings, GuideRequest
from apps.bounty import services
from apps.bounty.models import Bounty
from apps.wallet import api as wallet

pytestmark = pytest.mark.django_db


def test_public_blog_and_contribute_pages_only_show_open_public_guide_bounties(
    client, django_user_model
):
    now = timezone.now()
    poster = django_user_model.objects.create_user(
        username="guide-bounty-poster",
        email="guide-bounty-poster@example.test",
        email_verified_at=now,
    )
    wallet.credit(
        poster,
        500,
        reason=wallet.LedgerReason.REWARD,
        key="guide-bounty-sidebar-seed",
    )
    approved = GuideRequest.objects.create(
        requester=poster,
        title="Approved public guide bounty",
        status=GuideRequest.Status.APPROVED,
    )
    pending = GuideRequest.objects.create(
        requester=poster,
        title="Private pending guide bounty",
        status=GuideRequest.Status.PENDING,
    )
    expired = GuideRequest.objects.create(
        requester=poster,
        title="Expired guide bounty",
        status=GuideRequest.Status.APPROVED,
    )
    services.post_bounty(poster, kind="guide", object_id=approved.pk, amount=40)

    guide_type = ContentType.objects.get_for_model(GuideRequest)
    Bounty.objects.create(
        creator=poster,
        title="Private pending bounty",
        target_type=guide_type,
        target_id=pending.pk,
        target_label=pending.title,
        credits=30,
        expires_at=now + timedelta(days=1),
    )
    Bounty.objects.create(
        creator=poster,
        title="Expired bounty",
        target_type=guide_type,
        target_id=expired.pk,
        target_label=expired.title,
        credits=20,
        expires_at=now - timedelta(seconds=1),
    )

    response = client.get(reverse("blog:post_list"))

    assert response.status_code == 200
    html = response.content.decode()
    assert "Open guide bounties" in html
    assert html.index("Open guide bounties") < html.index("Popular articles")
    assert approved.title in html
    assert reverse("blog:request_detail", kwargs={"pk": approved.pk}) in html
    assert "40 credits" in html
    assert pending.title not in html
    assert expired.title not in html

    settings = BlogSettings.get_solo()
    settings.enable_blog = True
    settings.allow_user_blog_posts = True
    settings.save()
    contributor = django_user_model.objects.create_user(
        username="guide-bounty-contributor",
        email="guide-bounty-contributor@example.test",
        email_verified_at=now,
    )
    client.force_login(contributor)

    contribute_page = client.get(reverse("blog:contribute"))

    assert contribute_page.status_code == 200
    contribute_html = contribute_page.content.decode()
    assert 'data-testid="contribution-bounties"' in contribute_html
    assert approved.title in contribute_html
    assert reverse("blog:request_detail", kwargs={"pk": approved.pk}) in contribute_html
    assert "40 credits" in contribute_html
    assert "Write a new blog post" in contribute_html
    assert pending.title not in contribute_html
    assert expired.title not in contribute_html

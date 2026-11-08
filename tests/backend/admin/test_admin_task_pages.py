"""Per-object admin pages gather the related work instead of scattering it."""

from __future__ import annotations

import pytest
from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.blog.models import Post
from apps.core.admin_base import RelatedOverviewMixin
from apps.firmwares.models import AutomatedBlogLink, OfficialFirmware, PendingFirmware
from apps.firmwares.models import Model as DeviceModel
from tests.backend.admin.admin_model_factory import build


@pytest.fixture
def client(db) -> Client:
    superuser = get_user_model().objects.create_superuser(
        username="task-pages-admin", email="task-pages-admin@example.test"
    )
    test_client = Client()
    test_client.force_login(superuser)
    return test_client


def _device_with_firmware() -> tuple[DeviceModel, OfficialFirmware]:
    """The factory leaves nullable foreign keys empty, so attach the catalogue row."""
    device = build(DeviceModel)
    firmware = build(OfficialFirmware)
    assert isinstance(device, DeviceModel)
    assert isinstance(firmware, OfficialFirmware)
    OfficialFirmware.objects.filter(pk=firmware.pk).update(
        brand=device.brand, model=device
    )
    return device, firmware


def test_model_page_has_a_related_tab_with_firmware_and_blog_link(client):
    device, _firmware = _device_with_firmware()
    post = build(Post)
    assert isinstance(post, Post)
    AutomatedBlogLink.objects.create(
        kind=AutomatedBlogLink.Kind.MODEL, model=device, post=post
    )

    response = client.get(reverse("admin:firmwares_model_change", args=[device.pk]))

    assert response.status_code == 200
    html = response.content.decode()
    changelist = reverse("admin:firmwares_officialfirmware_changelist")
    assert f"{changelist}?model__id__exact={device.pk}" in html
    assert reverse("admin:blog_post_change", args=[post.pk]) in html
    assert "Related" in html


def test_model_page_related_tab_is_honest_when_nothing_is_linked(client):
    device = build(DeviceModel)
    assert isinstance(device, DeviceModel)

    response = client.get(reverse("admin:firmwares_model_change", args=[device.pk]))

    html = response.content.decode()
    assert response.status_code == 200
    assert "No firmware yet." in html
    assert "No automated blog post yet." in html
    assert "Nothing waiting for review." in html


def test_firmware_changelist_accepts_the_model_filter_used_by_the_related_tab(client):
    device, firmware = _device_with_firmware()

    response = client.get(
        reverse("admin:firmwares_officialfirmware_changelist"),
        {"model__id__exact": device.pk},
    )

    assert response.status_code == 200
    assert firmware.original_file_name in response.content.decode()


def test_pending_upload_link_filter_is_accepted(client):
    device = build(DeviceModel)
    assert isinstance(device, DeviceModel)
    build(PendingFirmware)

    response = client.get(
        reverse("admin:firmwares_pendingfirmware_changelist"),
        {"uploaded_model__id__exact": device.pk, "admin_decision__exact": "pending"},
    )

    assert response.status_code == 200


def test_user_page_lists_related_records_with_filtered_links(client):
    user = get_user_model().objects.create_user(
        username="related-owner", email="related-owner@example.test"
    )

    response = client.get(reverse("admin:users_customuser_change", args=[user.pk]))

    html = response.content.decode()
    assert response.status_code == 200
    changelist = reverse("admin:firmwares_pendingfirmware_changelist")
    assert f"{changelist}?uploader__id__exact={user.pk}" in html
    assert "Firmware uploads" in html


def test_related_overview_only_lists_models_the_staff_user_may_view(db):
    target = get_user_model().objects.create_user(
        username="related-target", email="related-target@example.test"
    )
    staff = get_user_model().objects.create_user(
        username="related-staff", email="related-staff@example.test", is_staff=True
    )
    staff.user_permissions.add(
        Permission.objects.get(codename="view_customuser"),
        Permission.objects.get(codename="change_customuser"),
    )
    staff_client = Client()
    staff_client.force_login(staff)

    response = staff_client.get(
        reverse("admin:users_customuser_change", args=[target.pk])
    )

    html = response.content.decode()
    assert response.status_code == 200
    assert "Nothing related that you can view." in html
    assert "Firmware uploads" not in html


def test_every_declared_related_link_resolves_to_a_real_field(db):
    declared = 0
    for model_admin in admin.site._registry.values():
        if not isinstance(model_admin, RelatedOverviewMixin):
            continue
        for link in model_admin.related_links:
            declared += 1
            related = django_apps.get_model(link.model)
            assert admin.site.is_registered(related), link
            field = related._meta.get_field(link.lookup.split("__")[0])
            assert field.is_relation, link
            assert related._default_manager.filter(**{link.lookup: 0}).count() == 0
    assert declared

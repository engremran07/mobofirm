"""Access type dialogs on the firmware page: staff change, uploaders request."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.firmwares import access_changes, access_requests
from apps.firmwares.access_change_models import AccessChangeLog, AccessChangeRequest
from apps.firmwares.models import Brand, Model, OfficialFirmware, Variant

pytestmark = pytest.mark.django_db
HTMX: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(name="people")
def people_fixture(django_user_model) -> dict[str, Any]:
    make = django_user_model.objects.create_user
    return {
        "staff": make(
            username="boss", email="boss@e.test", is_staff=True, is_superuser=True
        ),
        "uploader": make(username="up", email="up@e.test"),
        "stranger": make(username="nobody", email="nobody@e.test"),
    }


@pytest.fixture(name="firmware")
def firmware_fixture(people) -> OfficialFirmware:
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone", slug="phone")
    variant = Variant.objects.create(model=model, name="Global", slug="global")
    return OfficialFirmware.objects.create(
        original_file_name="fw.zip",
        stored_file_path="/firmware/fw.zip",
        brand=brand,
        model=model,
        variant=variant,
        is_verified=True,
        is_active=True,
        uploader=people["uploader"],
    )


def client_for(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def urls(firmware: OfficialFirmware) -> dict[str, str]:
    kwargs = {"firmware_type": "official", "firmware_id": firmware.pk}
    return {
        "change": reverse("firmwares:firmware_access_change", kwargs=kwargs),
        "request": reverse("firmwares:firmware_access_request", kwargs=kwargs),
        "detail": reverse("firmwares:firmware_detail", kwargs=kwargs),
    }


def test_staff_open_and_submit_the_change_dialog(people, firmware, monkeypatch) -> None:
    monkeypatch.setattr(access_changes, "is_seller", lambda user: True)
    client = client_for(people["staff"])
    link = urls(firmware)["change"]

    opened = client.get(link, **HTMX)
    assert opened.status_code == 200
    assert b"Change the access type" in opened.content
    assert b'id="firmware-access-dialog"' in opened.content

    invalid = client.post(link, {"access_tier": "paid", "reason": "x"}, **HTMX)
    assert invalid.status_code == 200
    assert b"A paid file needs a price." in invalid.content

    done = client.post(
        link,
        {"access_tier": "paid", "price": "4.00", "reason": "Rare build"},
        **HTMX,
    )

    assert done.status_code == 204
    assert "dialog:close" in done.headers["HX-Trigger"]
    firmware.refresh_from_db()
    assert (firmware.access_tier, firmware.approved_price) == ("paid", Decimal("4.00"))
    assert AccessChangeLog.objects.get().reason == "Rare build"


def test_a_rule_refusal_is_shown_inside_the_dialog(people, firmware) -> None:
    firmware.uploader = people["staff"]
    firmware.save()

    response = client_for(people["staff"]).post(
        urls(firmware)["change"],
        {"access_tier": "featured", "reason": "Mine"},
        **HTMX,
    )

    assert response.status_code == 200
    assert b"your own file" in response.content
    firmware.refresh_from_db()
    assert firmware.access_tier == "free"


def test_normal_users_cannot_change_the_access_type(people, firmware) -> None:
    link = urls(firmware)["change"]

    for user in (people["uploader"], people["stranger"]):
        response = client_for(user).post(
            link, {"access_tier": "featured", "reason": "Please"}, **HTMX
        )
        assert response.status_code == 403
    anonymous = Client().get(link, **HTMX)
    assert anonymous.status_code in (204, 302)
    firmware.refresh_from_db()
    assert firmware.access_tier == "free"


def test_the_uploader_requests_and_others_cannot(people, firmware, monkeypatch) -> None:
    monkeypatch.setattr(access_requests, "is_seller", lambda user: True)
    link = urls(firmware)["request"]

    assert client_for(people["stranger"]).get(link, **HTMX).status_code == 403
    client = client_for(people["uploader"])
    assert b"Request an access type change" in client.get(link, **HTMX).content

    done = client.post(
        link,
        {
            "access_tier": "paid",
            "price": "9.00",
            "reason": "A rare and valuable readback",
        },
        **HTMX,
    )

    assert done.status_code == 204
    request = AccessChangeRequest.objects.get()
    assert (request.to_tier, request.proposed_price) == ("paid", Decimal("9.00"))
    firmware.refresh_from_db()
    assert firmware.access_tier == "free"
    again = client.post(
        link,
        {"access_tier": "featured", "reason": "Different idea entirely"},
        **HTMX,
    )
    assert b"already has a pending request" in again.content


def test_the_page_shows_the_right_buttons(people, firmware, monkeypatch) -> None:
    monkeypatch.setattr(access_requests, "is_seller", lambda user: True)
    page = urls(firmware)["detail"]

    staff = client_for(people["staff"]).get(page).content
    uploader = client_for(people["uploader"]).get(page).content
    stranger = client_for(people["stranger"]).get(page).content

    assert b"data-access-change" in staff
    assert b"data-access-request" not in staff
    assert b"data-access-request" in uploader
    assert b"data-access-change" not in uploader
    assert b"data-access-actions" not in stranger
    assert b"data-access-actions" not in Client().get(page).content

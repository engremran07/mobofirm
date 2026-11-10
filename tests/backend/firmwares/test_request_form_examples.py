"""Every field of the request forms carries an example and a help text."""

from __future__ import annotations

import pytest
from django.forms import HiddenInput
from django.test import Client
from django.urls import reverse

from apps.firmwares.forms import (
    BrandRequestForm,
    ChipsetRequestForm,
    FirmwareRequestForm,
    ModelRequestForm,
    VariantRequestForm,
)

FORMS = {
    "brand": BrandRequestForm,
    "chipset": ChipsetRequestForm,
    "model": ModelRequestForm,
    "variant": VariantRequestForm,
    "firmware": FirmwareRequestForm,
}
CHOICE_FIELDS = {"firmware_type", "urgency"}
EXAMPLES = {
    "brand": ("Xiaomi",),
    "chipset": ("MediaTek", "Dimensity 9200 (MT6985)"),
    "model": ("Redmi Note 13 Pro 5G", "2312DRA50G", "MediaTek Dimensity 9200 (MT6985)"),
    "variant": ("AI2401_A", "Global", "8 GB / 256 GB"),
    "firmware": ("Redmi Note 13 Pro 5G", "AI2401_A"),
}


@pytest.mark.parametrize("kind", FORMS)
def test_every_visible_field_has_help_and_typed_fields_have_an_example(kind):
    """Text inputs show an example placeholder; every field explains itself."""
    form = FORMS[kind]()

    for name, field in form.fields.items():
        if isinstance(field.widget, HiddenInput):
            continue
        assert field.help_text, f"{kind}.{name} has no help text"
        if name not in CHOICE_FIELDS:
            assert field.widget.attrs.get("placeholder"), (
                f"{kind}.{name} has no example placeholder"
            )


@pytest.mark.django_db
@pytest.mark.parametrize("kind", FORMS)
def test_the_page_shows_the_realistic_examples(make_user, kind):
    """The rendered form, including the pickers, shows the agreed examples."""
    client = Client()
    client.force_login(make_user(f"examples-{kind}"))

    body = client.get(reverse(f"firmwares:request_{kind}")).content.decode()

    for example in EXAMPLES[kind]:
        assert example in body, f"{kind} form lacks the example {example!r}"

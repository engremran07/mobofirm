"""Variant identity rules: normalised codes, optional variants, identifying fields."""

from __future__ import annotations

from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.firmwares import catalog_requests as service
from apps.firmwares.models import Brand, Model, Variant, VariantCreationRequest
from apps.firmwares.variant_rules import (
    VariantIdentity,
    normalize_variant_code,
    parse_memory,
    variant_is_required,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AI2401", "AI2401"),
        (" ai2401_a ", "AI2401_A"),
        ("ai2401 d", "AI2401_D"),
        ("sm-s911b", "SM-S911B"),
        ("", ""),
        (None, ""),
    ],
)
def test_codes_are_trimmed_upper_cased_and_joined(raw, expected):
    """The same code never exists in two spellings."""
    assert normalize_variant_code(raw) == expected


@pytest.mark.parametrize(
    ("text", "ram", "storage"),
    [
        ("8 GB / 256 GB", [8], [256]),
        ("12GB+512GB", [12], [512]),
        ("6 GB, 128 GB", [6], [128]),
        ("8 GB / 1 TB", [8], [1024]),
        ("8 GB", [], []),
        ("lots / plenty", [], []),
        ("", [], []),
    ],
)
def test_memory_text_is_read_as_ram_then_storage(text, ram, storage):
    """Unreadable text yields no options instead of a wrong guess."""
    assert parse_memory(text) == (ram, storage)


def test_an_identity_needs_at_least_one_identifying_field():
    """Code, region, memory or carrier each identify; the name alone does not."""
    assert not VariantIdentity().is_identified
    assert VariantIdentity(model_code="AI2401").is_identified
    assert VariantIdentity(region="Global").is_identified
    assert VariantIdentity(memory="8 GB / 256 GB").is_identified
    assert VariantIdentity(carrier="Verizon").is_identified


def test_the_label_is_built_from_the_identifying_fields():
    """A request without a name is labelled by its code or its other fields."""
    assert VariantIdentity(model_code="AI2401_A", region="India").label() == (
        "AI2401_A · India"
    )
    assert VariantIdentity(region="EU", memory="8 GB / 256 GB").label() == (
        "EU · 8 GB / 256 GB"
    )


@pytest.mark.django_db
class TestVariantModel:
    """Codes are normalised on save and unique per model ignoring case."""

    def test_the_code_is_normalised_on_save(self, catalog):
        """A lower-case code is stored upper-case."""
        variant = Variant.objects.create(
            model=catalog.model, name="A", slug="a", model_code="ai2401_a"
        )

        variant.refresh_from_db()
        assert variant.model_code == "AI2401_A"

    def test_a_code_cannot_repeat_within_a_model(self, catalog):
        """The database refuses a second variant with the same code (any case)."""
        Variant.objects.create(
            model=catalog.model, name="A", slug="a", model_code="AI2401"
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            Variant.objects.create(
                model=catalog.model, name="B", slug="b", model_code="ai2401"
            )

    def test_the_same_code_is_allowed_under_another_model(self, catalog):
        """Uniqueness is per model, not global."""
        other = Model.objects.create(brand=catalog.brand, name="Other", slug="other")
        Variant.objects.create(
            model=catalog.model, name="A", slug="a", model_code="AI2401"
        )

        Variant.objects.create(model=other, name="A", slug="a", model_code="AI2401")

        assert Variant.objects.filter(model_code="AI2401").count() == 2

    def test_several_variants_may_have_no_code(self, catalog):
        """Blank codes never collide."""
        Variant.objects.create(model=catalog.model, name="X", slug="x")
        Variant.objects.create(model=catalog.model, name="Y", slug="y")

        assert Variant.objects.filter(model=catalog.model, model_code="").count() == 3

    def test_a_variant_is_required_only_while_the_model_has_active_ones(self, catalog):
        """Deactivating every variant makes the variant optional again."""
        assert variant_is_required(catalog.model)

        Variant.objects.filter(model=catalog.model).update(is_active=False)

        assert not variant_is_required(catalog.model)
        assert not variant_is_required(None)

    def test_the_migration_normalises_codes_stored_before_the_rule(self, catalog):
        """The 0005 data step upper-cases codes saved before the rule existed."""
        migration = import_module("apps.firmwares.migrations.0005_variant_identity")
        variant = Variant.objects.create(model=catalog.model, name="Old", slug="old")
        Variant.objects.filter(pk=variant.pk).update(model_code="ai2401 a")

        migration.normalise_existing_codes(django_apps, None)

        variant.refresh_from_db()
        assert variant.model_code == "AI2401_A"

    def test_the_admin_form_reports_a_repeated_code(self, catalog, make_user):
        """Add variant explains a repeated code instead of failing."""
        Variant.objects.create(
            model=catalog.model, name="A", slug="a", model_code="AI2401"
        )
        client = Client()
        client.force_login(make_user("variant-admin", superuser=True))

        response = client.post(
            reverse("admin:firmwares_variant_add"),
            {
                "brand": catalog.brand.pk,
                "model": catalog.model.pk,
                "name": "Dup",
                "slug": "dup",
                "model_code": "ai2401",
                "is_active": "on",
            },
        )

        assert response.status_code == 200
        assert "already uses this code" in response.content.decode()
        assert Variant.objects.filter(model=catalog.model).count() == 2


@pytest.mark.django_db
class TestVariantRequests:
    """A variant request needs brand, model and one identifying field."""

    def test_a_request_with_only_a_name_is_refused(self, make_user, catalog):
        """The name alone does not identify a variant."""
        with pytest.raises(service.CatalogRequestError, match="at least one"):
            service.submit_variant_request(
                make_user("name-only"),
                model=catalog.model,
                name="Mystery",
                identity=VariantIdentity(),
            )

    @pytest.mark.parametrize(
        "identity",
        [
            VariantIdentity(model_code="AI2401_D"),
            VariantIdentity(region="India"),
            VariantIdentity(memory="8 GB / 256 GB"),
            VariantIdentity(carrier="Verizon"),
        ],
    )
    def test_each_identifying_field_is_enough(self, make_user, catalog, identity):
        """Code, region, memory or carrier alone make a valid request."""
        pending = service.submit_variant_request(
            make_user("identified"), model=catalog.model, identity=identity
        )

        assert pending.name
        assert pending.model == catalog.model

    def test_the_name_defaults_to_the_normalised_code(self, make_user, catalog):
        """Without a name the request is labelled by its normalised code."""
        pending = service.submit_variant_request(
            make_user("coded"),
            model=catalog.model,
            identity=VariantIdentity(model_code="ai2401 a"),
        )

        assert (pending.name, pending.model_code) == ("AI2401_A", "AI2401_A")

    def test_an_existing_code_is_refused_in_any_case(self, make_user, catalog):
        """Requesting an existing code in another case points at the variant."""
        Variant.objects.create(
            model=catalog.model, name="Known", slug="known", model_code="AI2401"
        )

        with pytest.raises(service.CatalogRequestError, match="already exists"):
            service.submit_variant_request(
                make_user("case"),
                model=catalog.model,
                identity=VariantIdentity(model_code="ai2401"),
            )

    def test_a_pending_code_is_refused_for_the_next_requester(self, make_user, catalog):
        """A code already waiting for review cannot be requested twice."""
        service.submit_variant_request(
            make_user("first"),
            model=catalog.model,
            name="First",
            identity=VariantIdentity(model_code="AI2401_A"),
        )

        with pytest.raises(service.CatalogRequestError, match="already waiting"):
            service.submit_variant_request(
                make_user("second"),
                model=catalog.model,
                name="Second",
                identity=VariantIdentity(model_code="ai2401_a"),
            )

        assert VariantCreationRequest.objects.count() == 1

    def test_the_form_asks_for_brand_model_and_an_identifying_field(
        self, make_user, catalog
    ):
        """The page refuses a name alone and accepts a code."""
        client = Client()
        client.force_login(make_user("variant-form"))
        url = reverse("firmwares:request_variant")
        base = {"brand": catalog.brand.pk, "model": catalog.model.pk}

        empty = client.post(url, {**base, "name": "Only a name"})
        coded = client.post(url, {**base, "model_code": "AI2401_A"})

        assert "Give at least one of" in empty.content.decode()
        assert coded.status_code == 302
        assert VariantCreationRequest.objects.get().name == "AI2401_A"

    def test_a_request_for_another_brands_model_is_refused(self, make_user, catalog):
        """The model must belong to the chosen brand."""
        other = Brand.objects.create(name="Other", slug="other")
        client = Client()
        client.force_login(make_user("mismatch-form"))

        response = client.post(
            reverse("firmwares:request_variant"),
            {"brand": other.pk, "model": catalog.model.pk, "region": "EU"},
        )

        assert "does not belong to the chosen brand" in response.content.decode()

"""Variant identity rules: normalised codes, optional variants, identifying fields."""

from __future__ import annotations

from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.firmwares import catalog_requests as service
from apps.firmwares.models import Brand, Model, Variant, VariantCreationRequest
from apps.firmwares.variant_rules import (
    VariantIdentity,
    normalize_variant_code,
    normalize_variant_codes,
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


def test_model_codes_are_normalized_and_deduplicated_in_first_seen_order():
    assert normalize_variant_codes(
        [" sm-s928b ", "SM-S928U", "sm-s928b", "", None]
    ) == ["SM-S928B", "SM-S928U"]


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

    def test_model_slug_defaults_to_primary_code_and_falls_back_to_name(self, catalog):
        coded = Model.objects.create(
            brand=catalog.brand,
            name="Galaxy S24",
            model_codes=["SM-S921U", "SM-S921B"],
        )
        unnamed_code = Model.objects.create(
            brand=catalog.brand,
            name="Galaxy S24",
            model_code="SM-S921B",
        )
        uncoded = Model.objects.create(
            brand=catalog.brand,
            name="Galaxy S24",
        )

        assert coded.slug == "sm-s921u"
        assert unnamed_code.slug == "sm-s921b"
        assert uncoded.slug == "galaxy-s24"

    def test_model_slug_is_regenerated_when_blank_and_saved_partially(self, catalog):
        model = Model.objects.create(
            brand=catalog.brand,
            name="Galaxy S24",
            slug="temporary",
        )
        model.slug = ""
        model.model_code = "SM-S921B"
        model.save(update_fields={"model_code"})

        model.refresh_from_db()
        assert model.slug == "sm-s921b"

    def test_the_code_is_normalised_on_save(self, catalog):
        """A lower-case code is stored upper-case."""
        variant = Variant.objects.create(
            model=catalog.model, name="A", slug="a", model_code="ai2401_a"
        )

        variant.refresh_from_db()
        assert variant.model_code == "AI2401_A"

    def test_model_variant_codes_follow_model_and_variant_codes(self, catalog):
        catalog.model.model_code = "sm-a"
        catalog.model.model_codes = ["sm-b", "SM-A"]
        catalog.model.save(update_fields={"model_code", "model_codes"})
        catalog.model.refresh_from_db()
        assert catalog.model.variant_codes == ["SM-A", "SM-B"]

        catalog.variant.model_code = "sm-c"
        catalog.variant.save(update_fields={"model_code"})
        catalog.model.refresh_from_db()
        assert catalog.model.variant_codes == ["SM-A", "SM-B", "SM-C"]

        catalog.variant.delete()
        catalog.model.refresh_from_db()
        assert catalog.model.variant_codes == ["SM-A", "SM-B"]
        assert catalog.model.all_model_codes_display == "SM-A, SM-B"

    def test_active_model_rejects_codes_claimed_by_another_active_model(self, catalog):
        Model.objects.create(
            brand=catalog.brand,
            name="Other Phone",
            slug="other-phone",
            model_codes=["sm-x"],
        )
        candidate = Model(
            brand=catalog.brand,
            name="New Phone",
            slug="new-phone",
            variant_codes=[" SM-X "],
        )

        with pytest.raises(ValidationError, match="already assigned"):
            candidate.full_clean()

    def test_model_code_claims_ignore_inactive_models_and_other_brands(self, catalog):
        Model.objects.create(
            brand=catalog.brand,
            name="Retired Phone",
            slug="retired-phone",
            model_code="SM-X",
            is_active=False,
        )
        other_brand = Brand.objects.create(name="Other Brand", slug="other-brand")
        Model.objects.create(
            brand=other_brand,
            name="Other Phone",
            slug="other-phone",
            model_code="SM-Y",
        )

        Model(
            brand=catalog.brand,
            name="New Phone",
            slug="new-phone",
            model_code="SM-X",
        ).full_clean()
        Model(
            brand=catalog.brand,
            name="Different Phone",
            slug="different-phone",
            model_code="SM-Y",
        ).full_clean()

    def test_variant_clean_rejects_a_code_owned_by_another_active_model(self, catalog):
        Model.objects.create(
            brand=catalog.brand,
            name="Other Phone",
            slug="other-phone",
            model_code="SM-X",
        )
        variant = Variant(
            model=catalog.model,
            name="New Variant",
            slug="new-variant",
            model_code="sm-x",
        )

        with pytest.raises(ValidationError, match="already assigned"):
            variant.full_clean()

    def test_a_code_cannot_repeat_within_a_model(self, catalog):
        """The database refuses a second variant with the same code (any case)."""
        Variant.objects.create(
            model=catalog.model, name="A", slug="a", model_code="AI2401"
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            Variant.objects.create(
                model=catalog.model, name="B", slug="b", model_code="ai2401"
            )

    def test_the_same_code_is_allowed_under_another_brand(self, catalog):
        """A model-code claim is scoped to the brand."""
        Variant.objects.create(
            model=catalog.model, name="A", slug="a", model_code="AI2401"
        )
        other_brand = Brand.objects.create(name="Other Brand", slug="other-brand")
        other = Model.objects.create(
            brand=other_brand, name="Other", slug="other", model_code="AI2401"
        )

        variant = Variant(model=other, name="A", slug="a", model_code="AI2401")
        variant.full_clean()
        variant.save()

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

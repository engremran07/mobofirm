"""Every model registered in the admin renders its list, add and change pages."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import DatabaseError, models, transaction
from django.test import Client, RequestFactory
from django.urls import reverse

from tests.backend.admin.admin_model_factory import CannotBuild, build

# Models whose rows cannot be created from field types alone. Each entry names the
# concrete reason. The change-form test asserts the builder really fails for them, so
# the list cannot silently go stale; their list and add pages are still rendered.
UNBUILDABLE: dict[str, str] = {}

REGISTERED: list[type[models.Model]] = sorted(
    admin.site._registry, key=lambda model: model._meta.label_lower
)


def _automated_blog_link() -> models.Model:
    """The table's check constraint ties ``kind`` to exactly one target column."""
    from apps.blog.models import Post
    from apps.firmwares.models import AutomatedBlogLink, Brand

    brand = build(Brand)
    post = build(Post)
    assert isinstance(brand, Brand)
    assert isinstance(post, Post)
    return AutomatedBlogLink.objects.create(
        kind=AutomatedBlogLink.Kind.BRAND, brand=brand, post=post
    )


# Builders for models whose columns depend on each other (constraints), which the
# generic field-type builder cannot satisfy.
CUSTOM_BUILDERS: dict[str, Callable[[], models.Model]] = {
    "firmwares.automatedbloglink": _automated_blog_link,
}


def _build_row(model: type[models.Model]) -> models.Model:
    custom = CUSTOM_BUILDERS.get(model._meta.label_lower)
    return custom() if custom else build(model)


def _admin_pk(instance: models.Model) -> object:
    """DRF's token admin addresses a token by its user's id, not by the key."""
    if instance._meta.label_lower == "authtoken.token":
        return getattr(instance, "user_id", instance.pk)
    return instance.pk


@pytest.fixture
def superuser(db):
    return get_user_model().objects.create_superuser(
        username="all-models-admin",
        email="all-models-admin@example.test",
        password="x-test-password-123",
    )


@pytest.fixture
def client(superuser) -> Client:
    test_client = Client()
    test_client.force_login(superuser)
    return test_client


def _url_name(model: type[models.Model], suffix: str) -> str:
    return f"admin:{model._meta.app_label}_{model._meta.model_name}_{suffix}"


def _request_for(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


def test_allow_list_only_names_registered_models():
    registered = {model._meta.label_lower for model in REGISTERED}
    assert set(UNBUILDABLE) <= registered
    assert all(reason.strip() for reason in UNBUILDABLE.values())


@pytest.mark.parametrize("model", REGISTERED, ids=lambda m: m._meta.label_lower)
@pytest.mark.django_db
def test_changelist_and_add_form_render(model, client, superuser):
    model_admin = admin.site._registry[model]
    changelist = client.get(reverse(_url_name(model, "changelist")))
    # django-solo serves a singleton's change form at the changelist URL.
    assert changelist.status_code == 200, model._meta.label

    add_allowed = model_admin.has_add_permission(_request_for(superuser))
    add = client.get(reverse(_url_name(model, "add")))
    assert add.status_code == (200 if add_allowed else 403), model._meta.label


@pytest.mark.parametrize("model", REGISTERED, ids=lambda m: m._meta.label_lower)
@pytest.mark.django_db
def test_change_form_renders_for_a_created_row(model, client, superuser):
    label = model._meta.label_lower
    if label in UNBUILDABLE:
        with pytest.raises((CannotBuild, DatabaseError)), transaction.atomic():
            build(model)
        return
    instance = _build_row(model)
    model_admin = admin.site._registry[model]
    visible = model_admin.get_queryset(_request_for(superuser)).filter(pk=instance.pk)
    assert visible.exists(), f"{label}: the admin queryset hides a freshly built row"
    response = client.get(
        reverse(_url_name(model, "change"), args=[_admin_pk(instance)])
    )
    assert response.status_code == 200, label

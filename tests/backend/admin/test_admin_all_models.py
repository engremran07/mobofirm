"""Every model registered in the admin renders its list, add and change pages."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.db import DatabaseError, models, transaction
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.core.utils.managers import default_manager
from tests.backend.admin.admin_model_factory import (
    CannotBuild,
    admin_pk,
    build,
    build_row,
    registered_admins,
)
from tests.backend.admin.theme_guard import mentions_removed_theme

# Models whose rows cannot be created from field types alone. Each entry names the
# concrete reason. The change-form test asserts the builder really fails for them, so
# the list cannot silently go stale; their list and add pages are still rendered.
UNBUILDABLE: dict[str, str] = {}

REGISTERED: list[type[models.Model]] = sorted(
    registered_admins(), key=lambda model: model._meta.label_lower
)


@pytest.fixture(name="superuser")
def superuser_fixture(django_user_model):
    return django_user_model.objects.create_superuser(
        username="all-models-admin",
        email="all-models-admin@example.test",
        password="x-test-password-123",
    )


@pytest.fixture(name="client")
def client_fixture(superuser) -> Client:
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
    model_admin = admin.site.get_model_admin(model)
    changelist = client.get(reverse(_url_name(model, "changelist")))
    # django-solo serves a singleton's change form at the changelist URL.
    assert changelist.status_code == 200, model._meta.label
    assert not mentions_removed_theme(changelist.content), model._meta.label
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
    instance = build_row(model)
    model_admin = admin.site.get_model_admin(model)
    visible = model_admin.get_queryset(_request_for(superuser)).filter(pk=instance.pk)
    assert visible.exists(), f"{label}: the admin queryset hides a freshly built row"
    response = client.get(
        reverse(_url_name(model, "change"), args=[admin_pk(instance)])
    )
    assert response.status_code == 200, label
    assert not mentions_removed_theme(response.content), label


@pytest.mark.parametrize("model", REGISTERED, ids=lambda m: m._meta.label_lower)
@pytest.mark.django_db
def test_history_and_delete_pages_render_for_a_created_row(model, client, superuser):
    label = model._meta.label_lower
    if label in UNBUILDABLE:
        pytest.fail(f"{label}: listed as unbuildable, so history/delete are unverified")
    instance = build_row(model)
    model_admin = admin.site.get_model_admin(model)
    request = _request_for(superuser)
    pk = admin_pk(instance)

    history = client.get(reverse(_url_name(model, "history"), args=[pk]))
    assert history.status_code == 200, label

    delete_allowed = model_admin.has_delete_permission(request, instance)
    delete = client.get(reverse(_url_name(model, "delete"), args=[pk]))
    assert delete.status_code == (200 if delete_allowed else 403), label
    if delete_allowed:
        # Rendering the confirmation never deletes anything.
        assert default_manager(model).filter(pk=instance.pk).exists(), label

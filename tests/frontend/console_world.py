"""Django side of the staff-console crawl: seed one row per model, enumerate every URL.

Everything here touches the ORM, so it must run *before* Playwright starts (the
browser's event loop forbids ORM calls). The result is a plain list of ``Target``
values the browser side can crawl without any database access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import DatabaseError, models
from django.test import RequestFactory
from django.urls import NoReverseMatch, reverse

from apps.core.admin_dashboard import WIDGET_BY_SLUG
from tests.backend.admin.admin_model_factory import (
    CannotBuild,
    admin_pk,
    build_row,
    registered_admins,
)
from tests.url_inventory import AdminRoute, admin_routes

STANDARD_SUFFIXES = ("changelist", "add", "change", "history", "delete")
# Staff pages mounted outside the ``admin`` namespace that wear the console shell.
CURATED_STAFF_PAGES = (
    "tags:manage",
    "tags:duplicates_review",
    "tags:keyword_review",
    "comments:moderation_queue",
    "seo:dashboard",
    "seo:inventory",
    "seo:inspect",
    "seo:issues",
    "seo:links",
    "seo:guides",
    "seo:ledger",
    "seo:jobs",
    "seo:settings",
    "storage:status",
    "ads:dashboard",
    "ads:placement_manager",
    "ads:discoveries",
    "devices:device_events",
    "firmwares:requests_hub",
)
# Routes that are not a normal "open and look at it" page for a signed-in superuser.
# Each entry states why; ``mode="status"`` routes are checked without a browser audit.
EXPECTED_STATUS: dict[str, tuple[int, ...]] = {
    # POST-only since Django 5: a GET must not sign the user out.
    "admin:logout": (405,),
    # The project login owns sign-in; a signed-in staff user is sent on to the index.
    "admin:login": (302,),
    # Redirects to the object's public URL.
    "admin:view_on_site": (302,),
    # Distribution actions mutate state, so they accept POST only.
    "admin:distribution_channel_run_queue": (405,),
    "admin:distribution_channel_test_post": (405,),
    "admin:distribution_channel_check": (405,),
    "admin:distribution_channel_job_cancel": (405,),
    "admin:distribution_channel_job_reschedule": (405,),
    "admin:distribution_channel_job_decide": (405,),
    # A CSV download, not a page a browser can render.
    "admin:distribution_channel_analytics_export": (200,),
}
# Named routes whose URL kwargs are not ``object_id``: route name -> (model, kwarg).
ROUTE_MODELS: dict[str, tuple[str, str]] = {
    "admin:auth_user_password_change": ("users.customuser", "id"),
    "admin:distribution_channel_check": ("distribution.channel", "pk"),
    "admin:distribution_channel_preview": ("distribution.channel", "pk"),
    "admin:distribution_channel_test_post": ("distribution.channel", "pk"),
    "admin:distribution_channel_job_cancel": ("distribution.sharejob", "pk"),
    "admin:distribution_channel_job_decide": ("distribution.sharejob", "pk"),
    "admin:distribution_channel_job_reschedule": ("distribution.sharejob", "pk"),
}
FRAGMENT_MARKERS = ("<form", "dialog", "modal")


@dataclass(frozen=True)
class Target:
    """One URL to open and the outcome the console contract expects for it."""

    group: str
    label: str
    path: str
    mode: str = "page"  # page | fragment | status
    expected: tuple[int, ...] = (200,)
    needs_dialog_markup: bool = False

    @property
    def key(self) -> str:
        return f"{self.mode}:{self.label}"


@dataclass
class World:
    """Everything the crawl needs, computed once before the browser starts."""

    targets: list[Target] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    models_seeded: int = 0
    routes_seen: int = 0

    def for_group(self, group: str) -> list[Target]:
        return [target for target in self.targets if target.group == group]

    @property
    def groups(self) -> set[str]:
        return {target.group for target in self.targets}


def registered_models() -> list[type[models.Model]]:
    return sorted(registered_admins(), key=lambda model: model._meta.label_lower)


def crawl_groups() -> list[str]:
    """Static group names (known at collection time, without touching the database)."""
    apps = {model._meta.app_label for model in registered_admins()}
    return sorted(apps | {"admin-core", "staff-pages"})


def _request_for(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


def _model_for(name: str, by_prefix: dict[str, type[models.Model]]):
    """The registered model whose ``<app>_<model>_`` prefix starts the route name."""
    key = name.removeprefix("admin:")
    matches = [prefix for prefix in by_prefix if key.startswith(f"{prefix}_")]
    if not matches:
        return None, ""
    prefix = max(matches, key=len)
    return by_prefix[prefix], key[len(prefix) + 1 :]


def build_world(user) -> World:
    world = World()
    request = _request_for(user)
    by_prefix = {
        f"{model._meta.app_label}_{model._meta.model_name}": model
        for model in registered_models()
    }
    rows: dict[type[models.Model], models.Model] = {}
    for model in registered_models():
        try:
            rows[model] = build_row(model)
        except (
            CannotBuild,
            DatabaseError,
            ValidationError,
            ValueError,
            TypeError,
            LookupError,
            AttributeError,
        ) as error:
            world.problems.append(f"cannot seed {model._meta.label}: {error!r}")
    world.models_seeded = len(rows)

    routes = admin_routes()
    world.routes_seen = len(routes)
    for route in routes:
        _add_route(world, route, by_prefix, rows, request)

    for model in rows:
        _add_extra_variants(world, model)
    _add_autocomplete(world)
    _add_curated(world)
    seen: set[str] = set()
    unique: list[Target] = []
    for target in world.targets:
        if target.key in seen:
            continue
        seen.add(target.key)
        unique.append(target)
    world.targets = sorted(unique, key=lambda target: (target.group, target.key))
    return world


def _expected_for(model, suffix: str, request, row) -> tuple[int, ...]:
    model_admin = admin.site.get_model_admin(model)
    if suffix == "add" and not model_admin.has_add_permission(request):
        return (403,)
    if suffix == "delete" and row is not None:
        if not model_admin.has_delete_permission(request, row):
            return (403,)
    return (200,)


def _add_route(world: World, route: AdminRoute, by_prefix, rows, request) -> None:
    name = route.name
    if name == "admin:console_dashboard_widget":
        _add_widgets(world)
        return
    if name in ROUTE_MODELS:
        _add_sampled(world, route, rows)
        return
    if name in EXPECTED_STATUS:
        path = _reverse_sample(world, route, rows)
        if path:
            model, _suffix = _model_for(name, by_prefix)
            group = model._meta.app_label if model else "admin-core"
            world.targets.append(
                Target(group, name, path, "status", EXPECTED_STATUS[name])
            )
        return
    model, suffix = _model_for(name, by_prefix)
    group = model._meta.app_label if model else "admin-core"
    if not route.kwargs:
        path = reverse(name)
        expected = _expected_for(model, suffix, request, None) if model else (200,)
        world.targets.append(Target(group, name, path, "page", expected))
        return
    if route.kwargs == {"app_label"}:
        for label in sorted({m._meta.app_label for m in registered_models()}):
            world.targets.append(
                Target(
                    "admin-core",
                    f"{name}[{label}]",
                    reverse(name, kwargs={"app_label": label}),
                )
            )
        return
    if route.kwargs == {"object_id"} and model is not None and model in rows:
        row = rows[model]
        path = reverse(name, kwargs={"object_id": str(admin_pk(row))})
        expected = _expected_for(model, suffix, request, row)
        world.targets.append(Target(group, name, path, "page", expected))
        if suffix not in STANDARD_SUFFIXES:
            world.targets.append(
                Target(
                    group,
                    name,
                    path,
                    "fragment",
                    needs_dialog_markup=True,
                )
            )
        return
    if route.kwargs == {"object_id"} and model is not None:
        world.problems.append(f"{name}: no seeded row for {model._meta.label}")
        return
    world.problems.append(
        f"{name}: cannot sample route kwargs {sorted(route.kwargs)} ({route.route})"
    )


def _add_widgets(world: World) -> None:
    """Each dashboard panel: inside the full shell and as its lazy HTMX fragment."""
    for slug in WIDGET_BY_SLUG:
        label = f"admin:console_dashboard_widget[{slug}]"
        path = reverse("admin:console_dashboard_widget", args=[slug])
        world.targets.append(Target("admin-core", label, path))
        world.targets.append(Target("admin-core", label, path, "fragment"))


def _add_sampled(world: World, route: AdminRoute, rows) -> None:
    model_label, kwarg = ROUTE_MODELS[route.name]
    model = django_apps.get_model(model_label)
    row = rows.get(model)
    if row is None:
        world.problems.append(f"{route.name}: no seeded row for {model._meta.label}")
        return
    path = reverse(route.name, kwargs={kwarg: admin_pk(row)})
    expected = EXPECTED_STATUS.get(route.name)
    world.targets.append(
        Target(
            model._meta.app_label,
            route.name,
            path,
            "status" if expected else "page",
            expected or (200,),
        )
    )


def _reverse_sample(world: World, route: AdminRoute, rows) -> str | None:
    if not route.kwargs:
        return reverse(route.name)
    if route.kwargs == {"content_type_id", "object_id"}:
        for model, row in rows.items():
            content_type = ContentType.objects.get_for_model(model)
            if hasattr(row, "get_absolute_url"):
                return reverse(
                    route.name,
                    kwargs={
                        "content_type_id": content_type.pk,
                        "object_id": str(admin_pk(row)),
                    },
                )
        world.problems.append(f"{route.name}: no seeded row has get_absolute_url")
        return None
    world.problems.append(f"{route.name}: cannot sample {sorted(route.kwargs)}")
    return None


def _add_extra_variants(world: World, model) -> None:
    """Changelist partial (HX-Request) and the empty-result state of the list."""
    label = f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
    try:
        path = reverse(label)
    except NoReverseMatch:
        return
    group = model._meta.app_label
    world.targets.append(Target(group, label, path, "fragment"))
    query = urlencode({"q": "__no_match_for_crawl__"})
    world.targets.append(Target(group, f"{label}?empty", f"{path}?{query}", "page"))


def _add_autocomplete(world: World) -> None:
    url = reverse("admin:autocomplete")
    for model, model_admin in registered_admins().items():
        for field_name in getattr(model_admin, "autocomplete_fields", ()):
            query = urlencode(
                {
                    "app_label": model._meta.app_label,
                    "model_name": model._meta.model_name,
                    "field_name": field_name,
                    "term": "",
                }
            )
            label = f"admin:autocomplete[{model._meta.label_lower}.{field_name}]"
            world.targets.append(
                Target("admin-core", label, f"{url}?{query}", "status", (200,))
            )


def _add_curated(world: World) -> None:
    for name in CURATED_STAFF_PAGES:
        try:
            path = reverse(name)
        except NoReverseMatch:
            world.problems.append(f"{name}: curated staff page is not routable")
            continue
        world.targets.append(Target("staff-pages", name, path))

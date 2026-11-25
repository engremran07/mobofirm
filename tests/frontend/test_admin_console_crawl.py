"""Playwright crawl of every staff-console URL.

The URL world is derived, not hand-listed: every model in ``admin.site._registry`` gets
its changelist / add / change / history / delete page (plus the HTMX list fragment and
the empty-result state), every other named ``admin:`` route is sampled, and the curated
staff pages that live outside the admin namespace are added. Each page is loaded once
and audited at 1280, 768 and 360 px in the light theme, then in the dark theme.

Run a subset while iterating with ``-k "test_crawl_group and blog"``; only the selected
groups are crawled and the report goes to ``crawl-results-partial.json``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from django.test import Client
from playwright.sync_api import Error as PlaywrightError

from tests.frontend.browser_support import chromium
from tests.frontend.console_browser_support import (
    ConsoleServer,
    PageResult,
    Probe,
    check_fragment,
    check_status,
    console_server,
    crawl_page,
    write_results,
)
from tests.frontend.console_world import (
    CURATED_STAFF_PAGES,
    STANDARD_SUFFIXES,
    Target,
    World,
    build_world,
    crawl_groups,
    registered_models,
)

GROUPS = crawl_groups()
GROUP_TEST = "test_crawl_group_has_no_defects"
MAX_REPORTED = 12

# The marker is what makes pytest-django create the test database for this module's
# session-level setup; the seeded world itself lives in the module-scoped fixture below
# (its data is flushed once on teardown), so tests read cached results, not the database.
pytestmark = pytest.mark.django_db(transaction=True)


@dataclass
class Seeded:
    server: ConsoleServer
    world: World
    # Plain Django-client status of every target, taken before the browser starts.
    preflight: dict[str, int]


def _preflight(server: ConsoleServer, world: World) -> dict[str, int]:
    client = Client(raise_request_exception=False)
    client.force_login(server.user)
    statuses: dict[str, int] = {}
    for target in world.targets:
        headers = {"HX-Request": "true"} if target.mode == "fragment" else {}
        statuses[target.key + target.path] = client.get(
            target.path, headers=headers
        ).status_code
    return statuses


@pytest.fixture(name="console_world", scope="module")
def console_world_fixture(request, django_db_blocker):
    """Live server + one seeded row per model; the ORM work happens before Playwright."""
    request.getfixturevalue("django_db_setup")
    with console_server(django_db_blocker, username="console-crawler") as server:
        world = build_world(server.user)
        yield Seeded(server, world, _preflight(server, world))


def _selected_groups(request: pytest.FixtureRequest) -> set[str]:
    chosen = {
        str(item.callspec.params["group"])
        for item in request.session.items
        if isinstance(item, pytest.Function)
        and item.originalname == GROUP_TEST
        and hasattr(item, "callspec")
    }
    return chosen


@pytest.fixture(name="crawl_results", scope="module")
def crawl_results_fixture(request, console_world) -> dict[str, list[PageResult]]:
    groups = _selected_groups(request)
    seeded: Seeded = console_world
    targets = [target for target in seeded.world.targets if target.group in groups]
    results = _crawl(seeded.server, targets)
    write_results(
        results,
        {
            "base": "http://localhost (live test server, offline)",
            "groups": sorted(groups),
            "models_seeded": seeded.world.models_seeded,
            "routes_seen": seeded.world.routes_seen,
            "targets": len(targets),
            "world_problems": seeded.world.problems,
        },
        partial=groups != set(GROUPS),
    )
    grouped: dict[str, list[PageResult]] = {}
    for result in results:
        grouped.setdefault(result.group, []).append(result)
    return grouped


def _crawl(server: ConsoleServer, targets: list[Target]) -> list[PageResult]:
    results: list[PageResult] = []
    with chromium() as browser:
        context = server.context(browser)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        for target in targets:
            try:
                results.append(_visit(context, page, probe, server, target))
            except PlaywrightError as error:
                failed = PageResult(
                    target.group,
                    target.label,
                    target.path,
                    target.mode,
                    list(target.expected),
                    problems=[f"browser error: {error}"],
                )
                results.append(failed)
                if page.is_closed():
                    page = context.new_page()
                    probe = Probe(origin=server.url)
                    probe.attach(page)
        context.close()
    return results


def _visit(context, page, probe, server: ConsoleServer, target: Target) -> PageResult:
    if target.mode == "status":
        return check_status(
            context,
            server.url,
            target.group,
            target.label,
            target.path,
            target.expected,
        )
    if target.mode == "fragment":
        return check_fragment(
            context,
            server.url,
            target.group,
            target.label,
            target.path,
            target.needs_dialog_markup,
        )
    return crawl_page(
        page,
        probe,
        server.url,
        target.group,
        target.label,
        target.path,
        target.expected,
    )


def test_world_covers_every_registered_model_and_route(console_world):
    world = console_world.world
    labels = {target.label for target in world.targets}

    assert not world.problems, "\n".join(world.problems)
    assert world.models_seeded == len(registered_models())
    assert world.groups <= set(GROUPS), world.groups - set(GROUPS)
    for model in registered_models():
        for suffix in STANDARD_SUFFIXES:
            name = f"admin:{model._meta.app_label}_{model._meta.model_name}_{suffix}"
            assert name in labels, f"{name} is not part of the crawl"
    for name in CURATED_STAFF_PAGES:
        assert name in labels, name
    keys = [target.key for target in world.targets]
    assert len(keys) == len(set(keys))
    assert {"page", "fragment", "status"} <= {target.mode for target in world.targets}


def test_world_expects_every_post_only_route_to_refuse_get(console_world):
    statuses = {
        target.label: target.expected
        for target in console_world.world.targets
        if target.mode == "status" and target.group == "admin-core"
    }

    assert statuses["admin:logout"] == (405,)
    assert statuses["admin:login"] == (302,)


def test_every_target_answers_the_plain_django_client_as_expected(console_world):
    """Cheap pre-flight: catches 500s and POST-only routes before any browser starts."""
    wrong = [
        f"{target.label} [{target.mode}] {target.path} -> "
        f"{console_world.preflight[target.key + target.path]}, "
        f"expected {list(target.expected)}"
        for target in console_world.world.targets
        if console_world.preflight[target.key + target.path] not in target.expected
    ]

    assert not wrong, f"{len(wrong)} target(s)\n" + "\n".join(wrong[:60])


@pytest.mark.parametrize("group", GROUPS)
def test_crawl_group_has_no_defects(group, crawl_results, console_world):
    expected = [target.key for target in console_world.world.for_group(group)]
    crawled = crawl_results.get(group, [])
    assert expected, f"{group}: the URL world has no pages for this group"
    assert sorted(f"{result.mode}:{result.label}" for result in crawled) == sorted(
        expected
    ), f"{group}: the crawl did not visit every page"

    failing = [result for result in crawled if not result.passed]
    lines = [
        f"{result.label} [{result.mode}] {result.path} -> {result.status}\n    "
        + "\n    ".join(result.problems[:MAX_REPORTED])
        for result in failing[:MAX_REPORTED]
    ]
    assert not failing, (
        f"{group}: {len(failing)} of {len(crawled)} pages failed\n" + "\n".join(lines)
    )

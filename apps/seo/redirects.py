"""Redirect registry helpers: validation, loop detection and chain flattening."""

from __future__ import annotations

from urllib.parse import urlsplit

from .models import Redirect

MAX_HOPS = 10
_UNSAFE_CHARS = ("\\", "\r", "\n", "\t", "\x00")


class RedirectLoop(ValueError):
    """Following the redirect graph revisits a path."""


def is_safe_target(target: str) -> bool:
    """A site path, or an absolute http(s) URL. Never scheme-relative or backslashed.

    ``/\\evil.example`` is treated as ``//evil.example`` by browsers, so backslashes
    and control characters are refused outright.
    """
    if not target or any(char in target for char in _UNSAFE_CHARS):
        return False
    if target.startswith("/"):
        return not target.startswith("//")
    parts = urlsplit(target)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def active_targets(exclude_pk: int | None = None) -> dict[str, str]:
    rows = Redirect.objects.filter(is_active=True)
    if exclude_pk is not None:
        rows = rows.exclude(pk=exclude_pk)
    return dict(rows.values_list("source", "target"))


def follow(
    start: str, targets: dict[str, str] | None = None
) -> tuple[list[str], str | None]:
    """Walk redirects from ``start``; return the visited paths and the final target.

    Raises :class:`RedirectLoop` when a path repeats or the chain is too long.
    """
    graph = targets if targets is not None else active_targets()
    visited = [start]
    current = start
    while current in graph:
        nxt = graph[current]
        if nxt in visited:
            raise RedirectLoop(" -> ".join([*visited, nxt]))
        visited.append(nxt)
        current = nxt
        if len(visited) > MAX_HOPS:
            raise RedirectLoop(f"Redirect chain longer than {MAX_HOPS} hops.")
    return visited, current if len(visited) > 1 else None


def validate_redirect(
    source: str, target: str, *, exclude_pk: int | None = None
) -> list[str]:
    """Reasons the pair must not be stored; an empty list means it is safe."""
    errors: list[str] = []
    if (
        not source.startswith("/")
        or source.startswith("//")
        or any(char in source for char in _UNSAFE_CHARS)
    ):
        errors.append("Source must be a site path starting with a single '/'.")
    if not is_safe_target(target):
        errors.append("Target must be a site path or an http(s) URL.")
    if errors:
        return errors
    if source == target:
        return ["Target must differ from the source."]
    if not target.startswith("/"):
        return []
    graph = active_targets(exclude_pk)
    graph[source] = target
    try:
        follow(source, graph)
    except RedirectLoop as loop:
        errors.append(f"Redirect loop: {loop}")
    return errors


def flattened_target(source: str) -> str | None:
    """Final destination for a redirect whose target is itself redirected."""
    graph = active_targets()
    if source not in graph:
        return None
    try:
        visited, final = follow(source, graph)
    except RedirectLoop:
        return None
    if final is None or len(visited) <= 2:
        return None
    return final

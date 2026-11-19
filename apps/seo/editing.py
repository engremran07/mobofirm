"""Apply a reviewer's edits to one object (or route) atomically.

One submission can change several SEO override fields, declared object fields (slug,
summary, description) and the images/headings of an HTML body. Everything is validated
first; the writes then happen in one transaction, each as a ``ChangeLedger`` entry with
before/after values (so every change can be rolled back); and after the commit the
object is re-audited and *republished*: the SEO job outbox is told the page changed,
which refreshes the sitemap view of it (sitemaps are generated live), and notifies
IndexNow when the autonomy mode allows. A value equal to the stored one is skipped, so
repeating a submission writes nothing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from . import editable, fixes, jobs, ledger
from .contentsources import content_sources
from .inventory import resolve_path
from .models import ChangeLedger, Issue, Metadata


@dataclass
class ChangeSet:
    """What a reviewer submitted for one object."""

    overrides: dict[str, str] = field(default_factory=dict)
    object_fields: dict[str, str] = field(default_factory=dict)
    # Alt texts for the images that lack one, keyed by their position among those.
    alts: dict[int, str] = field(default_factory=dict)
    alt_sources: dict[int, str] = field(default_factory=dict)
    demote_h1: bool = False

    @property
    def empty(self) -> bool:
        return not (self.overrides or self.object_fields or self.alts or self.demote_h1)


@dataclass
class Applied:
    entries: list[ChangeLedger]
    path: str
    previous_path: str


def override_values(label: str, pk: str) -> dict[str, Any]:
    """Stored override values for an object (empty when it has none)."""
    app_label, _, model = label.partition(".")
    metadata = (
        Metadata.objects.filter(
            seo__content_type__app_label=app_label,
            seo__content_type__model=model,
            seo__object_id=str(pk),
            seo__is_deleted=False,
        )
        .select_related("seo")
        .first()
    )
    if metadata is None:
        return {}
    return {name: getattr(metadata, name) for name in fixes.OVERRIDE_SPECS}


def _key(label: str, pk: str, name: str, digest: str) -> str:
    owner = hashlib.sha256(f"{label}:{pk}".encode()).hexdigest()[:12]
    base = f"fix:{owner}:{name}:{digest}"
    taken = ChangeLedger.objects.filter(idempotency_key__startswith=f"{base}:").count()
    return f"{base}:{taken}"[:128]


@dataclass(frozen=True)
class Context:
    user: Any
    source: str
    title: str
    mode: str


def _entry(
    context: Context,
    *,
    kind: str,
    action: str,
    target_model: str,
    target_pk: str,
    target_label: str,
    before: dict[str, Any],
    after: dict[str, Any],
    reason: str,
    key: str,
) -> ChangeLedger:
    user = context.user
    return ChangeLedger.objects.create(
        idempotency_key=key,
        kind=kind,
        action=action,
        target_model=target_model,
        target_pk=target_pk,
        target_label=target_label[:255],
        before=before,
        after=after,
        reason=reason[:500],
        source=context.source,
        mode=context.mode,
        requires_approval=False,
        decided_by=user if getattr(user, "is_authenticated", False) else None,
        decided_at=timezone.now(),
        validation={"errors": []},
    )


def _applied(entry: ChangeLedger, user: Any) -> ChangeLedger:
    entry = ledger.apply_change(entry, user)
    if entry.status != ChangeLedger.Status.APPLIED:
        raise fixes.FixError(entry.error or "The change could not be applied.")
    return entry


def _write_override(
    context: Context, label: str, pk: str, name: str, value: str
) -> ChangeLedger | None:
    final = value.strip() if name == "robots_directives" else " ".join(value.split())
    problems = fixes.validate_value(name, final)
    if problems:
        raise fixes.FixError(" ".join(problems))
    typed = fixes.typed(name, final)
    container = fixes.container_for(label, pk)
    metadata = Metadata.objects.filter(seo=container).first()
    if metadata is not None:
        current = getattr(metadata, name)
        if current == typed:
            return None
        action, target_pk = ChangeLedger.Action.UPDATE, str(metadata.pk)
        before, after = {name: current}, {name: typed}
    else:
        if typed in ("", False):
            return None
        action, target_pk = ChangeLedger.Action.CREATE, ""
        before, after = {}, {"seo_id": container.pk, name: typed}
    spec = fixes.OVERRIDE_SPECS[name]
    entry = _entry(
        context,
        kind=fixes.KIND_REWRITE if before.get(name) else fixes.KIND_ADD,
        action=action,
        target_model="seo.metadata",
        target_pk=target_pk,
        target_label=f"{context.title}: {spec.label}",
        before=before,
        after=after,
        reason=f"Reviewed {spec.label.lower()}",
        key=_key(label, pk, name, hashlib.sha256(str(typed).encode()).hexdigest()[:12]),
    )
    return _applied(entry, context.user)


def _write_object(
    context: Context, label: str, pk: str, name: str, value: str, reason: str
) -> ChangeLedger | None:
    obj = editable.load(label, pk)
    if obj is None:
        raise fixes.FixError("The object no longer exists.")
    current = editable.read(obj, name)
    if value == current:
        return None
    entry = _entry(
        context,
        kind=fixes.KIND_OBJECT,
        action=ChangeLedger.Action.UPDATE,
        target_model=label,
        target_pk=str(pk),
        target_label=f"{context.title}: {name}",
        before={name: current},
        after={name: value},
        reason=reason,
        key=_key(label, pk, name, hashlib.sha256(value.encode()).hexdigest()[:12]),
    )
    return _applied(entry, context.user)


def _html_value(label: str, obj: Any, changes: ChangeSet) -> tuple[str, str] | None:
    """(field name, new markup) for the image/heading repairs, or ``None``."""
    name = editable.html_field_for(label)
    if not name:
        if changes.alts or changes.demote_h1:
            raise fixes.FixError("This object has no editable body.")
        return None
    markup = editable.read(obj, name)
    new = markup
    if changes.alts:
        images = [image for image in editable.body_images(markup) if image.alt is None]
        absolute: dict[int, str] = {}
        for position, text in changes.alts.items():
            expected = changes.alt_sources.get(position)
            if position >= len(images) or (
                expected is not None and images[position].src != expected
            ):
                raise fixes.FixError(
                    "The images changed since this screen loaded; reload and retry."
                )
            absolute[images[position].index] = text
        new = editable.set_image_alts(new, absolute)
    if changes.demote_h1:
        new = editable.demote_h1(new)
    return (name, new) if new != markup else None


def validate(label: str, pk: str, changes: ChangeSet) -> list[str]:
    """Every reason the submission cannot be applied, before anything is written."""
    problems: list[str] = []
    for name, value in changes.overrides.items():
        if name not in fixes.OVERRIDE_SPECS:
            problems.append(f"{name} cannot be edited here.")
        else:
            problems.extend(fixes.validate_value(name, value.strip()))
    if changes.object_fields or changes.alts or changes.demote_h1:
        obj = editable.load(label, pk)
        if obj is None:
            return [*problems, "This page has no editable content object."]
        specs = {spec.name: spec for spec in editable.specs_for(label)}
        for name, value in changes.object_fields.items():
            spec = specs.get(name)
            if spec is None:
                problems.append(f"{name} cannot be edited on this object.")
            else:
                problems.extend(editable.validate_object_value(obj, spec, value))
        problems.extend(
            "Alt text cannot be empty."
            for text in changes.alts.values()
            if not text.strip()
        )
    return problems


def republish(label: str, pk: str, path: str) -> None:
    """Tell the job outbox a page changed (re-scan, IndexNow when allowed) on commit."""
    resolved = resolve_path(path)
    public = bool(resolved and resolved.indexable)
    clean = urlsplit(path).path or "/"
    transaction.on_commit(
        partial(
            jobs.record_content_change,
            label,
            str(pk),
            url=clean,
            public=public,
            previous=(clean, public),
        )
    )


def apply_changes(
    label: str,
    pk: str,
    path: str,
    changes: ChangeSet,
    user: Any,
    *,
    source: str = "review:screen",
    issues: list[Issue] | None = None,
) -> Applied:
    """Validate, write atomically with ledger entries, re-audit and republish."""
    row = fixes.check_mode()
    if changes.empty:
        raise fixes.FixError("Nothing was changed.")
    problems = validate(label, pk, changes)
    if problems:
        raise fixes.FixError(" ".join(problems))
    context = Context(user, source, path or label, row.autonomy_mode)
    entries: list[ChangeLedger] = []
    new_path = path
    with transaction.atomic():
        for name, value in changes.overrides.items():
            made = _write_override(context, label, pk, name, value)
            if made is not None:
                entries.append(made)
        for name, value in changes.object_fields.items():
            made = _write_object(
                context, label, pk, name, value.strip(), f"Reviewed {name}"
            )
            if made is not None:
                entries.append(made)
        obj = editable.load(label, pk)
        if obj is not None and (changes.alts or changes.demote_h1):
            html_change = _html_value(label, obj, changes)
            if html_change is not None:
                made = _write_object(
                    context,
                    label,
                    pk,
                    html_change[0],
                    html_change[1],
                    "Repaired images and headings in the body",
                )
                if made is not None:
                    entries.append(made)
        if not entries:
            raise fixes.FixError("Nothing changed: the stored values already match.")
        refreshed = editable.load(label, pk)
        info = content_sources().get(label)
        if refreshed is not None and info is not None:
            new_path = info.url(refreshed) or path
        if issues:
            Issue.objects.filter(pk__in=[issue.pk for issue in issues]).update(
                fix_ledger=entries[0]
            )
    from . import scan

    scan.rescan_paths(list(dict.fromkeys([path, new_path])))
    republish(label, pk, new_path)
    return Applied(entries, new_path, path)

"""Report duplicate, empty, and unreadable files discovered by staticfiles finders."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import PurePosixPath

from django.contrib.staticfiles.finders import get_finders
from django.core.files.storage import Storage
from django.core.management.base import BaseCommand, CommandError


def _content_hash(storage: Storage, name: str) -> str:
    """Return a streaming SHA-256 digest for one stored asset."""
    hasher = hashlib.sha256()
    with storage.open(name, "rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _scan_assets() -> tuple[
    dict[str, list[str]],
    dict[str, list[str]],
    list[str],
    list[str],
    int,
]:
    """Collect asset paths, content hashes, empty files, and read failures."""
    locations: dict[str, list[str]] = defaultdict(list)
    contents: dict[str, list[str]] = defaultdict(list)
    empty: list[str] = []
    unreadable: list[str] = []
    asset_count = 0

    for finder in get_finders():
        for name, storage in finder.list(ignore_patterns=[]):
            asset_count += 1
            normalized = PurePosixPath(name.replace("\\", "/")).as_posix()
            origin = str(getattr(storage, "location", storage))
            locations[normalized].append(origin)
            try:
                if not storage.size(name):
                    empty.append(f"{normalized} ({origin})")
                    continue
                contents[_content_hash(storage, name)].append(
                    f"{normalized} ({origin})"
                )
            except (OSError, NotImplementedError) as error:
                unreadable.append(f"{normalized} ({origin}): {error}")
    return locations, contents, empty, unreadable, asset_count


def _duplicate_groups(index: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return keys associated with more than one source location."""
    return {key: values for key, values in index.items() if len(values) > 1}


class Command(BaseCommand):
    """Report duplicate, empty, and unreadable source static assets."""

    help = (
        "Audit source assets for duplicate paths or content, empty files, and "
        "unreadable files. Findings are reports only; no files are removed."
    )

    def add_arguments(self, parser):
        """Register options that control whether findings fail the command."""
        parser.add_argument(
            "--report-only",
            action="store_true",
            help="Return success even when findings are reported.",
        )

    def handle(self, *args, **options):
        """Inspect static finder files without modifying or deleting any asset."""
        locations, contents, empty, unreadable, asset_count = _scan_assets()
        duplicates = _duplicate_groups(locations)
        duplicate_contents = _duplicate_groups(contents)
        finding_count = len(duplicates) + len(empty) + len(unreadable)
        self.stdout.write(f"Scanned {asset_count} source static files.")

        self._write_findings(duplicates, duplicate_contents, empty, unreadable)
        if finding_count:
            message = (
                f"Static audit found {finding_count} issue(s); review them before "
                "removing or changing any source asset."
            )
            if not options["report_only"]:
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(message))
            return

        if duplicate_contents:
            self.stdout.write(
                self.style.WARNING(
                    f"Found {len(duplicate_contents)} identical-content group(s); "
                    "review them for intentional or vendor-provided copies."
                )
            )
        self.stdout.write(
            self.style.SUCCESS("No path collisions, empty, or unreadable files found.")
        )

    def _write_findings(
        self,
        duplicates: dict[str, list[str]],
        duplicate_contents: dict[str, list[str]],
        empty: list[str],
        unreadable: list[str],
    ) -> None:
        """Print each finding without changing its source asset."""
        for name, origins in sorted(duplicates.items()):
            self.stderr.write(f"Duplicate path: {name}")
            for origin in origins:
                self.stderr.write(f"  {origin}")
        for digest, paths in sorted(duplicate_contents.items()):
            self.stderr.write(
                f"Duplicate content (review; assets are retained): sha256:{digest}"
            )
            for path in paths:
                self.stderr.write(f"  {path}")
        for item in sorted(empty):
            self.stderr.write(f"Empty file: {item}")
        for item in sorted(unreadable):
            self.stderr.write(f"Unreadable file: {item}")

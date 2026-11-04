"""
URL Smoke Test Command

Resolves all URL patterns, renders templates via Django's test client,
and collects HTTP errors, template errors, and missing context variables.
Outputs a structured report of all issues found.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test.client import Client
from django.urls import URLPattern, URLResolver

logger = logging.getLogger(__name__)

User = get_user_model()

# Patterns that need dynamic args — skip unless we have a known PK
SKIP_PATTERNS = {
    # Admin CRUD/detail
    r"<int:pk>",
    r"<slug:slug>",
    r"<str:token>",
    r"<uuid:",
    # Auth tokens
    r"<str:key>",
    r"<str:uidb64>",
    # Webhook/callback endpoints
    "webhook",
    "callback",
    # Media/static
    "media/",
    "static/",
    # API endpoints needing auth/POST
    "api/v1/",
}


def _extract_url_patterns(urlconf: Any, prefix: str = "") -> list[tuple[str, str, str]]:
    """Extract all URL patterns recursively.

    Returns list of (full_path, name, namespace).
    """
    results: list[tuple[str, str, str]] = []
    patterns = getattr(urlconf, "urlpatterns", urlconf)

    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            new_prefix = prefix + str(pattern.pattern)
            results.extend(_extract_url_patterns(pattern, new_prefix))
        elif isinstance(pattern, URLPattern):
            full_path = prefix + str(pattern.pattern)
            name = pattern.name or ""
            results.append((full_path, name, ""))

    return results


def _is_parameterized(path: str) -> bool:
    """Check if a URL pattern requires dynamic parameters."""
    return bool(re.search(r"<\w+:\w+>", path))


def _should_skip(path: str) -> bool:
    """Check if a URL should be skipped based on SKIP_PATTERNS."""
    for skip in SKIP_PATTERNS:
        if skip in path:
            return True
    return False


class Command(BaseCommand):
    help = (
        "Smoke test all URL patterns by loading them via Django test client. "
        "Reports HTTP errors, template errors, and missing variables."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--include-api",
            action="store_true",
            help="Include API endpoints (usually need auth)",
        )
        parser.add_argument(
            "--include-admin",
            action="store_true",
            help="Include admin panel URLs",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show every URL being tested",
        )
        parser.add_argument(
            "--fail-fast",
            action="store_true",
            help="Stop on first error",
        )
        parser.add_argument(
            "--pattern",
            type=str,
            default="",
            help="Only test URLs matching this substring",
        )

    def handle(self, *args, **options):
        include_api = options["include_api"]
        include_admin = options["include_admin"]
        verbose = options["verbose"]
        fail_fast = options["fail_fast"]
        pattern_filter = options["pattern"]

        self.stdout.write(self.style.SUCCESS("=== URL Smoke Test ===\n"))

        # Import the root URL conf
        urlconf = __import__(settings.ROOT_URLCONF, fromlist=["urlpatterns"])
        all_patterns = _extract_url_patterns(urlconf)

        self.stdout.write(f"Found {len(all_patterns)} total URL patterns\n")

        # Filter
        testable: list[str] = []
        skipped_param = 0
        skipped_filter = 0

        for path, _name, _ns in all_patterns:
            # Skip parameterized URLs
            if _is_parameterized(path) or _should_skip(path):
                skipped_param += 1
                continue
            # Skip API unless requested
            if not include_api and "api/" in path:
                skipped_filter += 1
                continue
            # Skip admin unless requested
            if not include_admin and path.startswith("admin-suite/"):
                skipped_filter += 1
                continue
            # Pattern filter
            if pattern_filter and pattern_filter not in path:
                skipped_filter += 1
                continue

            # Normalize path
            url = "/" + path.rstrip("$").lstrip("^")
            if not url.endswith("/") and "." not in url.split("/")[-1]:
                url += "/"
            testable.append(url)

        self.stdout.write(
            f"Testing {len(testable)} URLs "
            f"(skipped {skipped_param} parameterized, "
            f"{skipped_filter} filtered)\n"
        )

        # Initialize test client
        client = Client()

        # Results tracking
        results: dict[str, list[dict[str, Any]]] = {
            "success": [],
            "redirect": [],
            "client_error": [],
            "server_error": [],
            "exception": [],
        }
        missing_vars: list[str] = []
        start_time = time.monotonic()

        for url in sorted(set(testable)):
            if verbose:
                self.stdout.write(f"  Testing: {url} ... ", ending="")

            try:
                response = client.get(url, follow=False)
                status = response.status_code
                content = ""

                if hasattr(response, "content"):
                    try:
                        content = response.content.decode("utf-8", errors="replace")
                    except Exception:
                        content = ""

                entry = {
                    "url": url,
                    "status": status,
                }

                if status < 300:
                    results["success"].append(entry)
                    # Check for missing template variables
                    if "!!! MISSING:" in content:
                        missing = re.findall(r"!!! MISSING: (\S+?) !!!", content)
                        for var in missing:
                            missing_vars.append(f"{url} -> {var}")
                        entry["missing_vars"] = missing
                    if verbose:
                        self.stdout.write(self.style.SUCCESS(f"{status} OK"))
                elif status < 400:
                    location = response.get("Location", "?")
                    entry["redirect_to"] = location
                    results["redirect"].append(entry)
                    if verbose:
                        self.stdout.write(self.style.WARNING(f"{status} -> {location}"))
                elif status < 500:
                    results["client_error"].append(entry)
                    if verbose:
                        self.stdout.write(self.style.WARNING(f"{status}"))
                else:
                    results["server_error"].append(entry)
                    if verbose:
                        self.stdout.write(self.style.ERROR(f"{status} SERVER ERROR"))

                if fail_fast and status >= 500:
                    self.stdout.write(
                        self.style.ERROR(f"\nFail-fast: stopping at {url} ({status})")
                    )
                    break

            except Exception as exc:
                results["exception"].append({"url": url, "error": str(exc)})
                if verbose:
                    self.stdout.write(self.style.ERROR(f"EXCEPTION: {exc}"))
                if fail_fast:
                    break

        elapsed = time.monotonic() - start_time

        # Report
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            self.style.SUCCESS(f"\n  Smoke Test Results ({elapsed:.2f}s)\n")
        )
        self.stdout.write("=" * 60)

        self.stdout.write(
            self.style.SUCCESS(f"  OK (2xx):       {len(results['success'])}")
        )
        self.stdout.write(
            self.style.WARNING(f"  Redirects (3xx): {len(results['redirect'])}")
        )
        self.stdout.write(
            self.style.WARNING(f"  Client (4xx):   {len(results['client_error'])}")
        )
        self.stdout.write(
            self.style.ERROR(f"  Server (5xx):   {len(results['server_error'])}")
        )
        self.stdout.write(
            self.style.ERROR(f"  Exceptions:     {len(results['exception'])}")
        )

        # Detail sections
        if results["server_error"]:
            self.stdout.write(self.style.ERROR("\n--- Server Errors (5xx) ---"))
            for entry in results["server_error"]:
                self.stdout.write(f"  {entry['status']} {entry['url']}")

        if results["exception"]:
            self.stdout.write(self.style.ERROR("\n--- Exceptions ---"))
            for entry in results["exception"]:
                self.stdout.write(f"  {entry['url']}: {entry['error']}")

        if missing_vars:
            self.stdout.write(
                self.style.WARNING("\n--- Missing Template Variables ---")
            )
            for mv in missing_vars:
                self.stdout.write(f"  {mv}")

        if results["client_error"]:
            self.stdout.write(self.style.WARNING("\n--- Client Errors (4xx) ---"))
            for entry in results["client_error"]:
                self.stdout.write(f"  {entry['status']} {entry['url']}")

        # Summary
        total_issues = (
            len(results["server_error"]) + len(results["exception"]) + len(missing_vars)
        )
        if total_issues == 0:
            self.stdout.write(self.style.SUCCESS("\n All URLs passed smoke test!"))
        else:
            self.stdout.write(self.style.ERROR(f"\n {total_issues} issue(s) found."))

"""Public endpoints: safe verification files, the JSON bootstrap and the theme CSS."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from django.apps import apps as django_apps
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
    JsonResponse,
)
from django.urls import reverse
from django.views.decorators.http import require_GET, require_safe

from . import validators
from .models import VerificationFile
from .services import build_theme_css, get_site_settings, public_settings

logger = logging.getLogger(__name__)

# Legacy public paths and the CMS page slugs that may now hold their content.
LEGACY_LEGAL_SLUGS: dict[str, tuple[str, ...]] = {
    "privacy": ("privacy",),
    "terms": ("terms",),
    "cookies": ("cookies", "consent"),
}


def _serve_verification(
    filename: str, allowed_extensions: frozenset[str]
) -> FileResponse:
    extension = PurePosixPath(filename).suffix.lower()
    if (
        not validators.SAFE_FILENAME.fullmatch(filename)
        or extension not in allowed_extensions
        or filename not in get_site_settings().verification_filenames
    ):
        raise Http404("Unknown verification file.")
    row = VerificationFile.objects.filter(filename=filename, is_active=True).first()
    if row is None:
        raise Http404("Unknown verification file.")
    try:
        handle = row.file.open("rb")
    except FileNotFoundError as exc:
        logger.error("Verification file %s is missing from storage.", filename)
        raise Http404("Unknown verification file.") from exc
    response = FileResponse(
        handle, content_type=validators.VERIFICATION_CONTENT_TYPES[extension]
    )
    # Staff upload these files; the sandbox keeps any markup inert on our origin.
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "public, max-age=300"
    return response


@require_safe
def verification_file(request: HttpRequest, filename: str) -> FileResponse:
    return _serve_verification(filename, validators.VERIFICATION_EXTENSIONS)


@require_safe
def verification_file_root(request: HttpRequest, filename: str) -> FileResponse:
    """The same files at the site root, where search engines look for them."""
    return _serve_verification(filename, validators.ROOT_VERIFICATION_EXTENSIONS)


@require_GET
def site_info(request: HttpRequest) -> JsonResponse:
    response = JsonResponse(public_settings())
    response["Cache-Control"] = "public, max-age=60"
    return response


@require_GET
def theme_css(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(
        build_theme_css(get_site_settings()), content_type="text/css; charset=utf-8"
    )
    response["Cache-Control"] = "public, max-age=3600"
    return response


@require_safe
def legacy_legal_redirect(
    request: HttpRequest, key: str
) -> HttpResponsePermanentRedirect:
    """Send an old /privacy, /terms or /cookies link to its CMS page, if published."""
    if key not in LEGACY_LEGAL_SLUGS or not django_apps.is_installed("apps.pages"):
        raise Http404("No such page.")
    from apps.pages.widgets import published_page_slugs

    live = published_page_slugs()
    for slug in LEGACY_LEGAL_SLUGS[key]:
        if slug in live:
            return HttpResponsePermanentRedirect(
                reverse("pages:page", kwargs={"slug": slug})
            )
    raise Http404("No such page.")

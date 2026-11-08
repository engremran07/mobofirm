"""The one metadata resolver. Every page head comes from :func:`resolve`.

Precedence (highest first):

1. Indexation policy that cannot be overridden: private routes, unmapped routes,
   parameterised variants and thin-content rules force ``noindex``.
2. An explicit ``Metadata`` override stored for the page's content object.
3. Verified content-object data supplied by the page provider.
4. Page-type defaults supplied by the provider.
5. Site defaults from ``SEOSettings``.
6. A conservative fallback.

Every emitted field records where it came from in ``provenance``.
"""

from __future__ import annotations

import re
import weakref
from collections.abc import Mapping
from typing import Any

from django.conf import settings as django_settings
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils import translation

from apps.site_settings.services import get_site_settings

from . import schema
from .contracts import POLICY_VERSION, PageSEO, ResolvedSEO
from .models import Metadata, SEOSettings
from .policy import (
    canonical_query,
    compose_title,
    is_private_path,
    robots_directive,
    truncate,
)
from .providers import ProviderContext, get_provider
from .siteurls import absolute_url, is_absolute_http, site_origin

FALLBACK_DESCRIPTION = (
    "Explore mobile firmware, device models and technical guides in the Mobofirm "
    "catalogue."
)
DESCRIPTION_LIMIT = 160
_ROBOTS_TOKENS = re.compile(
    r"^(index|noindex|follow|nofollow|noarchive|nosnippet|noimageindex|"
    r"max-snippet:-?\d+|max-image-preview:(none|standard|large)|"
    r"max-video-preview:-?\d+)$"
)

_cache: weakref.WeakKeyDictionary[HttpRequest, ResolvedSEO] = (
    weakref.WeakKeyDictionary()
)


def humanise(view_name: str, path: str) -> str:
    """Conservative title for a route without a provider; never invents content."""
    label = view_name.split(":")[-1] if view_name else ""
    label = re.sub(r"[_-]+", " ", label).strip() or path.strip("/").split("/")[-1]
    return label.capitalize() if label else ""


def _fallback_page(ctx: ProviderContext) -> PageSEO:
    request = ctx.request
    if request.resolver_match is None:
        return PageSEO(
            page_type="unmapped",
            title="Page not found",
            index=False,
            follow=False,
            noindex_reason="no matching route",
        )
    private = is_private_path(request.path)
    return PageSEO(
        page_type="private" if private else "unmapped",
        title=humanise(ctx.view_name, request.path),
        index=False,
        follow=not private,
        noindex_reason=(
            "private area" if private else "route has no registered SEO provider"
        ),
    )


def override_for(ref: tuple[str, str] | None) -> Metadata | None:
    """Explicit administrator metadata stored for a content object, if any."""
    if ref is None:
        return None
    label, object_id = ref
    app_label, _, model = label.partition(".")
    return (
        Metadata.objects.select_related("seo")
        .filter(
            seo__content_type__app_label=app_label,
            seo__content_type__model=model,
            seo__object_id=str(object_id),
            seo__is_deleted=False,
        )
        .first()
    )


def _valid_robots(value: str) -> str:
    tokens = [token.strip().lower() for token in value.split(",") if token.strip()]
    if tokens and all(_ROBOTS_TOKENS.match(token) for token in tokens):
        return ", ".join(tokens)
    return ""


def _search_template(request: HttpRequest) -> str:
    try:
        path = reverse("firmwares:universal_search")
    except NoReverseMatch:
        return ""
    return f"{absolute_url(path, request)}?q={{search_term_string}}"


def _site_nodes(
    request: HttpRequest, row: SEOSettings, site_name: str
) -> list[dict[str, Any]]:
    origin = site_origin(request)
    if not origin:
        return []
    home = f"{origin}/"
    logo = row.organization_logo or absolute_url(get_site_settings().logo_url, request)
    return [
        schema.organization(name=site_name, url=home, logo=logo),
        schema.website(
            name=site_name, url=home, search_url_template=_search_template(request)
        ),
    ]


def resolve(
    request: HttpRequest, context: Mapping[str, Any] | None = None
) -> ResolvedSEO:
    cached = _cache.get(request)
    if cached is not None:
        return cached
    resolved = _resolve(request, context or {})
    _cache[request] = resolved
    return resolved


def _resolve(request: HttpRequest, context: Mapping[str, Any]) -> ResolvedSEO:
    row = SEOSettings.get_solo()
    site = get_site_settings()
    site_name = row.site_name or site.site_name
    match = request.resolver_match
    view_name = match.view_name if match else ""
    ctx = ProviderContext(
        request=request,
        context=context,
        settings=row,
        view_name=view_name,
        kwargs=match.kwargs if match else {},
    )
    provider = get_provider(view_name)
    page = provider(ctx) if provider else None
    if page is None:
        page = _fallback_page(ctx)

    sources = dict(page.sources)
    warnings: list[str] = []
    index, follow, reason = page.index, page.follow, page.noindex_reason

    if is_private_path(request.path):
        index, follow, reason = False, False, "private area"
    query, variants = canonical_query(request, page.canonical_params)
    if variants and index:
        index, reason = False, f"parameterised view ({', '.join(variants)})"

    canonical = page.canonical or absolute_url(page.path or request.path, request)
    if canonical and query and not page.canonical:
        canonical = f"{canonical}?{query}"
    sources.setdefault("canonical", "route")

    title, full_title, description = page.title, page.full_title, page.description
    title_source = sources.get("title", "object")
    description_source = sources.get("description", "object")
    social_title = social_description = social_image = robots_override = ""

    override = override_for(page.object_ref)
    if override is not None:
        if override.meta_title:
            title, full_title, title_source = override.meta_title, True, "override"
        if override.meta_description:
            description, description_source = override.meta_description, "override"
        if is_absolute_http(override.canonical_url):
            canonical, sources["canonical"] = override.canonical_url, "override"
        if override.noindex and index:
            index, reason = False, "administrator override"
        if override.nofollow:
            follow = False
        robots_override = _valid_robots(override.robots_directives)
        social_title = override.social_title
        social_description = override.social_description
        social_image = override.social_image

    if not title:
        final_title, title_source = site_name, "fallback"
    elif full_title:
        final_title = title
    else:
        final_title = compose_title(title, site_name, row.title_separator)
    sources["title"] = title_source

    final_description = truncate(description, DESCRIPTION_LIMIT)
    default_description = row.default_description or site.site_description
    if not final_description:
        final_description = truncate(
            default_description or FALLBACK_DESCRIPTION, DESCRIPTION_LIMIT
        )
        description_source = "default" if default_description else "fallback"
    sources["description"] = description_source

    use_robots_override = bool(robots_override) and index
    robots = (
        robots_override
        if use_robots_override
        else robots_directive(index=index, follow=follow)
    )
    sources["robots"] = (
        "override" if use_robots_override else ("default" if index else "policy")
    )

    image = absolute_url(
        social_image or page.image or row.default_social_image or site.og_image_url,
        request,
    )
    language = (translation.get_language() or django_settings.LANGUAGE_CODE).lower()
    og = {
        "og:type": page.og_type,
        "og:site_name": site_name,
        "og:title": social_title or final_title,
        "og:description": truncate(social_description, 200) or final_description,
        "og:locale": language.replace("-", "_"),
    }
    if canonical:
        og["og:url"] = canonical
    if image:
        og["og:image"] = image
    twitter = {
        "twitter:card": "summary_large_image" if image else "summary",
        "twitter:title": og["og:title"],
        "twitter:description": og["og:description"],
    }
    if image:
        twitter["twitter:image"] = image
    twitter_site = row.twitter_site or site.twitter_handle
    if twitter_site:
        twitter["twitter:site"] = f"@{twitter_site.lstrip('@')}"

    crumbs = tuple(
        (crumb.name, absolute_url(crumb.path, request)) for crumb in page.crumbs
    )
    nodes: list[dict[str, Any]] = []
    if index and row.seo_enabled and row.auto_schema_enabled:
        if page.page_type == "home":
            nodes.extend(_site_nodes(request, row, site_name))
        if len(crumbs) >= 2 and all(url for _name, url in crumbs):
            nodes.append(schema.breadcrumb_list(crumbs))
        nodes.extend(page.schema)

    if not canonical:
        warnings.append("canonical is empty: configure SITE_URL")
    if image and not is_absolute_http(image):
        warnings.append("social image is not absolute")

    alternates: tuple[tuple[str, str], ...] = ()
    if index and len(page.alternates) > 1:
        alternates = tuple(sorted(page.alternates.items()))

    return ResolvedSEO(
        page_type=page.page_type,
        title=final_title,
        description=final_description,
        canonical=canonical,
        robots=robots,
        indexable=index,
        noindex_reason="" if index else reason,
        language=language,
        og=og,
        twitter=twitter,
        image=image,
        alternates=alternates,
        schema=tuple(nodes),
        breadcrumbs=crumbs,
        provenance=sources,
        policy_version=POLICY_VERSION,
        warnings=tuple(warnings),
        object_ref=page.object_ref,
    )

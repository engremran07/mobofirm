"""Search step of the guide-request flow: show every related article before asking.

A member who wants a new method first enters the model and/or variant code (plus an
optional brand, chipset and keywords). :func:`find_related_posts` lists *all* live
articles that may already solve the issue (same variant, model, chipset, brand, tag or
fuzzy text) with their access level. It never reads or returns a body, so gated text
cannot leak through the list.

Only after the list is shown does the member get a signed token
(:func:`make_token`) that the request form requires, so the search step cannot be
skipped. Matching reuses the shared engine in :mod:`apps.core.search`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from django.core import signing
from django.db.models import Q, QuerySet, TextField
from django.db.models.functions import Cast

from apps.blog.models import Post
from apps.core.search.listing import search_queryset
from apps.core.search.text import clean_query, fold
from apps.firmwares.models import Brand, Chipset, Variant
from apps.firmwares.models import Model as DeviceModel

TOKEN_SALT: Final = "blog.guide-request.search"
TOKEN_MAX_AGE: Final = 6 * 3600
RESULT_LIMIT: Final = 20
CATALOGUE_LIMIT: Final = 12
POST_CANDIDATES: Final = 60

SCORE_VARIANT: Final = 100.0
SCORE_MODEL: Final = 90.0
SCORE_CODE_MENTION: Final = 85.0
SCORE_SIMILAR_MODEL: Final = 60.0
SCORE_CHIPSET: Final = 55.0
SCORE_CHIPSET_MENTION: Final = 45.0
SCORE_TAG: Final = 38.0
SCORE_KEYWORDS: Final = 35.0
SCORE_BRAND: Final = 30.0
EXTRA_REASON_BONUS: Final = 2.0
ACCESS_BADGES: Final = {"free": "Free", "featured": "Featured", "paid": "Paid"}

MODEL_FIELDS: Final = (
    "model_code",
    "codename",
    "name",
    "marketing_name",
    "also_known_as",
    "codes_text",
)
VARIANT_FIELDS: Final = ("model_code", "name", "slug", "board_id")
CHIPSET_FIELDS: Final = ("name", "vendor", "family", "aliases_text")
BRAND_FIELDS: Final = ("name", "slug")
CODE_FIELDS: Final = ("title", "summary", "tags__name")
POST_FIELDS: Final = (
    "title",
    "summary",
    "tags__name",
    "category__name",
    "firmware_model__name",
    "firmware_model__model_code",
    "firmware_model__codename",
    "firmware_brand__name",
)
_SUFFIX = re.compile(r"[\s_-]+[A-Za-z0-9]{1,3}$")
_SEPARATORS = re.compile(r"[\s_-]+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class DeviceQuery:
    """What the member typed in the search step."""

    model_code: str = ""
    variant_code: str = ""
    brand: str = ""
    chipset: str = ""
    keywords: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> DeviceQuery:
        """Read the search inputs from ``request.GET``-like data."""

        def read(name: str, limit: int) -> str:
            return clean_query(str(data.get(name, "") or ""))[:limit]

        return cls(
            model_code=read("model_code", 64),
            variant_code=read("variant_code", 64),
            brand=read("brand", 64),
            chipset=read("chipset", 64),
            keywords=read("keywords", 120),
        )

    @property
    def has_device(self) -> bool:
        """A model and/or variant code is mandatory before searching."""
        return bool(self.model_code or self.variant_code)

    def payload(self) -> dict[str, str]:
        """The query as plain strings (token payload and form prefill)."""
        return {
            "model_code": self.model_code,
            "variant_code": self.variant_code,
            "brand": self.brand,
            "chipset": self.chipset,
            "keywords": self.keywords,
        }


@dataclass(frozen=True, slots=True)
class Catalogue:
    """Catalogue rows the typed text resolved to."""

    brands: tuple[Brand, ...] = ()
    models: tuple[DeviceModel, ...] = ()
    variants: tuple[Variant, ...] = ()
    chipsets: tuple[Chipset, ...] = ()

    @property
    def model_ids(self) -> set[int]:
        """Models matched directly or through a matched variant."""
        return {m.pk for m in self.models} | {v.model_id for v in self.variants}

    @property
    def chipset_ids(self) -> set[int]:
        """Chipsets matched directly or through a matched model or variant."""
        ids = {c.pk for c in self.chipsets}
        ids |= {m.chipset_ref_id for m in self.models if m.chipset_ref_id}
        ids |= {v.chipset_ref_id for v in self.variants if v.chipset_ref_id}
        return ids

    @property
    def brand_ids(self) -> set[int]:
        """Brands matched directly or through a matched model."""
        return {b.pk for b in self.brands} | {m.brand_id for m in self.models}


@dataclass(frozen=True, slots=True)
class RelatedPost:
    """A candidate answer. It deliberately carries no body or excerpt."""

    post_id: int
    title: str
    url: str
    access: str
    device: str
    reasons: tuple[str, ...]
    score: float

    @property
    def access_label(self) -> str:
        """Short badge text: Free, Featured or Paid."""
        return ACCESS_BADGES.get(self.access, self.access)


def code_candidates(code: str, *, with_root: bool = False) -> list[str]:
    """Spelling variants of a device code: ``SM A145F`` also tries ``SM-A145F``.

    ``with_root`` adds the code without its trailing variant suffix
    (``AI2401_A`` -> ``AI2401``) so a variant code also finds its model.
    """
    base = clean_query(code)
    if not base:
        return []
    found = [base, _SEPARATORS.sub("-", base), _SEPARATORS.sub("_", base)]
    if with_root:
        root = _SUFFIX.sub("", base)
        if root and root != base:
            found.append(root)
    unique: dict[str, str] = {}
    for item in found:
        unique.setdefault(item.casefold(), item)
    return list(unique.values())


def compact(value: str) -> str:
    """Lower-case letters and digits only, for exact code comparison."""
    return _NON_ALNUM.sub("", fold(value))


def _search_ids(
    queryset: QuerySet[Any],
    candidates: Iterable[str],
    fields: tuple[str, ...],
    limit: int,
    **annotations: Any,
) -> list[int]:
    """Primary keys matching any candidate spelling, best candidate first."""
    ordered: dict[int, None] = {}
    for text in candidates:
        result = search_queryset(
            queryset, text, fields, annotations=annotations or None
        )
        for pk in result.queryset.order_by().values_list("pk", flat=True)[:limit]:
            ordered.setdefault(pk, None)
        if len(ordered) >= limit:
            break
    return list(ordered)[:limit]


def _ordered(rows: Iterable[Any], ids: list[int]) -> tuple[Any, ...]:
    by_id = {row.pk: row for row in rows}
    return tuple(by_id[pk] for pk in ids if pk in by_id)


def resolve_catalogue(query: DeviceQuery) -> Catalogue:
    """Resolve the typed codes and names to catalogue rows (typo tolerant)."""
    model_ids = _search_ids(
        DeviceModel.objects.all(),
        code_candidates(query.model_code or query.variant_code, with_root=True),
        MODEL_FIELDS,
        CATALOGUE_LIMIT,
        codes_text=Cast("model_codes", TextField()),
    )
    variant_ids = _search_ids(
        Variant.objects.all(),
        code_candidates(query.variant_code),
        VARIANT_FIELDS,
        CATALOGUE_LIMIT,
    )
    chipset_ids = _search_ids(
        Chipset.objects.all(),
        [query.chipset] if query.chipset else [],
        CHIPSET_FIELDS,
        CATALOGUE_LIMIT,
        aliases_text=Cast("aliases", TextField()),
    )
    brand_ids = _search_ids(
        Brand.objects.all(),
        [query.brand] if query.brand else [],
        BRAND_FIELDS,
        CATALOGUE_LIMIT,
    )
    return Catalogue(
        brands=_ordered(Brand.objects.filter(pk__in=brand_ids), brand_ids),
        models=_ordered(
            DeviceModel.objects.filter(pk__in=model_ids).select_related("brand"),
            model_ids,
        ),
        variants=_ordered(
            Variant.objects.filter(pk__in=variant_ids).select_related("model"),
            variant_ids,
        ),
        chipsets=_ordered(Chipset.objects.filter(pk__in=chipset_ids), chipset_ids),
    )


class _Ranking:
    """Best score per post plus the reasons that matched, strongest first."""

    def __init__(self) -> None:
        self.scores: dict[int, float] = {}
        self.reasons: dict[int, dict[str, float]] = {}

    def add(self, ids: Iterable[int], score: float, reason: str) -> None:
        """Credit ``reason`` to every post in ``ids``."""
        for pk in ids:
            self.reasons.setdefault(pk, {})[reason] = score

    def ranked(self) -> list[tuple[int, float, tuple[str, ...]]]:
        """``(post id, score, reasons)`` sorted by descending score."""
        rows: list[tuple[int, float, tuple[str, ...]]] = []
        for pk, found in self.reasons.items():
            ordered = sorted(found, key=found.__getitem__, reverse=True)
            score = found[ordered[0]] + EXTRA_REASON_BONUS * (len(ordered) - 1)
            rows.append((pk, score, tuple(ordered)))
        rows.sort(key=lambda row: (-row[1], -row[0]))
        return rows


def _live() -> QuerySet[Post]:
    return Post.objects.live().defer("body")


def _text_ids(texts: Iterable[str], fields: tuple[str, ...]) -> set[int]:
    return set(_search_ids(_live(), texts, fields, POST_CANDIDATES))


def _rank_posts(query: DeviceQuery, catalogue: Catalogue) -> _Ranking:
    ranking = _Ranking()
    live = _live()
    if query.variant_code:
        found = _text_ids(code_candidates(query.variant_code), CODE_FIELDS)
        ranking.add(found, SCORE_VARIANT, "Same variant code")
    if query.model_code:
        found = _text_ids(code_candidates(query.model_code), CODE_FIELDS)
        ranking.add(found, SCORE_CODE_MENTION, "Mentions your model code")
        loose = _text_ids([query.model_code], POST_FIELDS)
        ranking.add(loose, SCORE_SIMILAR_MODEL, "Looks like your model")
    if catalogue.model_ids:
        ids = live.filter(firmware_model_id__in=catalogue.model_ids)
        ranking.add(ids.values_list("pk", flat=True), SCORE_MODEL, "Same model")
    if catalogue.chipset_ids:
        ids = live.filter(firmware_model__chipset_ref_id__in=catalogue.chipset_ids)
        ranking.add(ids.values_list("pk", flat=True), SCORE_CHIPSET, "Same chipset")
    if query.chipset:
        ranking.add(
            _text_ids([query.chipset], CODE_FIELDS),
            SCORE_CHIPSET_MENTION,
            "Mentions the chipset",
        )
    if catalogue.brand_ids:
        brands = catalogue.brand_ids
        ids = live.filter(
            Q(firmware_brand_id__in=brands) | Q(firmware_model__brand_id__in=brands)
        )
        ranking.add(ids.values_list("pk", flat=True), SCORE_BRAND, "Same brand")
    if query.keywords:
        ranking.add(
            _text_ids([query.keywords], ("tags__name",)), SCORE_TAG, "Matching tag"
        )
        ranking.add(
            _text_ids([query.keywords], POST_FIELDS),
            SCORE_KEYWORDS,
            "Matches your keywords",
        )
    return ranking


def _device_label(post: Post) -> str:
    model = post.firmware_model
    if model is not None:
        return f"{model.brand.name} {model.name}".strip()
    if post.firmware_brand is not None:
        return post.firmware_brand.name
    return post.category.name if post.category else ""


def find_related_posts(
    query: DeviceQuery, limit: int = RESULT_LIMIT
) -> list[RelatedPost]:
    """All live articles that may already solve the issue, best match first."""
    if not query.has_device:
        return []
    ranked = _rank_posts(query, resolve_catalogue(query)).ranked()[:limit]
    posts = {
        post.pk: post
        for post in _live()
        .filter(pk__in=[row[0] for row in ranked])
        .select_related("firmware_model__brand", "firmware_brand", "category")
    }
    return [
        RelatedPost(
            post_id=pk,
            title=posts[pk].title,
            url=posts[pk].get_absolute_url(),
            access=posts[pk].access,
            device=_device_label(posts[pk]),
            reasons=reasons,
            score=score,
        )
        for pk, score, reasons in ranked
        if pk in posts
    ]


@dataclass(frozen=True, slots=True)
class Proof:
    """What a valid token proves: a completed search, or an article's request button."""

    query: DeviceQuery
    post_id: int | None = None


def make_token(
    user_id: int, query: DeviceQuery, shown: int, post_id: int | None = None
) -> str:
    """Signed proof that ``user_id`` searched ``query`` and saw ``shown`` articles.

    The article button passes ``post_id`` instead: the member is already reading
    the article that did not help.
    """
    return signing.dumps(
        {"u": user_id, "q": query.payload(), "n": shown, "p": post_id},
        salt=TOKEN_SALT,
    )


def read_token(token: str, user_id: int) -> Proof | None:
    """The proof a valid, unexpired token of this member carries; else ``None``."""
    try:
        data = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or data.get("u") != user_id:
        return None
    query = DeviceQuery.from_mapping(data.get("q") or {})
    post_id = data.get("p")
    if not isinstance(post_id, int):
        post_id = None
    if not query.has_device and post_id is None:
        return None
    return Proof(query, post_id)


def initial_for(query: DeviceQuery) -> dict[str, Any]:
    """Form prefill from a search: typed codes plus any exact catalogue match."""
    catalogue = resolve_catalogue(query)
    initial: dict[str, Any] = {
        "model_code": query.model_code,
        "variant_code": query.variant_code,
        "search_keywords": query.keywords,
    }
    typed = compact(query.model_code)
    model = next(
        (m for m in catalogue.models if typed and _model_has_code(m, typed)), None
    )
    variant = next(
        (
            v
            for v in catalogue.variants
            if query.variant_code
            and compact(v.model_code) == compact(query.variant_code)
        ),
        None,
    )
    if variant is not None and model is None:
        model = variant.model
    if model is not None:
        initial.update(model=model.pk, brand=model.brand_id)
        if model.chipset_ref_id:
            initial["chipset"] = model.chipset_ref_id
    if variant is not None:
        initial["variant_code"] = variant.model_code or query.variant_code
    if "brand" not in initial and catalogue.brands:
        initial["brand"] = catalogue.brands[0].pk
    if "chipset" not in initial and catalogue.chipsets:
        initial["chipset"] = catalogue.chipsets[0].pk
    return initial


def _model_has_code(model: DeviceModel, typed: str) -> bool:
    codes = [model.model_code, model.codename, *list(model.model_codes or [])]
    return any(compact(str(code)) == typed for code in codes if code)


def initial_from_post(post: Post) -> dict[str, Any]:
    """Prefill for the "Request a new method" button on an article."""
    initial: dict[str, Any] = {
        "related_post": post.pk,
        "title": f"Another method for: {post.title}"[:200],
    }
    model = post.firmware_model
    if model is not None:
        initial.update(model=model.pk, brand=model.brand_id)
        initial["model_code"] = model.model_code or model.name
        if model.chipset_ref_id:
            initial["chipset"] = model.chipset_ref_id
    elif post.firmware_brand_id:
        initial["brand"] = post.firmware_brand_id
    return initial

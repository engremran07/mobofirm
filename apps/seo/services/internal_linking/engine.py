from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any, cast

from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q
from django.utils.html import escape

from apps.core import ai
from apps.core.utils import feature_flags
from apps.seo.models import InterlinkExclusion, LinkableEntity, LinkSuggestion

logger = logging.getLogger(__name__)

_TAG_SPLIT_RE = re.compile(r"(<[^>]+>)")
_DEFAULT_MAX_OUTBOUND = 6
_MAX_OUTBOUND_BY_TYPE: dict[str, int] = {
    "post": 8,
    "topic": 7,
    "brand": 5,
    "model": 5,
    "officialfirmware": 4,
    "engineeringfirmware": 4,
    "readbackfirmware": 4,
    "modifiedfirmware": 4,
}


def _entity_kind(entity: LinkableEntity) -> str:
    if entity.entity_type:
        return entity.entity_type.lower().strip()
    return entity.content_type.model.lower().strip()


def _crawl_budget_limit(source: LinkableEntity, requested_limit: int) -> int:
    base = _MAX_OUTBOUND_BY_TYPE.get(_entity_kind(source), _DEFAULT_MAX_OUTBOUND)
    # Long-form sources can safely carry slightly more links without dilution.
    keyword_count = len(source.keywords or [])
    if keyword_count >= 18:
        base += 1
    return max(2, min(requested_limit, base, 10))


def _balanced_link_score(
    source: LinkableEntity,
    target: LinkableEntity,
    tfidf_score: float,
    inbound_count: int,
) -> float:
    score = tfidf_score

    if inbound_count == 0:
        score += 0.22
    elif inbound_count <= 2:
        score += 0.1
    elif inbound_count >= 20:
        score -= 0.06

    if source.content_type.pk != target.content_type.pk:
        score += 0.04

    return round(score, 6)


def render_body_with_internal_links(
    *,
    body_html: str,
    source_obj: object | None = None,
    source_entity: LinkableEntity | None = None,
    max_links: int = 8,
) -> str:
    """Inject approved internal links into body HTML text nodes only.

    This renderer never touches title/meta fields because it only operates on the
    body HTML string passed by the caller.
    """
    if not body_html or not feature_flags.seo_enabled() or max_links <= 0:
        return body_html

    if source_entity is None and source_obj is not None:
        source_pk = getattr(source_obj, "pk", None)
        if source_pk is not None:
            source_entity = LinkableEntity.objects.filter(
                content_type=ContentType.objects.get_for_model(cast(Any, source_obj)),
                object_id=str(source_pk),
                is_active=True,
            ).first()

    if source_entity is None:
        return body_html

    suggestions = list(
        LinkSuggestion.objects.filter(
            source=source_entity,
            is_applied=True,
            is_active=True,
            target__is_active=True,
        )
        .select_related("target")
        .order_by("-score")[:max_links]
    )
    if not suggestions:
        return body_html

    phrase_map: list[tuple[str, str]] = []
    for suggestion in suggestions:
        target = suggestion.target
        candidates: list[str] = []
        title = (target.title or "").strip()
        if title:
            candidates.append(title)
        for alias in target.aliases or []:
            if isinstance(alias, str) and alias.strip():
                candidates.append(alias.strip())

        for phrase in sorted(set(candidates), key=len, reverse=True):
            if len(phrase) < 4:
                continue
            if _is_excluded(source_entity.url, target.url, phrase):
                continue
            phrase_map.append((phrase, target.url))
            break

    if not phrase_map:
        return body_html

    parts = _TAG_SPLIT_RE.split(body_html)
    in_anchor = False
    links_inserted = 0
    used_phrases: set[str] = set()
    out: list[str] = []

    for part in parts:
        if not part:
            continue
        if part.startswith("<"):
            lower = part.lower()
            if lower.startswith("<a "):
                in_anchor = True
            elif lower.startswith("</a"):
                in_anchor = False
            out.append(part)
            continue

        if in_anchor or links_inserted >= max_links:
            out.append(part)
            continue

        segment = part
        for phrase, target_url in phrase_map:
            normalized = phrase.lower()
            if normalized in used_phrases:
                continue
            if links_inserted >= max_links:
                break
            pattern = re.compile(rf"(?<!\w)({re.escape(phrase)})(?!\w)", re.IGNORECASE)
            match = pattern.search(segment)
            if not match:
                continue

            anchor = (
                f'<a href="{escape(target_url)}" class="internal-link" '
                f'data-interlink="1">{escape(match.group(1))}</a>'
            )
            segment = pattern.sub(anchor, segment, count=1)
            links_inserted += 1
            used_phrases.add(normalized)

        out.append(segment)

    return "".join(out)


def refresh_linkable_entity(obj, title: str, url: str, keywords: str = ""):
    if not feature_flags.seo_enabled():
        return None
    ct = ContentType.objects.get_for_model(obj.__class__)
    defaults = {
        "title": title,
        "url": url,
        "keywords": keywords.split(",")
        if isinstance(keywords, str)
        else (keywords or []),
        "entity_type": getattr(getattr(obj, "_meta", None), "model_name", "") or "",
        "is_active": True,
    }
    # Optional embedding if enabled; a provider failure must not block the save.
    auto_linking = feature_flags.auto_linking_enabled()
    if auto_linking:
        try:
            vec = ai.embed_text(f"{title}\n{keywords}")
        except (OSError, ValueError, RuntimeError):
            logger.warning("Embedding failed for %r; saved without it.", title)
        else:
            defaults["embedding"] = vec
            defaults["vector"] = vec
    entity, _ = LinkableEntity.objects.update_or_create(
        content_type=ct,
        object_id=str(obj.pk),
        defaults=defaults,
    )

    # Keep suggestions warm when auto-linking is enabled.
    if auto_linking:
        generate_interlink_suggestions(entity, limit=10)

    return entity


def _eligible_candidates(
    source: LinkableEntity, candidates: Iterable[LinkableEntity]
) -> list[LinkableEntity]:
    seen = {source.pk}
    out: list[LinkableEntity] = []
    for c in candidates:
        if not c or c.pk in seen:
            continue
        seen.add(c.pk)
        out.append(c)
    return out


def _score_candidate(source: LinkableEntity, target: LinkableEntity) -> float:
    """
    Lightweight semantic-ish score:
    - keyword overlap (bag-of-words)
    - title overlap
    """
    src_terms = Counter(" ".join(source.keywords or []).lower().split())
    tgt_terms = Counter(" ".join(target.keywords or []).lower().split())
    overlap = sum((src_terms & tgt_terms).values())

    title_overlap = 0
    if source.title and target.title:
        src_title = set(source.title.lower().split())
        tgt_title = set(target.title.lower().split())
        title_overlap = len(src_title & tgt_title)

    return float(overlap + title_overlap * 0.5)


def tokenize(text: str) -> list[str]:
    return re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())


def classify_intent(text: str) -> str:
    lowered = text.lower()
    transactional_terms = {"buy", "price", "order", "download", "deal", "coupon"}
    navigational_terms = {"login", "homepage", "official", "site", "forum"}
    commercial_terms = {"best", "top", "compare", "review", "vs"}

    if any(term in lowered for term in transactional_terms):
        return "transactional"
    if any(term in lowered for term in navigational_terms):
        return "navigational"
    if any(term in lowered for term in commercial_terms):
        return "commercial"
    return "informational"


def tfidf_similarity(source_text: str, candidate_texts: list[str]) -> list[float]:
    source_tokens = tokenize(source_text)
    documents = [source_tokens] + [tokenize(text) for text in candidate_texts]
    total_docs = len(documents)

    df: dict[str, int] = {}
    for doc in documents:
        for token in set(doc):
            df[token] = df.get(token, 0) + 1

    idf = {
        token: math.log((total_docs + 1) / (freq + 1)) + 1.0
        for token, freq in df.items()
    }

    def to_vector(tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        size = len(tokens) or 1
        return {
            token: (count / size) * idf.get(token, 0.0)
            for token, count in counts.items()
        }

    def cosine(a: dict[str, float], b: dict[str, float]) -> float:
        common = set(a).intersection(b)
        if not common:
            return 0.0
        dot = sum(a[token] * b[token] for token in common)
        mag_a = math.sqrt(sum(v * v for v in a.values()))
        mag_b = math.sqrt(sum(v * v for v in b.values()))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)

    source_vector = to_vector(source_tokens)
    return [
        round(cosine(source_vector, to_vector(tokens)), 4) for tokens in documents[1:]
    ]


def _is_excluded(source_url: str, target_url: str, keyword: str) -> bool:
    exclusions = InterlinkExclusion.objects.filter(is_active=True)
    for exclusion in exclusions:
        phrase_match = exclusion.phrase.lower() in keyword.lower()
        source_match = (
            not exclusion.source_pattern or exclusion.source_pattern in source_url
        )
        target_match = (
            not exclusion.target_pattern or exclusion.target_pattern in target_url
        )
        if phrase_match and source_match and target_match:
            return True
    return False


def suggest_links(
    source: LinkableEntity,
    candidates: list[LinkableEntity],
    limit: int = 5,
    force: bool = False,
):
    """
    Keyword-first suggestions with stability: we do not churn locked suggestions,
    cap updates to the provided limit, and order by simple similarity scoring.
    """
    if not feature_flags.seo_enabled() or (
        not feature_flags.auto_linking_enabled() and not force
    ):
        return

    allowed = _eligible_candidates(source, candidates)
    LinkSuggestion.objects.filter(source=source, locked=False).delete()

    ranked = sorted(allowed, key=lambda t: _score_candidate(source, t), reverse=True)
    for added, target in enumerate(ranked):
        if added >= limit:
            break
        score = _score_candidate(source, target)
        LinkSuggestion.objects.update_or_create(
            source=source,
            target=target,
            defaults={"score": score, "is_applied": False, "locked": False},
        )


def generate_interlink_suggestions(source: LinkableEntity, limit: int = 10) -> int:
    effective_limit = _crawl_budget_limit(source, limit)
    candidates = list(
        LinkableEntity.objects.filter(is_active=True)
        .exclude(pk=source.pk)
        .annotate(
            inbound_count=Count(
                "suggestions_to",
                filter=Q(
                    suggestions_to__is_active=True,
                    suggestions_to__is_applied=True,
                ),
            )
        )
    )
    if not candidates:
        return 0

    source_text = f"{source.title} {' '.join(source.keywords or [])}"
    candidate_texts = [
        f"{item.title} {' '.join(item.keywords or [])}" for item in candidates
    ]
    scores = tfidf_similarity(source_text, candidate_texts)

    LinkSuggestion.objects.filter(source=source, locked=False).delete()

    created = 0
    ranked = []
    for target, score in zip(candidates, scores, strict=True):
        inbound_count = int(getattr(target, "inbound_count", 0) or 0)
        balanced_score = _balanced_link_score(source, target, score, inbound_count)
        ranked.append((target, score, balanced_score, inbound_count))

    ranked.sort(
        key=lambda row: (
            row[3] != 0,
            -row[2],
            -row[1],
        )
    )

    for target, raw_score, balanced_score, _inbound_count in ranked:
        if created >= effective_limit:
            break
        if _is_excluded(source.url, target.url, source.title):
            continue
        if raw_score < 0.08 and balanced_score < 0.14:
            continue
        LinkSuggestion.objects.update_or_create(
            source=source,
            target=target,
            defaults={
                "score": balanced_score,
                "is_applied": False,
                "locked": False,
                "is_active": True,
            },
        )
        created += 1

    # Keep at least one recommendation for discoverability unless all are excluded.
    if created == 0 and ranked:
        fallback_target, fallback_raw_score, fallback_balanced_score, _ = ranked[0]
        if not _is_excluded(source.url, fallback_target.url, source.title):
            LinkSuggestion.objects.update_or_create(
                source=source,
                target=fallback_target,
                defaults={
                    "score": max(fallback_balanced_score, fallback_raw_score),
                    "is_applied": False,
                    "locked": False,
                    "is_active": True,
                },
            )
            created = 1

    return created

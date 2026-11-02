from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from django.core.cache import cache

from apps.core import ai_client
from apps.core.utils.sanitize import sanitize_html

logger = logging.getLogger(__name__)


@dataclass
class ModerationResult:
    label: str
    score: float
    rationale: str = ""
    toxicity_score: float = 0.0
    spam_score: float = 0.0
    hate_score: float = 0.0
    cached: bool = False


def _compute_cache_key(text: str, context: str | None = None) -> str:
    """Generate cache key for moderation results."""
    content = f"{text}:{context or ''}"
    return f"comment_moderation:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


def _extract_scores(raw_text: str) -> dict[str, float]:
    """Extract numeric scores from AI response."""
    scores = {"toxicity": 0.0, "spam": 0.0, "hate": 0.0}
    if not raw_text:
        return scores

    lower = raw_text.lower()
    # Look for score patterns like "toxicity: 0.8" or "spam score: 0.3"
    import re

    for key in scores.keys():
        pattern = rf"{key}[:\s]+([0-9.]+)"
        match = re.search(pattern, lower)
        if match:
            try:
                scores[key] = min(1.0, max(0.0, float(match.group(1))))
            except ValueError:
                pass
    return scores


def classify_comment(
    text: str,
    context: str | None = None,
    use_cache: bool = True,
    cache_ttl: int = 3600,
) -> ModerationResult:
    """
    Enhanced AI moderation with caching, toxicity scoring, and comprehensive safety checks.

    Args:
        text: Comment text to classify
        context: Optional context about the comment
        use_cache: Whether to use cached results
        cache_ttl: Cache time-to-live in seconds

    Returns:
        ModerationResult with classification and scores
    """
    if not text or not text.strip():
        return ModerationResult(label="rejected", score=0.0, rationale="Empty comment")

    # Check cache first
    cache_key = _compute_cache_key(text, context)
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"Using cached moderation result for key {cache_key[:8]}")
            cached["cached"] = True
            return ModerationResult(**cached)

    # Sanitize input
    safe_text = sanitize_html(text or "", allowed_tags=["p", "br", "strong", "em"])

    # Use AI client if available
    if not hasattr(ai_client, "generate_text"):
        # No AI available, use heuristic only
        return ModerationResult(
            label="approved", score=0.5, rationale="AI moderation not available"
        )

    # Quick heuristic checks before AI call
    lower_text = safe_text.lower()
    if len(safe_text) < 3:
        result = ModerationResult(
            label="rejected", score=1.0, rationale="Comment too short"
        )
    elif any(
        spam_word in lower_text
        for spam_word in ["buy now", "click here", "free money", "limited offer"]
    ):
        result = ModerationResult(
            label="spam", score=0.9, spam_score=0.9, rationale="Spam keywords detected"
        )
    else:
        # AI classification
        try:
            # Create a dummy user for AI client (required by interface)
            from django.contrib.auth import get_user_model

            User = get_user_model()
            dummy_user = User.objects.first()  # Get any user for system calls

            if not dummy_user:
                # No users yet, use heuristic
                return ModerationResult(
                    label="approved",
                    score=0.5,
                    rationale="No AI moderation available (no users)",
                )

            # Use ai_client.moderate_text
            moderation_result = ai_client.moderate_text(safe_text, dummy_user)
            raw = moderation_result.get("analysis", "")
            toxicity_score = float(moderation_result.get("score", "0.0"))

            # Extract additional scores from raw text
            scores = _extract_scores(raw)
            if toxicity_score > 0:
                scores["toxicity"] = toxicity_score

            # Determine label based on scores
            max_score = max(scores.values())
            if max_score >= 0.8:
                label = "rejected"
            elif scores["spam"] >= 0.6:
                label = "spam"
            elif max_score >= 0.5:
                label = "pending"
            else:
                label = "approved"

            # Override with explicit label if found
            if raw:
                lower = raw.lower()
                if "label: rejected" in lower or "label: abuse" in lower:
                    label = "rejected"
                elif "label: spam" in lower:
                    label = "spam"
                elif "label: approved" in lower or "label: clean" in lower:
                    label = "approved"
                elif "label: pending" in lower:
                    label = "pending"

            result = ModerationResult(
                label=label,
                score=max_score,
                rationale=raw or "AI moderation completed",
                toxicity_score=scores["toxicity"],
                spam_score=scores["spam"],
                hate_score=scores["hate"],
            )

        except Exception as exc:
            logger.error(f"AI moderation failed: {exc}", exc_info=True)
            # Fail-safe: require manual review on errors
            result = ModerationResult(
                label="pending",
                score=0.5,
                rationale=f"Moderation unavailable: {str(exc)[:100]}",
            )

    # Cache the result
    if use_cache:
        cache_data = {
            "label": result.label,
            "score": result.score,
            "rationale": result.rationale,
            "toxicity_score": result.toxicity_score,
            "spam_score": result.spam_score,
            "hate_score": result.hate_score,
        }
        cache.set(cache_key, cache_data, cache_ttl)
        logger.debug(f"Cached moderation result for key {cache_key[:8]}")

    return result

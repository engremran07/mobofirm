from __future__ import annotations

import hashlib
import logging

from django.core.cache import cache

from apps.core import ai, ai_client

logger = logging.getLogger(__name__)

# AI Generation limits
MAX_INPUT_TOKENS = 4000
MAX_OUTPUT_TOKENS = 2000
CACHE_TTL = 3600  # 1 hour


def _truncate_text(text: str, max_tokens: int = MAX_INPUT_TOKENS) -> str:
    """Truncate text to approximately max_tokens (rough estimate: 4 chars = 1 token)."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _cache_key(prefix: str, text: str) -> str:
    """Generate cache key for AI operations."""
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    return f"ai_editor:{prefix}:{content_hash}"


def suggest_outline(text: str, use_cache: bool = True) -> str:
    """
    Generate blog post outline using AI.

    Args:
        text: Source content for outline generation
        use_cache: Whether to use cached results

    Returns:
        Outline text or empty string on error
    """
    if not text or not text.strip():
        return ""

    # Check cache
    cache_key = _cache_key("outline", text)
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            logger.debug("Using cached outline")
            return cached

    try:
        truncated = _truncate_text(text, MAX_INPUT_TOKENS)
        prompt = (
            "Draft a concise, hierarchical outline for a blog post based on this content. "
            "Use numbered sections and bullet points. Keep it under 300 words.\n\n"
            f"Content: {truncated}"
        )
        result = ai.safe_generate_text(prompt, context="blog_outline") or ""

        # Cache successful result
        if result and use_cache:
            cache.set(cache_key, result, CACHE_TTL)

        return result

    except Exception as exc:
        logger.error(f"Outline generation failed: {exc}", exc_info=True)
        return ""


def rewrite_paragraph(text: str, tone: str = "concise", use_cache: bool = True) -> str:
    """
    Rewrite paragraph with specified tone.

    Args:
        text: Original paragraph
        tone: Desired tone (concise, formal, casual, technical)
        use_cache: Whether to use cached results

    Returns:
        Rewritten text or original on error
    """
    if not text or not text.strip():
        return text

    # Validate tone
    allowed_tones = [
        "concise",
        "formal",
        "casual",
        "technical",
        "friendly",
        "professional",
    ]
    if tone not in allowed_tones:
        tone = "concise"

    # Check cache
    cache_key = _cache_key(f"rewrite_{tone}", text)
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            logger.debug("Using cached rewrite")
            return cached

    try:
        truncated = _truncate_text(text, 1000)  # Paragraphs should be shorter
        prompt = (
            f"Rewrite this paragraph in a {tone} tone. Preserve all key information and meaning. "
            "Do not add new information. Keep similar length.\n\n"
            f"Original: {truncated}"
        )
        result = ai.safe_generate_text(prompt, context="blog_rewrite") or text

        # Basic validation: result should be similar length
        if len(result) < len(text) * 0.5 or len(result) > len(text) * 2:
            logger.warning("Rewrite length mismatch, using original")
            result = text

        # Cache successful result
        if result != text and use_cache:
            cache.set(cache_key, result, CACHE_TTL)

        return result

    except Exception as exc:
        logger.error(f"Paragraph rewrite failed: {exc}", exc_info=True)
        return text


def generate_summary(text: str, max_sentences: int = 2, use_cache: bool = True) -> str:
    """
    Generate concise summary of content.

    Args:
        text: Content to summarize
        max_sentences: Maximum sentences in summary
        use_cache: Whether to use cached results

    Returns:
        Summary text or truncated original on error
    """
    if not text or not text.strip():
        return ""

    # Check cache
    cache_key = _cache_key(f"summary_{max_sentences}", text)
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            logger.debug("Using cached summary")
            return cached

    try:
        truncated = _truncate_text(text, MAX_INPUT_TOKENS)
        prompt = (
            f"Summarize this content in exactly {max_sentences} clear, concise sentence(s). "
            "Focus on main points only. No introduction or conclusion phrases.\n\n"
            f"Content: {truncated}"
        )
        result = ai.safe_generate_text(prompt, context="blog_summary") or ""

        # Fallback: create manual summary if AI fails
        if not result:
            sentences = text.split(". ")
            result = ". ".join(sentences[:max_sentences]) + "."
            result = result[:500]  # Cap length

        # Cache successful result
        if result and use_cache:
            cache.set(cache_key, result, CACHE_TTL)

        return result

    except Exception as exc:
        logger.error(f"Summary generation failed: {exc}", exc_info=True)
        # Fallback
        sentences = text.split(". ")
        return ". ".join(sentences[:max_sentences]) + "." if sentences else ""


def suggest_tags(
    text: str, max_tags: int = 10, use_cache: bool = True
) -> dict[str, list[str]]:
    """
    Generate tag suggestions using AI.

    Args:
        text: Content to analyze for tags
        max_tags: Maximum number of tags to suggest
        use_cache: Whether to use cached results

    Returns:
        Dictionary with 'suggestions' key containing list of tag names
    """
    if not text or not text.strip():
        return {"suggestions": []}

    # Check cache
    cache_key = _cache_key(f"tags_{max_tags}", text)
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            logger.debug("Using cached tags")
            return cached

    try:
        suggestions = ai_client.suggest_tags(text, None) or []

        # Limit and validate
        suggestions = [
            tag.strip()
            for tag in suggestions
            if tag and isinstance(tag, str) and len(tag.strip()) <= 50
        ][:max_tags]

        result = {"suggestions": suggestions}

        # Cache successful result
        if suggestions and use_cache:
            cache.set(cache_key, result, CACHE_TTL)

        return result

    except Exception as exc:
        logger.error(f"Tag suggestion failed: {exc}", exc_info=True)
        return {"suggestions": []}


def generate_title(text: str, max_length: int = 100, use_cache: bool = True) -> str:
    """
    Generate catchy title for blog post.

    Args:
        text: Content to generate title from
        max_length: Maximum title length
        use_cache: Whether to use cached results

    Returns:
        Generated title or empty string on error
    """
    if not text or not text.strip():
        return ""

    cache_key = _cache_key("title", text)
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            logger.debug("Using cached title")
            return cached

    try:
        truncated = _truncate_text(text, 2000)
        prompt = (
            f"Generate a catchy, SEO-friendly title (under {max_length} characters) for this content. "
            "Make it compelling and specific. Return only the title, no explanation.\n\n"
            f"Content: {truncated}"
        )
        result = ai.safe_generate_text(prompt, context="blog_title") or ""

        # Validate and truncate
        result = result.strip().strip("\"'")
        if len(result) > max_length:
            result = result[:max_length].rsplit(" ", 1)[0] + "..."

        if result and use_cache:
            cache.set(cache_key, result, CACHE_TTL)

        return result

    except Exception as exc:
        logger.error(f"Title generation failed: {exc}", exc_info=True)
        return ""

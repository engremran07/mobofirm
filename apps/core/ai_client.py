"""
apps.core.ai_client
-------------------
AI provider integration for the assistant endpoint.

- OpenAI SDK v1-style implementation with timeouts
- Explicit configuration loading and fail-fast behaviour
- Safe fallbacks and structured logging
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

logger = logging.getLogger(__name__)
User = get_user_model()


class AiClientError(RuntimeError):
    """Raised when the AI backend cannot be used safely."""


class AIRateLimiter:
    """Rate limiter for AI API calls to prevent abuse and manage costs."""

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.cache_key = "ai_rate_limit_global"
        self.user_cache_key_template = "ai_rate_limit_user_{}"

    def is_allowed(self, user_id: int | None = None) -> bool:
        """Check if request is allowed under rate limits."""
        import time

        now = time.time()
        window_start = now - 60  # 1 minute window

        # Global rate limit
        global_requests = cache.get(self.cache_key, [])
        global_requests = [req for req in global_requests if req > window_start]

        if len(global_requests) >= self.requests_per_minute:
            return False

        # Per-user rate limit (10 requests per minute per user)
        if user_id:
            user_cache_key = self.user_cache_key_template.format(user_id)
            user_requests = cache.get(user_cache_key, [])
            user_requests = [req for req in user_requests if req > window_start]

            if len(user_requests) >= 10:  # 10 requests per minute per user
                return False

            user_requests.append(now)
            cache.set(user_cache_key, user_requests, timeout=60)

        # Add to global counter
        global_requests.append(now)
        cache.set(self.cache_key, global_requests, timeout=60)
        return True


class AICircuitBreaker:
    """Circuit breaker pattern for AI service reliability."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 300):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.cache_key = "ai_circuit_breaker"

    def is_open(self) -> bool:
        """Check if circuit breaker is open."""
        import time

        if self.failure_count >= self.failure_threshold:
            if time.time() - self.last_failure_time < self.recovery_timeout:
                return True
            # Try to close circuit (half-open state)
            self.failure_count = self.failure_threshold - 1
        return False

    def record_success(self):
        """Record successful operation."""
        self.failure_count = 0
        cache.set(
            self.cache_key, {"failure_count": 0, "last_failure_time": 0}, timeout=600
        )

    def record_failure(self):
        """Record failed operation."""
        import time

        self.failure_count += 1
        self.last_failure_time = time.time()
        cache.set(
            self.cache_key,
            {
                "failure_count": self.failure_count,
                "last_failure_time": self.last_failure_time,
            },
            timeout=600,
        )

    def __enter__(self):
        if self.is_open():
            raise AiClientError("AI service circuit breaker is open")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.record_success()
        else:
            self.record_failure()
            return False  # Re-raise exception


@dataclass
class AiConfig:
    api_key: str
    model: str
    max_retries: int = 2
    retry_backoff: float = 0.75
    timeout: float = 15.0
    mock_mode: bool = False
    rate_limit_per_minute: int = 60
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: int = 300


def _load_config() -> AiConfig:
    """
    Load AI provider configuration from Django settings.
    """
    api_key = getattr(settings, "AI_OPENAI_API_KEY", "") or ""
    mock_mode = bool(getattr(settings, "AI_MOCK_MODE", False))

    if not api_key and not mock_mode:
        raise AiClientError("AI_OPENAI_API_KEY is not configured")

    model = getattr(settings, "AI_OPENAI_MODEL", "gpt-4.1-mini")
    timeout = float(getattr(settings, "AI_OPENAI_TIMEOUT", 15.0))
    max_retries = int(getattr(settings, "AI_OPENAI_MAX_RETRIES", 2))
    retry_backoff = float(getattr(settings, "AI_OPENAI_RETRY_BACKOFF", 0.75))
    rate_limit = int(getattr(settings, "AI_RATE_LIMIT_PER_MINUTE", 60))
    circuit_threshold = int(getattr(settings, "AI_CIRCUIT_BREAKER_THRESHOLD", 5))
    circuit_timeout = int(getattr(settings, "AI_CIRCUIT_BREAKER_TIMEOUT", 300))

    return AiConfig(
        api_key=api_key,
        model=model,
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        mock_mode=mock_mode,
        rate_limit_per_minute=rate_limit,
        circuit_breaker_threshold=circuit_threshold,
        circuit_breaker_timeout=circuit_timeout,
    )


def _calculate_backoff_delay(attempt: int, base_delay: float = 1.0) -> float:
    """Calculate exponential backoff with jitter to prevent thundering herd."""
    import random

    delay = base_delay * (2**attempt)
    jitter = random.uniform(0.1, 1.0) * delay * 0.1  # 10% jitter  # noqa: S311
    return min(delay + jitter, 60.0)  # Cap at 60 seconds


def _get_client(config: AiConfig):
    """Return an OpenAI client instance or raise if SDK missing."""
    if OpenAI is None:
        raise AiClientError("OpenAI SDK is not installed")
    return OpenAI(api_key=config.api_key)


def _call_openai_response(config: AiConfig, system_prompt: str, prompt: str) -> str:
    """Wrapper that selects mock mode or performs API call with retries."""
    start = time.monotonic()
    if config.mock_mode:
        return _mock_response(prompt)
    return _call_openai_with_retry(config, system_prompt, prompt, start)


def _build_system_prompt(user: Any) -> str:
    username = getattr(user, "username", "user")
    return (
        "You are the AppCore support assistant. "
        "Answer questions concisely and accurately based on general knowledge. "
        "Never reveal internal system details, secrets, or source code. "
        f"User identifier: {username}."
    )


def generate_answer(*, question: str, user: Any) -> str:
    """
    Main AI entrypoint used by apps.core.views.ai_assistant_view.

    Returns a plain text answer or raises AiClientError on config/client issues.
    """
    question = (question or "").strip()
    if not question:
        raise AiClientError("Empty question passed to generate_answer")

    config = _load_config()

    # Check for cached response (AI responses are relatively stable)
    cache_key = f"ai_response:{hash(question):x}"
    cached_response = cache.get(cache_key)
    if cached_response:
        return cached_response

    system_prompt = _build_system_prompt(user)

    # Check rate limits
    rate_limiter = AIRateLimiter(config.rate_limit_per_minute)
    if not rate_limiter.is_allowed(user.id if user and user.id else None):
        raise AiClientError("Rate limit exceeded. Please try again later.")

    # Use circuit breaker for reliability
    circuit_breaker = AICircuitBreaker(
        config.circuit_breaker_threshold, config.circuit_breaker_timeout
    )

    with circuit_breaker:
        start = time.monotonic()
        if config.mock_mode:
            response = _mock_response(question)
        else:
            response = _call_openai_with_retry(config, system_prompt, question, start)

        # Cache successful responses for 1 hour
        if response and len(response) > 10:  # Only cache meaningful responses
            cache.set(cache_key, response, timeout=3600)

        return response or "I don't have an answer for that yet."


def predict_ad_performance(features: dict[str, Any]) -> dict[str, Any]:
    """Predict ad performance (CTR, CPC, confidence, recommendations).

    This is a best-effort implementation: in mock mode it uses heuristics;
    otherwise it will attempt to call the provider with a short prompt and
    parse JSON. Failures fall back to conservative heuristics.
    """
    cfg = _load_config()

    # Simple heuristic fallback
    def _heuristic() -> dict[str, Any]:
        ctr = min(
            0.1, 0.01 + float(features.get("placement_performance_score", 0)) * 0.001
        )
        cpc = max(0.01, float(features.get("campaign_budget", 0)) * 0.0001)
        return {
            "ctr": round(ctr, 4),
            "cpc": round(cpc, 4),
            "confidence": 0.5,
            "recommendations": ["Increase bids on high-value placements"],
        }

    if cfg.mock_mode:
        return _heuristic()

    try:
        prompt = (
            "Provide a JSON object with keys ctr, cpc, confidence, recommendations given features: "
            + json.dumps(features)
        )
        system = _build_system_prompt(None)
        text = _call_openai_response(cfg, system, prompt)
        # Try to parse JSON response
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:  # noqa: S110
        pass
    return _heuristic()


def optimize_bidding_strategy(payload: dict[str, Any]) -> dict[str, Any]:
    """Return an optimized bid recommendation based on historical data.

    The payload is expected to contain keys like `campaign_id`, `historical_data`, and `budget`.
    """
    _load_config()

    # Simple heuristic optimizer
    try:
        historical = payload.get("historical_data", []) or []
        if historical:
            avg_bid = sum(float(h.get("bid", 0.0)) for h in historical) / len(
                historical
            )
        else:
            avg_bid = float(payload.get("budget", 0.0)) * 0.001

        return {
            "bid": round(max(0.01, avg_bid), 4),
            "strategy": "heuristic",
            "confidence": 0.5,
        }
    except Exception:
        return {
            "bid": float(payload.get("budget", 0.0)) * 0.001,
            "strategy": "fallback",
            "confidence": 0.3,
        }


def detect_ad_anomalies(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect anomalies in recent vs historical ad data.

    Returns a list of anomaly dicts. This implementation uses simple statistical
    heuristics and falls back to an empty list when data is insufficient.
    """
    recent = payload.get("recent_data", []) or []
    historical = payload.get("historical_data", []) or []

    anomalies: list[dict[str, Any]] = []
    try:
        # Compare simple totals for a quick heuristic
        recent_sum = sum(float(r.get("impressions", 0)) for r in recent)
        historical_sum = sum(float(h.get("impressions", 0)) for h in historical) / max(
            1, len(historical)
        )
        if historical_sum > 0 and recent_sum < historical_sum * 0.5:
            anomalies.append(
                {
                    "type": "drop_in_impressions",
                    "severity": "high",
                    "description": "Impressions dropped by >50%",
                }
            )
    except Exception:  # noqa: S110
        pass
    return anomalies


def generate_personalized_ad_content(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate personalized ad HTML using available creative template and user context.

    Falls back to simple template substitution when AI is unavailable.
    """
    cfg = _load_config()
    template = payload.get("creative_template", "")
    user_ctx = payload.get("user_context", {}) or {}

    if cfg.mock_mode or not template:
        # Simple personalization: inject user country or name tokens if present
        html = template
        if "{country}" in html:
            html = html.replace("{country}", str(user_ctx.get("country", "")))
        if "{name}" in html:
            html = html.replace("{name}", str(user_ctx.get("name", "")))
        return {"html": html, "confidence": 0.5}

    try:
        prompt = (
            "Personalize the following HTML template given user context as JSON: "
            + json.dumps({"template": template, "user_context": user_ctx})
        )
        system = _build_system_prompt(None)
        text = _call_openai_response(cfg, system, prompt)
        # Best-effort: assume returned text is the personalized html
        return {"html": text, "confidence": 0.6}
    except Exception:
        return {"html": template, "confidence": 0.5}


def predict_campaign_performance(features: dict[str, Any]) -> dict[str, Any]:
    """Predict campaign-level performance metrics (confidence, predicted_revenue, predicted_ctr)."""
    cfg = _load_config()

    if cfg.mock_mode:
        return {"confidence": 0.5, "predicted_revenue": 0.0, "predicted_ctr": 0.01}

    try:
        prompt = "Predict campaign performance as JSON given features: " + json.dumps(
            features
        )
        system = _build_system_prompt(None)
        text = _call_openai_response(cfg, system, prompt)
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:  # noqa: S110
        pass

    return {"confidence": 0.5, "predicted_revenue": 0.0, "predicted_ctr": 0.01}


# --------------------------------------------------------------------
# Action-specific helpers (generate title/excerpt/seo/tags/summarize/moderate)
# If OpenAI is unavailable or not configured, fallback to simple heuristics.
# --------------------------------------------------------------------


def _fallback_title(text: str) -> str:
    base = text.strip() or "Concise, compelling title"
    return base[:120]


def _fallback_excerpt(text: str) -> str:
    return (text.strip() or "Add a short summary.").split(".")[0][:200]


def _fallback_tags(text: str) -> list[str]:
    seeds = ["ai", "engineering", "product", "update", "announcement", "guide"]
    out = []
    text_lower = text.lower()
    for t in seeds:
        if t in text_lower and t not in out:
            out.append(t)
    return out[:5] or ["general"]


def _fallback_seo(text: str) -> str:
    return (text.strip() or "Actionable, keyword-rich description for this post.")[:150]


def _fallback_summary(text: str) -> str:
    return "Summary: " + (text.strip()[:240] or "Key points and takeaways.")


def _fallback_moderation(text: str) -> dict[str, Any]:
    lower = text.lower()
    toxic = any(w in lower for w in ["hate", "kill", "idiot"])
    score = 0.8 if toxic else 0.05
    return {"toxicity_score": score, "label": "high" if toxic else "low"}


def _call_openai_with_retry(
    config: AiConfig, system_prompt: str, question: str, start_time: float
) -> str:
    """Call OpenAI API with retry logic and metrics."""
    client = _get_client(config)

    attempts = config.max_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            response = client.responses.create(
                model=config.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                timeout=config.timeout,
            )
            text = _extract_text_response(response)
            _emit_metrics(
                ok=bool(text),
                attempt=attempt,
                elapsed_ms=round((time.monotonic() - start_time) * 1000),
            )
            return text
        except Exception as exc:  # pragma: no cover - provider edge cases
            logger.warning(
                "AI provider attempt %s/%s failed: %s", attempt, attempts, exc
            )
            if attempt >= attempts:
                logger.exception("AI provider call failed after retries")
                raise AiClientError("AI provider call failed") from exc
            # Exponential backoff with jitter
            delay = _calculate_backoff_delay(attempt, config.retry_backoff)
            time.sleep(delay)

    return ""


def generate_title(text: str, user: Any) -> str:
    try:
        config = _load_config()
        prompt = "Generate a concise, engaging blog post title:\n" + text
        system = _build_system_prompt(user)
        result = _call_openai_response(config, system, prompt)
        return result or _fallback_title(text)
    except Exception:
        return _fallback_title(text)


def generate_excerpt(text: str, user: Any) -> str:
    try:
        config = _load_config()
        prompt = "Generate a 1-2 sentence summary/excerpt for this post:\n" + text
        system = _build_system_prompt(user)
        result = _call_openai_response(config, system, prompt)
        return result or _fallback_excerpt(text)
    except Exception:
        return _fallback_excerpt(text)


def generate_seo_description(text: str, user: Any) -> str:
    try:
        config = _load_config()
        prompt = "Write an SEO meta description (max 150 chars):\n" + text
        system = _build_system_prompt(user)
        result = _call_openai_response(config, system, prompt)
        return result or _fallback_seo(text)
    except Exception:
        return _fallback_seo(text)


def suggest_tags(text: str, user: Any) -> list[str]:
    try:
        config = _load_config()
        prompt = "Suggest up to 5 comma-separated tags for this post:\n" + text
        system = _build_system_prompt(user)
        result = _call_openai_response(config, system, prompt)
        if result:
            tags = [t.strip().lower() for t in result.split(",") if t.strip()]
            return tags[:5]
    except Exception:  # noqa: S110
        pass
    return _fallback_tags(text)


def summarize_text(text: str, user: Any) -> str:
    try:
        config = _load_config()
        prompt = "Summarize the following content in 3 bullet points:\n" + text
        system = _build_system_prompt(user)
        result = _call_openai_response(config, system, prompt)
        return result or _fallback_summary(text)
    except Exception:
        return _fallback_summary(text)


def moderate_text(text: str, user: Any) -> dict[str, Any]:
    try:
        config = _load_config()
        prompt = (
            "Assess the following text for toxicity/harm. "
            "Return 'low' or 'high' risk and a numeric toxicity score 0-1."
        )
        system = _build_system_prompt(user)
        result = _call_openai_response(config, system, f"{prompt}\n{text}")
        if result:
            lower = result.lower()
            label = "high" if "high" in lower else "low"
            score = "0.8" if label == "high" else "0.05"
            return {"toxicity_score": float(score), "label": label}
    except Exception:  # noqa: S110
        pass
    return _fallback_moderation(text)


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def _extract_text_response(response) -> str:
    try:
        for output in response.output:
            for item in getattr(output, "content", []):
                if getattr(item, "type", "") == "output_text":
                    text = getattr(item, "text", "") or ""
                    return text.strip()
    except Exception:
        return ""
    return ""


def _mock_response(user_content: str) -> str:
    # Deterministic mock useful for tests and local dev without keys.
    snippet = (user_content or "").strip().split("\n")[0][:120]
    return f"[mock-ai] {snippet or 'No content provided.'}"


def _emit_metrics(*, ok: bool, attempt: int, elapsed_ms: int) -> None:
    try:
        logger.info(
            "ai.call",
            extra={
                "event": {
                    "ok": ok,
                    "attempt": attempt,
                    "elapsed_ms": elapsed_ms,
                }
            },
        )
    except Exception:
        # Metrics should never break the request flow
        return

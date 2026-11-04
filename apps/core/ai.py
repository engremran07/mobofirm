from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.core import ai_client
from apps.core.utils.logging import log_event

logger = logging.getLogger(__name__)


# Central, deterministic AI service facade.
# All apps should call these helpers so we retain a single control-plane for
# timeouts, retries, mock mode, and observability.


@dataclass(frozen=True)
class AiRequest:
    prompt: str
    context: str = ""
    constraints: dict[str, Any] | None = None
    labels: Sequence[str] | None = None

    def with_context(self) -> str:
        if not self.context:
            return self.prompt
        return f"{self.prompt}\n\nContext:\n{self.context}"


class CoreAiService:
    def __init__(self, *, mock_mode: bool | None = None) -> None:
        # Allow admin to toggle mock mode via settings to keep behaviour stable in tests/dev.
        self.mock_mode = (
            bool(mock_mode)
            if mock_mode is not None
            else bool(getattr(settings, "AI_MOCK_MODE", False))
        )

    def _measure(self, name: str, start: float, ok: bool, **extra: Any) -> None:
        log_event(
            logger,
            "info" if ok else "warning",
            f"ai.{name}",
            elapsed_ms=round((time.monotonic() - start) * 1000),
            **extra,
        )

    def generate_text(
        self,
        prompt: str,
        *,
        context: str = "",
        constraints: dict[str, Any] | None = None,
        user=None,
    ) -> str:
        req = AiRequest(prompt=prompt, context=context, constraints=constraints)
        start = time.monotonic()
        try:
            # Delegate to ai_client for provider-specific handling.
            text = ai_client.generate_answer(question=req.with_context(), user=user)
            ok = bool(text)
            self._measure("generate_text", start, ok, mock=self.mock_mode)
            return text or ""
        except Exception as exc:
            self._measure("generate_text_failed", start, False, error=str(exc))
            return ""

    def classify_text(
        self, text: str, *, labels: Sequence[str] | None = None, user=None
    ) -> str:
        labels = labels or []
        prompt = (
            f"Classify the following text into one of the labels: {', '.join(labels)}"
        )
        req = AiRequest(prompt=prompt, context=text, labels=labels)
        start = time.monotonic()
        try:
            result = ai_client.generate_answer(question=req.with_context(), user=user)
            ok = bool(result)
            self._measure(
                "classify_text", start, ok, labels=list(labels), mock=self.mock_mode
            )
            return (result or "").strip()
        except Exception as exc:
            self._measure("classify_text_failed", start, False, error=str(exc))
            return ""

    def embed_text(self, text: str) -> bytes:
        start = time.monotonic()
        # Placeholder deterministic embedding until provider wired; stable to keep tests idempotent.
        try:
            digest = text.encode("utf-8")[:256]
            self._measure(
                "embed_text", start, True, mock=self.mock_mode, length=len(digest)
            )
            return digest
        except Exception as exc:
            self._measure("embed_text_failed", start, False, error=str(exc))
            return b""


# Singleton-style service instance used across the project
core_ai = CoreAiService()


def generate_text(
    prompt: str, context: str = "", constraints: dict[str, Any] | None = None, user=None
) -> str:
    return core_ai.generate_text(
        prompt, context=context, constraints=constraints, user=user
    )


def classify_text(text: str, labels: list[str] | None = None, user=None) -> str:
    result = core_ai.classify_text(text, labels=labels or [], user=user)
    log_event(
        logger, "info", "ai.classify_text", labels=labels or [], result=result[:60]
    )
    return result


def embed_text(text: str) -> bytes:
    emb = core_ai.embed_text(text)
    log_event(logger, "info", "ai.embed_text", length=len(emb))
    return emb


# Safety-aware entrypoints -------------------------------------------------

AI_HARD_TOKEN_CAP = 2048
AI_MAX_CALLS_PER_MIN = (
    30  # simple in-memory throttle; replace with Redis/counters in prod
)
_ai_call_counts: dict[str, int] = {}


def _throttle(key: str) -> bool:
    """Return True if allowed, False if throttled."""
    from time import monotonic

    now = int(monotonic())
    bucket = now // 60
    k = f"{key}:{bucket}"
    count = _ai_call_counts.get(k, 0)
    if count >= AI_MAX_CALLS_PER_MIN:
        return False
    _ai_call_counts[k] = count + 1
    return True


def safe_generate_text(
    prompt: str, *, context: str = "", user=None, max_tokens: int = AI_HARD_TOKEN_CAP
) -> str:
    """
    Gateway for all AI generation with basic throttling and token cap.
    """
    if not _throttle(str(getattr(user, "id", "anon"))):
        log_event(logger, "warning", "ai.rate_limited", user=getattr(user, "id", None))
        return ""
    if len(prompt) + len(context) > max_tokens:
        prompt = prompt[: max_tokens // 2]
        context = context[: max_tokens // 2]
    try:
        return core_ai.generate_text(
            prompt, context=context, constraints={"max_tokens": max_tokens}, user=user
        )
    except Exception as exc:
        log_event(logger, "warning", "ai.safe_generate_text.failed", error=str(exc))
        return ""

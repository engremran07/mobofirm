"""Deterministic, rule-based comment screening.

Every comment is scored from explicit, explainable signals (links, spam phrases,
abusive terms, shouting, repetition). The score decides the *initial* status only;
staff decisions in ``decisions.py`` always override it.

Policy:
    * score < ``REVIEW_THRESHOLD``  -> ``approved`` (clean)
    * score >= ``REVIEW_THRESHOLD`` -> ``pending`` (queued for staff)
    * score >= ``SPAM_THRESHOLD``   -> ``spam`` (hidden, still visible to staff)
    * abusive wording               -> ``pending`` (a person decides; never auto-published)
    * empty                         -> ``rejected``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

REVIEW_THRESHOLD = 0.3
SPAM_THRESHOLD = 0.8

_LINK = re.compile(
    r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:ru|cn|tk|top|xyz|click|loan)\b", re.IGNORECASE
)
_REPEATED_CHARS = re.compile(r"(.)\1{9,}")
_WORD = re.compile(r"[a-z0-9']+")

SPAM_PHRASES = (
    "buy now",
    "click here",
    "free money",
    "limited offer",
    "work from home",
    "make money fast",
    "casino",
    "viagra",
    "crypto giveaway",
    "whatsapp me",
    "telegram me",
    "cheap loans",
    "seo services",
    "earn $",
)
ABUSIVE_TERMS = frozenset(
    {
        "idiot",
        "moron",
        "stupid",
        "scumbag",
        "retard",
        "kill yourself",
        "shut up",
        "piece of shit",
        "fuck",
        "fucking",
        "bitch",
        "asshole",
        "bastard",
    }
)


@dataclass(frozen=True)
class ModerationResult:
    label: str
    score: float
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def rationale(self) -> str:
        return ", ".join(self.reasons) if self.reasons else "No risk signals"


def _abusive(lower: str) -> bool:
    words = set(_WORD.findall(lower))
    return any(
        (term in lower) if " " in term else (term in words) for term in ABUSIVE_TERMS
    )


def assess_comment(
    body: str, *, max_links: int = 2, duplicate_of_recent: bool = False
) -> ModerationResult:
    """Score ``body``; ``duplicate_of_recent`` is true when the same user just sent it."""
    text = (body or "").strip()
    if not text:
        return ModerationResult("rejected", 1.0, ("empty",))

    lower = text.lower()
    reasons: list[str] = []
    score = 0.0

    links = len(_LINK.findall(text))
    if links > max_links:
        score += min(0.9, 0.35 + 0.15 * (links - max_links))
        reasons.append(f"{links} links")
    elif links:
        score += 0.1
        reasons.append("contains a link")

    phrases = [phrase for phrase in SPAM_PHRASES if phrase in lower]
    if phrases:
        score += min(0.9, 0.5 * len(phrases))
        reasons.append("spam phrase: " + phrases[0])

    if _REPEATED_CHARS.search(text):
        score += 0.4
        reasons.append("repeated characters")

    letters = [char for char in text if char.isalpha()]
    if (
        len(letters) >= 20
        and sum(char.isupper() for char in letters) / len(letters) > 0.7
    ):
        score += 0.35
        reasons.append("shouting")

    if duplicate_of_recent:
        score += 0.6
        reasons.append("duplicate of a recent comment")

    if _abusive(lower):
        return ModerationResult("pending", 0.85, (*reasons, "abusive wording"))

    score = round(min(score, 1.0), 2)
    if score >= SPAM_THRESHOLD:
        return ModerationResult("spam", score, tuple(reasons))
    if score >= REVIEW_THRESHOLD:
        return ModerationResult("pending", score, tuple(reasons))
    return ModerationResult("approved", score, tuple(reasons))

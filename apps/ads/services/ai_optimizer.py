from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def analyze_performance(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Analyze ad creative performance and generate optimization suggestions.

    This lightweight heuristic optimizer analyzes creative performance metrics
    and returns actionable suggestions for optimization (pause underperforming ads,
    boost high-performing ones, etc.). Current rules are simple heuristics;
    in production, this should be offloaded to Celery for async processing and
    persist results to database for trending analysis.

    Decision Rules:
    1. If creative has 100+ impressions AND CTR < 0.2%, suggest PAUSE
       (low click-through rate indicates poor engagement)
    2. If creative has CTR >= 1.5%, suggest BOOST
       (exceptional performance warrants higher weight/budget)

    Args:
        metrics (List[Dict[str, Any]]): List of creative performance metrics.
            Each dict should contain:
            - "creative_id" (int, required): ID of the creative being evaluated
            - "ctr" (float, required): Click-through rate as decimal (0.5 = 0.5%)
            - "impressions" (int, required): Total impressions for this creative
            - Other fields are ignored but may be present (extra: for future use)

    Returns:
        List[Dict[str, Any]]: List of optimization suggestions. Each suggestion is a dict:
            {
                "creative_id": int,  # ID of creative to act on
                "action": str,  # "pause", "boost", or other actions
                "reason": str,  # Human-readable explanation for the suggestion
            }
            Returns empty list if no creatives meet threshold criteria.

    Raises:
        No exceptions raised; invalid data is silently skipped (creative_id=None).

    Example:
        >>> metrics = [
        ...     {
        ...         "creative_id": 1,
        ...         "ctr": 0.15,  # 0.15%
        ...         "impressions": 500
        ...     },
        ...     {
        ...         "creative_id": 2,
        ...         "ctr": 2.0,  # 2.0%
        ...         "impressions": 200
        ...     },
        ...     {
        ...         "creative_id": 3,
        ...         "ctr": 0.8,
        ...         "impressions": 150
        ...     }
        ... ]
        >>> suggestions = analyze_performance(metrics)
        >>> # Returns:
        >>> # [
        >>> #     {
        >>> #         "creative_id": 1,
        >>> #         "action": "pause",
        >>> #         "reason": "Low CTR (0.15) after 500 impressions"
        >>> #     },
        >>> #     {
        >>> #         "creative_id": 2,
        >>> #         "action": "boost",
        >>> #         "reason": "High CTR (2.00); consider higher weight"
        >>> #     }
        >>> # ]

    Note:
        - Creative ID of None is skipped silently (from deleted creatives)
        - Logging at DEBUG level logs all suggestions for audit trail
        - Consider moving to async task + persistence for production workloads
        - Thresholds (100 impressions, 0.2% CTR, 1.5% CTR) are tunable parameters
          that should eventually be moved to AdsSettings
    """
    suggestions = []
    for row in metrics:
        ctr = row.get("ctr", 0)
        impressions = row.get("impressions", 0)
        creative_id = row.get("creative_id")
        if creative_id is None:
            continue
        if impressions >= 100 and ctr < 0.2:
            suggestions.append(
                {
                    "creative_id": creative_id,
                    "action": "pause",
                    "reason": f"Low CTR ({ctr:.2f}) after {impressions} impressions",
                }
            )
        elif ctr >= 1.5:
            suggestions.append(
                {
                    "creative_id": creative_id,
                    "action": "boost",
                    "reason": f"High CTR ({ctr:.2f}); consider higher weight",
                }
            )
    logger.debug("AI optimizer suggestions=%s", suggestions)
    return suggestions

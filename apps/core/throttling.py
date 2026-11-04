"""
Custom Throttling Classes for API Rate Limiting
Enterprise-grade rate limiting with different tiers
"""

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class UploadRateThrottle(UserRateThrottle):
    """
    Rate limit for firmware uploads.
    Prevents abuse while allowing legitimate uploads.
    """

    rate = "10/hour"
    scope = "upload"


class DownloadRateThrottle(UserRateThrottle):
    """
    Rate limit for firmware downloads.
    Generous limit for normal usage.
    """

    rate = "50/hour"
    scope = "download"


class APIRateThrottle(UserRateThrottle):
    """
    General API rate limit for authenticated users.
    """

    rate = "1000/hour"
    scope = "api"


class StrictAnonRateThrottle(AnonRateThrottle):
    """
    Strict rate limit for anonymous users.
    Prevents scraping and abuse.
    """

    rate = "100/hour"
    scope = "anon_strict"


class SearchRateThrottle(UserRateThrottle):
    """
    Rate limit for search endpoints.
    Prevents search abuse.
    """

    rate = "200/hour"
    scope = "search"


class AIRateThrottle(UserRateThrottle):
    """
    Rate limit for AI-powered endpoints.
    More restrictive due to computational cost.
    """

    rate = "20/hour"
    scope = "ai"

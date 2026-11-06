import logging

from django.core.cache import cache
from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@never_cache
@require_safe
def liveness(_request):
    return JsonResponse({"status": "ok"})


@never_cache
@require_safe
def readiness(_request):
    """Report readiness without exposing which dependency failed or why."""
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.exception("Readiness check failed: database unavailable")
        return JsonResponse({"status": "unavailable"}, status=503)
    try:
        cache.set("health:ready", "1", timeout=10)
        cache_ok = cache.get("health:ready") == "1"
    except (RedisError, OSError):
        logger.exception("Readiness check failed: cache unavailable")
        cache_ok = False
    if not cache_ok:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})

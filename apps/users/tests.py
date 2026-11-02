from __future__ import annotations

from types import SimpleNamespace

from django.core.cache import cache
from django.test import TestCase

from apps.comments.views import _check_comment_rate_limit
from apps.users.services.rate_limit import (
    allow_action,
    reset_rate_limit,
)


class RateLimitServiceTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_allow_action_enforces_limits(self):
        key = "tests:rate_limit"
        for _ in range(5):
            assert allow_action(key, max_attempts=5, window_seconds=60)
        assert not allow_action(key, max_attempts=5, window_seconds=60)

    def test_reset_rate_limit_allows_new_attempts(self):
        key = "tests:rate_limit_reset"
        for _ in range(5):
            allow_action(key, max_attempts=5, window_seconds=60)
        assert not allow_action(key, max_attempts=5, window_seconds=60)
        reset_rate_limit(key)
        assert allow_action(key, max_attempts=5, window_seconds=60)


class CommentRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def _build_request(self, user_id: int | None = None):
        user = SimpleNamespace(pk=user_id or 0, is_authenticated=bool(user_id))
        return SimpleNamespace(
            user=user,
            META={"REMOTE_ADDR": "198.51.100.42"},
        )

    def test_comment_rate_limit_enforced(self):
        request = self._build_request(user_id=42)
        for _ in range(10):
            assert _check_comment_rate_limit(request)  # type: ignore[arg-type]
        assert not _check_comment_rate_limit(request)  # type: ignore[arg-type]

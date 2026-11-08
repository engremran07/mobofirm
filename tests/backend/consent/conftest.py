"""Shared fixtures for the consent app tests."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client


@pytest.fixture(autouse=True)
def fresh_policy_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client() -> Client:
    return Client()

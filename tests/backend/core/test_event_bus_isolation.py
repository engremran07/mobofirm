"""A subscriber that raises a domain error never reaches the event publisher."""

from __future__ import annotations

import pytest

from apps.core.events.bus import DomainError, EventBus
from apps.promotions.grants import ClaimError
from apps.wallet.services import WalletError


class Unexpected(Exception):
    pass


@pytest.mark.parametrize("error", [DomainError, WalletError, ClaimError])
def test_domain_errors_are_isolated_from_the_publisher(error):
    bus = EventBus()
    seen: list[str] = []

    def broken(data):
        raise error("refused")

    def healthy(data):
        seen.append("ran")

    bus.subscribe("sample.event", broken)
    bus.subscribe("sample.event", healthy)

    bus.publish("sample.event", {})

    assert seen == ["ran"]


def test_unexpected_errors_still_surface():
    bus = EventBus()

    def broken(data):
        raise Unexpected("bug")

    bus.subscribe("sample.event", broken)

    with pytest.raises(Unexpected):
        bus.publish("sample.event", {})

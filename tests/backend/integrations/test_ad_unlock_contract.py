"""Contracts between shop (unlock tickets), ads (rewarded watches) and firmwares (the gate)."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from django.contrib.auth.base_user import AbstractBaseUser

from apps.ads import rewarded
from apps.ads.models import AdPlacement, AdsSettings, RewardedAdConfig
from apps.core.app_service import AppService
from apps.firmwares import access
from apps.firmwares.models import BaseFirmware
from apps.shop import ad_unlock, entitlements

pytestmark = pytest.mark.django_db


@pytest.fixture
def unlock_config() -> RewardedAdConfig:
    row = AdsSettings.get_solo()
    row.ads_enabled = True
    row.rewarded_ads_enabled = True
    row.save()
    placement = AdPlacement.objects.create(name="Slot", slug="slot", code="slot")
    return RewardedAdConfig.objects.create(
        name="Download unlock",
        placement=placement,
        is_enabled=True,
        reward_type=RewardedAdConfig.RewardType.DOWNLOAD_UNLOCK,
    )


def test_the_shop_reads_the_unlock_ad_only_through_the_ads_contract(unlock_config):
    offer = AppService.export("ads", "rewarded_offer")

    assert callable(offer)
    result = offer("download_unlock")
    assert result["enabled"] is True and result["reward_type"] == "download_unlock"


def test_the_shop_never_imports_the_ads_app():
    for name in ("ad_unlock.py", "unlock_models.py", "entitlements.py", "views.py"):
        source = Path(inspect.getfile(ad_unlock)).with_name(name).read_text("utf-8")
        assert not re.search(r"^\s*(from|import)\s+apps\.ads", source, re.MULTILINE), (
            name
        )


def test_the_unlock_reward_is_unavailable_until_the_shop_plugs_in(
    unlock_config, monkeypatch
):
    monkeypatch.setattr(rewarded, "_handlers", {})

    assert rewarded.unavailable_reason(unlock_config) == (
        "File unlocks are not available on this site."
    )
    assert rewarded.available_config("download_unlock") is None


def test_the_bridge_registers_the_shop_rules_at_startup(unlock_config):
    handler = rewarded._handlers[RewardedAdConfig.RewardType.DOWNLOAD_UNLOCK]

    assert rewarded.unavailable_reason(unlock_config) == ""
    assert callable(handler.check) and callable(handler.grant)


def _authenticated_user() -> AbstractBaseUser:
    """A stand-in for a signed-in account (the gate only reads ``is_authenticated``)."""
    return cast("AbstractBaseUser", SimpleNamespace(is_authenticated=True))


def _firmware(label: str) -> BaseFirmware:
    """A stand-in firmware: the fake shop module only records what it receives."""
    return cast("BaseFirmware", label)


def test_the_gate_asks_the_shop_through_the_optional_module(monkeypatch):
    seen: list[BaseFirmware] = []

    def record(user: object, firmware: BaseFirmware) -> bool:
        seen.append(firmware)
        return True

    fake = SimpleNamespace(has_ad_unlock=record)
    firmware = _firmware("firmware")
    monkeypatch.setattr(
        access,
        "optional_module",
        lambda name: fake if name == access.ENTITLEMENTS_MODULE else None,
    )

    assert access.has_ad_unlock(_authenticated_user(), firmware) is True
    assert seen == [firmware]


def test_without_the_shop_nobody_holds_an_unlock(monkeypatch):
    monkeypatch.setattr(access, "optional_module", lambda name: None)

    assert access.has_ad_unlock(_authenticated_user(), _firmware("fw")) is False
    assert access.has_ad_unlock(None, _firmware("fw")) is False


def test_a_shop_module_without_the_contract_is_tolerated(monkeypatch):
    monkeypatch.setattr(access, "optional_module", lambda name: SimpleNamespace())

    assert access.has_ad_unlock(_authenticated_user(), _firmware("fw")) is False


def test_the_entitlement_module_exports_the_unlock_hook():
    assert callable(entitlements.has_ad_unlock)
    assert callable(entitlements.file_type_key)


def test_the_unlock_reward_needs_no_amount_range(unlock_config):
    unlock_config.reward_amount = 1
    unlock_config.full_clean(exclude=["placement", "network", "ad_unit"])

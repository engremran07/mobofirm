import pytest


@pytest.fixture(autouse=True)
def no_external_autofill(monkeypatch):
    """Creating catalogue records must never call GSMArena or the AI provider."""
    for method in ("autofill_brand", "autofill_model", "autofill_variant"):
        monkeypatch.setattr(
            f"apps.firmwares.admin_autofill.FirmwareAutoFill.{method}",
            lambda *args, **kwargs: {},
        )

"""Backend test fixtures shared by every test module."""

import pytest

from apps.site_settings.services import clear_site_settings_cache


@pytest.fixture(autouse=True)
def fresh_site_settings_cache():
    """Never carry a site-settings snapshot across tests.

    Database rollbacks do not fire signals, so the cache is cleared explicitly.
    """
    clear_site_settings_cache()
    yield
    clear_site_settings_cache()

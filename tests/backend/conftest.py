"""Backend test fixtures shared by every test module."""

import pytest

from apps.site_settings.editor_models import EditorProfile
from apps.site_settings.editor_store import clear_profile_cache
from apps.site_settings.services import clear_site_settings_cache


@pytest.fixture(autouse=True)
def fresh_site_settings_cache():
    """Never carry a site-settings snapshot across tests.

    Database rollbacks do not fire signals, so the cache is cleared explicitly.
    """
    clear_site_settings_cache()
    clear_profile_cache()
    yield
    clear_site_settings_cache()
    clear_profile_cache()


@pytest.fixture
def seeded_profiles(db):
    """The editor profile rows that the data migration creates (not run here)."""
    del db
    EditorProfile.seed()

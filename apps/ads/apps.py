from importlib import import_module

from django.apps import AppConfig


class AdsConfig(AppConfig):
    name = "apps.ads"
    verbose_name = "Ads & Monetization"

    def ready(self) -> None:
        import_module("apps.ads.signals")

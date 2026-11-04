from django.apps import AppConfig


class AdsConfig(AppConfig):
    name = "apps.ads"
    verbose_name = "Ads & Monetization"

    def ready(self):
        # Import signals if present
        try:
            import apps.ads.signals  # noqa: F401
        except Exception:
            return

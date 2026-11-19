from importlib import import_module

from django.apps import AppConfig, apps


class AdsConfig(AppConfig):
    name = "apps.ads"
    verbose_name = "Ads & Monetization"

    def ready(self) -> None:
        import_module("apps.ads.signals")
        if apps.is_installed("apps.shop"):
            from apps.ads import unlock_bridge
            from apps.ads.rewarded import entitlement_modifier
            from apps.shop.entitlements import register_entitlement_modifier

            # Live rewarded-ad grants (extra downloads, ad-free) feed the one resolver.
            register_entitlement_modifier(entitlement_modifier)
            # Watches of the "download unlock" reward count toward shop unlock tickets.
            unlock_bridge.register()

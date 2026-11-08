from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.module_loading import autodiscover_modules


class WalletConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.wallet"
    label = "wallet"
    verbose_name = "Wallet"

    def ready(self) -> None:
        from apps.core.events.bus import event_bus

        from .receivers import sync_economy_rows
        from .services import SHOP_ORDER_PAID_EVENT, handle_shop_order_paid

        # Every app declares what it pays and charges in these modules.
        autodiscover_modules("reward_events", "spend_sinks")
        event_bus.subscribe(SHOP_ORDER_PAID_EVENT, handle_shop_order_paid)
        post_migrate.connect(
            sync_economy_rows, dispatch_uid="wallet_sync_economy_rows_v1"
        )

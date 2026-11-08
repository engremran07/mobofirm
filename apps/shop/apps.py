from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_save


class ShopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.shop"
    label = "shop"
    verbose_name = "Shop"

    def ready(self) -> None:
        from .receivers import assign_free_package

        post_save.connect(
            assign_free_package,
            sender=settings.AUTH_USER_MODEL,
            dispatch_uid="shop_assign_free_package_v1",
        )

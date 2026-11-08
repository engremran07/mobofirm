from django.apps import AppConfig


class ReferralConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.referral"
    label = "referral"
    verbose_name = "Referral"

    def ready(self) -> None:
        from .receivers import connect_receivers

        connect_receivers()

"""Order integrity: an index for the per-user status lookups and two constraints.

Additive only (no data is rewritten): ``total`` is never negative, and a credits payment
key identifies exactly one order, so a repeated purchase request cannot record a second.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = (("shop", "0005_ad_unlocks"),)

    operations = (
        migrations.AddIndex(
            model_name="order",
            index=models.Index(
                fields=["user", "status"], name="shop_order_user_status_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.CheckConstraint(
                condition=models.Q(("total__gte", 0)),
                name="shop_order_total_not_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.UniqueConstraint(
                condition=models.Q(("payment_method", "credits")),
                fields=("payment_reference",),
                name="shop_one_credit_order_per_key",
                violation_error_message="This credits payment was already recorded.",
            ),
        ),
    )

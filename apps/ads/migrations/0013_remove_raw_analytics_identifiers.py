"""Drop raw visitor identifiers from Ads analytics tables.

The removed values were not needed by any current analytics or abuse-control flow.
This migration is intentionally irreversible so rolling back cannot restore code that
stores raw IP addresses, User-Agent strings, or session keys.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = (("ads", "0012_encrypt_ads_credentials"),)

    operations = (
        migrations.RemoveField(
            model_name="affiliateclick",
            name="ip_address",
        ),
        migrations.RemoveField(
            model_name="affiliateclick",
            name="user_agent",
        ),
        migrations.RemoveField(
            model_name="affiliateclick",
            name="session_id",
        ),
        migrations.RemoveField(
            model_name="rewardedadview",
            name="ip_address",
        ),
        migrations.RemoveField(
            model_name="rewardedadview",
            name="user_agent",
        ),
        migrations.RemoveField(
            model_name="adevent",
            name="user_agent",
        ),
        migrations.RemoveField(
            model_name="adevent",
            name="session_id",
        ),
        migrations.RunPython(migrations.RunPython.noop),
    )

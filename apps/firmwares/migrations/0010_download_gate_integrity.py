"""Download gate integrity: bound tokens, real transfer leases, protected history.

Additive only (new defaulted or nullable columns, one index) plus three ``on_delete``
changes that Django enforces in Python, so no data is rewritten or deleted:

* ``DownloadToken.anon_hash`` replaces the client IP as the identity of an anonymous
  token (a keyed hash of a signed cookie);
* ``DownloadSession`` gains the lease columns the concurrent-download cap counts, and
  ``FirmwareDownloadAttempt.session`` links an attempt to its lease;
* ``AdGateLog.proof`` states how strong the evidence of a gate step is;
* access-change history no longer cascades from a content-type cleanup, and deleting
  a tool category no longer deletes its tools.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = (
        ("contenttypes", "0002_remove_content_type_name"),
        ("firmwares", "0009_tool_category_kinds"),
    )

    operations = (
        migrations.AddField(
            model_name="downloadtoken",
            name="anon_hash",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text=(
                    "Keyed hash of the signed download cookie that owns an anonymous "
                    "token (empty for signed-in users). The IP never identifies a token."
                ),
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="downloadsession",
            name="redeem_count",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="How often the signed link was opened (resume budget)",
            ),
        ),
        migrations.AddField(
            model_name="downloadsession",
            name="redeemed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="downloadsession",
            name="lease_expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text="The transfer slot is free after this moment, whatever happens",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="downloadsession",
            name="released_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the transfer slot was given back",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="downloadsession",
            name="bytes_delivered",
            field=models.BigIntegerField(
                default=0,
                help_text="Bytes reported delivered (0 until a transfer reports)",
            ),
        ),
        migrations.AddIndex(
            model_name="downloadsession",
            index=models.Index(
                fields=["user", "released_at", "lease_expires_at"],
                name="dl_session_lease_idx",
            ),
        ),
        migrations.AddField(
            model_name="adgatelog",
            name="proof",
            field=models.CharField(
                choices=[
                    ("dwell", "Server timer (delay only)"),
                    ("ad_unlock", "Rewarded-ad unlock (server verified)"),
                ],
                db_index=True,
                default="dwell",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="firmwaredownloadattempt",
            name="session",
            field=models.ForeignKey(
                blank=True,
                help_text="The download session (transfer lease) this attempt belongs to",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attempts",
                to="firmwares.downloadsession",
            ),
        ),
        migrations.AlterField(
            model_name="accesschangelog",
            name="content_type",
            field=models.ForeignKey(
                help_text="History must outlive a content-type cleanup, so it is protected",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="contenttypes.contenttype",
            ),
        ),
        migrations.AlterField(
            model_name="accesschangerequest",
            name="content_type",
            field=models.ForeignKey(
                help_text="History must outlive a content-type cleanup, so it is protected",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="contenttypes.contenttype",
            ),
        ),
        migrations.AlterField(
            model_name="flashingtool",
            name="category",
            field=models.ForeignKey(
                help_text="Deleting a category never deletes its tools: move them first.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tools",
                to="firmwares.flashingtoolcategory",
            ),
        ),
    )

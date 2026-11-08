import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def accept_applied_suggestions(apps, _schema_editor):
    """Suggestions that were already rendered keep rendering: they count as accepted."""
    apps.get_model("seo", "LinkSuggestion").objects.filter(is_applied=True).update(
        status="accepted"
    )


class Migration(migrations.Migration):
    dependencies = (
        ("seo", "0003_scan_inventory_and_issue_review"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    )

    operations = (
        migrations.AddField(
            model_name="linksuggestion",
            name="anchor",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="linksuggestion",
            name="decided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="linksuggestion",
            name="decided_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="linksuggestion",
            name="reason",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="linksuggestion",
            name="relation",
            field=models.CharField(
                blank=True,
                default="",
                help_text="The real relation that justifies the link (e.g. model_to_chipset).",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="linksuggestion",
            name="status",
            field=models.CharField(
                choices=[
                    ("proposed", "Proposed"),
                    ("accepted", "Accepted"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="proposed",
                max_length=10,
            ),
        ),
        migrations.RunPython(accept_applied_suggestions, migrations.RunPython.noop),
    )

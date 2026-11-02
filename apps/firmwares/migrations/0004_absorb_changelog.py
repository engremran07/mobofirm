# Generated migration — absorbs apps.changelog models into apps.firmwares.
# Tables already exist (created by changelog 0001_initial); we only update
# Django's migration state so the ORM knows these models live here now.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "firmwares",
            "0003_hotlinkblock_syncrun_downloadsession_adgatelog_and_more",
        ),
        # Note: changelog/0001_initial already ran (tables exist in DB).
        # That app is dissolved — no longer in INSTALLED_APPS.
        # SeparateDatabaseAndState below does zero DB work; tables are preserved.
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # No database operations — tables already exist.
            database_operations=[],
            # State operations register the models in the firmwares app.
            state_operations=[
                migrations.CreateModel(
                    name="ChangelogEntry",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("version", models.CharField(max_length=30, unique=True)),
                        ("title", models.CharField(max_length=200)),
                        ("summary", models.TextField(blank=True, default="")),
                        (
                            "changes",
                            models.JSONField(
                                blank=True,
                                default=list,
                                help_text="Structured list of change objects {type, description}",
                            ),
                        ),
                        ("release_date", models.DateField(db_index=True)),
                        (
                            "is_published",
                            models.BooleanField(db_index=True, default=False),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        "verbose_name": "Changelog Entry",
                        "verbose_name_plural": "Changelog Entries",
                        "ordering": ["-release_date"],
                        "db_table": "changelog_changelogentry",
                    },
                ),
                migrations.CreateModel(
                    name="ReleaseNote",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "category",
                            models.CharField(
                                choices=[
                                    ("feature", "New Feature"),
                                    ("improvement", "Improvement"),
                                    ("bug_fix", "Bug Fix"),
                                    ("security", "Security"),
                                    ("breaking", "Breaking Change"),
                                    ("deprecated", "Deprecation"),
                                ],
                                max_length=15,
                            ),
                        ),
                        ("description", models.TextField()),
                        ("is_breaking", models.BooleanField(default=False)),
                        (
                            "entry",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="release_notes",
                                to="firmwares.changelogentry",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Release Note",
                        "verbose_name_plural": "Release Notes",
                        "db_table": "changelog_releasenote",
                    },
                ),
                migrations.CreateModel(
                    name="FirmwareDiff",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("diff_html", models.TextField(blank=True, default="")),
                        ("diff_text", models.TextField(blank=True, default="")),
                        ("generated_at", models.DateTimeField(auto_now_add=True)),
                        (
                            "new_firmware",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="diffs_as_new",
                                to="firmwares.officialfirmware",
                            ),
                        ),
                        (
                            "old_firmware",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="diffs_as_old",
                                to="firmwares.officialfirmware",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Firmware Diff",
                        "verbose_name_plural": "Firmware Diffs",
                        "db_table": "changelog_firmwarediff",
                        "unique_together": {("old_firmware", "new_firmware")},
                    },
                ),
            ],
        ),
    ]

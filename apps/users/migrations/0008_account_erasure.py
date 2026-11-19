from typing import ClassVar

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Account erasure: the tombstone/audit table, the user marker and its permission.

    Additive only: one nullable column, one new table, one permission. No data is
    rewritten or deleted.
    """

    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("users", "0007_announcement_cta"),
    ]

    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.AlterModelOptions(
            name="customuser",
            options={
                "ordering": ["-date_joined"],
                "permissions": (
                    ("anonymize_account", "Can anonymise (erase) a user account"),
                ),
                "verbose_name": "User",
                "verbose_name_plural": "Users",
            },
        ),
        migrations.AddField(
            model_name="customuser",
            name="anonymized_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                editable=False,
                help_text="Set when the account was anonymised in place (account erasure).",
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="AccountErasure",
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
                    "user_pk",
                    models.PositiveBigIntegerField(
                        db_index=True,
                        help_text="Primary key of the account (stays in ledgers).",
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("self_service", "Requested by the account holder"),
                            ("staff", "Performed by staff"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("awaiting_confirmation", "Awaiting email confirmation"),
                            ("scheduled", "Scheduled (grace period)"),
                            ("cancelled", "Cancelled"),
                            ("completed", "Completed"),
                            ("partial", "Completed, apps without a handler remain"),
                            ("failed", "Failed (needs staff)"),
                        ],
                        db_index=True,
                        default="awaiting_confirmation",
                        max_length=24,
                    ),
                ),
                (
                    "reason",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Why staff erased the account. Do not put personal data here.",
                        max_length=1000,
                    ),
                ),
                ("token_hash", models.CharField(blank=True, default="", max_length=64)),
                ("token_expires_at", models.DateTimeField(blank=True, null=True)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("scheduled_for", models.DateTimeField(blank=True, null=True)),
                ("executed_at", models.DateTimeField(blank=True, null=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "failure_reason",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                (
                    "receipt",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Per-app counts reported by each anonymiser, and apps without one.",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        help_text="The account; cleared only if the row is hard-deleted afterwards.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="erasure_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Account erasure",
                "verbose_name_plural": "Account erasures",
                "ordering": ["-requested_at"],
                "indexes": [
                    models.Index(
                        fields=["status", "scheduled_for"], name="users_erasure_due_idx"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("status__in", ["awaiting_confirmation", "scheduled"])
                        ),
                        fields=("user_pk",),
                        name="users_erasure_one_open_per_account",
                    )
                ],
            },
        ),
    ]

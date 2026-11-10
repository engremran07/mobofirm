from typing import ClassVar

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("core", "0007_appregistry_promotions_enabled"),
    ]

    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.CreateModel(
            name="SearchSettings",
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
                    "popular_enabled",
                    models.BooleanField(
                        default=True,
                        help_text="Show 'Popular models', 'Popular firmware' and 'Popular articles'.",
                    ),
                ),
                (
                    "popular_window_days",
                    models.PositiveSmallIntegerField(
                        default=30,
                        help_text="Downloads and views of the last N days decide what is popular.",
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(365),
                        ],
                    ),
                ),
                (
                    "popular_limit",
                    models.PositiveSmallIntegerField(
                        default=6,
                        help_text="How many entries each popular block lists.",
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(24),
                        ],
                    ),
                ),
                (
                    "popular_cache_minutes",
                    models.PositiveSmallIntegerField(
                        default=10,
                        help_text="How long a computed popular list is reused before recomputing.",
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(1440),
                        ],
                    ),
                ),
            ],
            options={
                "verbose_name": "Search and popularity settings",
                "verbose_name_plural": "Search and popularity settings",
            },
        ),
    ]

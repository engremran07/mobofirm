"""
Management command to regenerate blog posts for all models with firmware
Useful for bulk generation or refreshing existing posts
"""

from django.core.management.base import BaseCommand

from apps.firmwares.blog_automation import FirmwareBlogService
from apps.firmwares.models import (
    EngineeringFirmware,
    Model,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    ReadbackFirmware,
)


class Command(BaseCommand):
    help = "Generate or update blog posts for all models with firmware"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force regeneration of all posts, even if they exist",
        )
        parser.add_argument(
            "--model-slug",
            type=str,
            help="Generate blog post for specific model slug only",
        )
        parser.add_argument(
            "--async",
            action="store_true",
            dest="run_async",
            help="Queue as Celery task instead of running synchronously",
        )

    def handle(self, *args, **options):
        force_update = options.get("force", False)
        model_slug = options.get("model_slug")
        run_async = options.get("run_async", False)

        if run_async:
            from apps.firmwares.tasks import generate_all_firmware_blogs

            task = generate_all_firmware_blogs.delay(force_update=force_update)
            self.stdout.write(
                self.style.SUCCESS(f"Queued blog generation task: {task.id}")
            )
            return

        self.stdout.write(self.style.SUCCESS("Starting blog post generation..."))

        # Get models with firmware
        if model_slug:
            models = Model.objects.filter(slug=model_slug)
        else:
            models = Model.objects.all().select_related("brand")

        total_models = models.count()
        self.stdout.write(f"Found {total_models} models to process")

        success_count = 0
        skip_count = 0
        error_count = 0

        for model in models:
            try:
                # Check if model has any firmware (directly linked to model, not via variant)
                has_firmware = (
                    OfficialFirmware.objects.filter(
                        model=model, is_active=True
                    ).exists()
                    or EngineeringFirmware.objects.filter(
                        model=model, is_active=True
                    ).exists()
                    or ReadbackFirmware.objects.filter(
                        model=model, is_active=True
                    ).exists()
                    or ModifiedFirmware.objects.filter(
                        model=model, is_active=True
                    ).exists()
                    or OtherFirmware.objects.filter(
                        model=model, is_active=True
                    ).exists()
                )

                if not has_firmware and not force_update:
                    skip_count += 1
                    continue

                # Generate blog post
                post = FirmwareBlogService.generate_firmware_post(
                    model, force_update=force_update
                )

                if post:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Generated: {model.brand.name} {model.name}"
                        )
                    )
                    success_count += 1
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  - Skipped: {model.brand.name} {model.name}"
                        )
                    )
                    skip_count += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f"  ✗ Error: {model.brand.name} {model.name} - {e!s}"
                    )
                )
                error_count += 1

        # Summary
        self.stdout.write(self.style.SUCCESS("\n" + "=" * 60))
        self.stdout.write(self.style.SUCCESS("Blog Post Generation Complete"))
        self.stdout.write(self.style.SUCCESS(f"Total Models: {total_models}"))
        self.stdout.write(self.style.SUCCESS(f"Posts Generated: {success_count}"))
        self.stdout.write(self.style.WARNING(f"Skipped: {skip_count}"))
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f"Errors: {error_count}"))
        self.stdout.write(self.style.SUCCESS("=" * 60))

"""Catch-up sync of the automated brand, model and chipset blog posts."""

from django.core.management.base import BaseCommand, CommandError

from apps.firmwares.blog_automation import FirmwareBlogService
from apps.firmwares.models import Brand, Chipset, Model


class Command(BaseCommand):
    help = (
        "Create or refresh the single automated blog post for every brand, model "
        "and chipset. Idempotent: unchanged posts are left untouched."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--brand", help="Only sync this brand slug (and its models)."
        )
        parser.add_argument("--model", help="Only sync this model slug.")
        parser.add_argument("--chipset", help="Only sync this chipset slug.")
        parser.add_argument(
            "--async",
            action="store_true",
            dest="run_async",
            help="Queue the catch-up as a Celery task instead of running it inline.",
        )

    def handle(self, *args, **options):
        if options["run_async"]:
            from apps.firmwares.tasks import catchup_missing_blog_posts

            task = catchup_missing_blog_posts.delay(brand_slug=options["brand"])
            self.stdout.write(
                self.style.SUCCESS(f"Queued blog catch-up task {task.id}")
            )
            return

        if options["model"]:
            models = Model.objects.filter(slug=options["model"])
            if options["brand"]:
                models = models.filter(brand__slug=options["brand"])
            if not models.exists():
                raise CommandError("No model matches the given slug.")
            for model in models:
                post = FirmwareBlogService.sync_model_post(model)
                self.stdout.write(f"model {model}: post #{post.pk}")
            return

        if options["chipset"]:
            chipset = Chipset.objects.filter(slug=options["chipset"]).first()
            if chipset is None:
                raise CommandError("No chipset matches the given slug.")
            post = FirmwareBlogService.sync_chipset_post(chipset)
            self.stdout.write(f"chipset {chipset}: post #{post.pk}")
            return

        if (
            options["brand"]
            and not Brand.objects.filter(slug=options["brand"]).exists()
        ):
            raise CommandError("No brand matches the given slug.")
        counts = FirmwareBlogService.sync_all(brand_slug=options["brand"])
        summary = ", ".join(f"{value} {key}" for key, value in counts.items())
        style = self.style.WARNING if counts["errors"] else self.style.SUCCESS
        self.stdout.write(style(f"Blog sync finished: {summary}"))
        if counts["errors"]:
            raise CommandError("Some posts could not be synced; see the log.")

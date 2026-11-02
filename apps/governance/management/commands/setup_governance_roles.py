from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from apps.governance.models import ReviewCase, ReviewEvent


class Command(BaseCommand):
    help = "Create the reviewer and publisher groups used by content governance."

    def handle(self, *args, **options):
        case_type = ContentType.objects.get_for_model(ReviewCase)
        event_type = ContentType.objects.get_for_model(ReviewEvent)
        reviewer_permissions = Permission.objects.filter(
            content_type=case_type,
            codename__in=["can_review_content", "view_reviewcase"],
        ) | Permission.objects.filter(
            content_type=event_type,
            codename="view_reviewevent",
        )
        publisher_permissions = Permission.objects.filter(
            content_type=case_type,
            codename__in=["can_publish_content", "view_reviewcase"],
        ) | Permission.objects.filter(
            content_type=event_type,
            codename="view_reviewevent",
        )

        for name, permissions in (
            ("Content Reviewers", reviewer_permissions),
            ("Content Publishers", publisher_permissions),
        ):
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(permissions)
            self.stdout.write(f"Configured {name}")

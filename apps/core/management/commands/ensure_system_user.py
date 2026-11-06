"""Create (or repair) the non-login account that authors automated content."""

from __future__ import annotations

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.users.models import CustomUser


class Command(BaseCommand):
    help = (
        "Ensure the automation system user exists. The account has an unusable "
        "password, no staff/superuser rights and no verified email, so it can "
        "author content but can never sign in. Safe to run repeatedly."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=getattr(
                settings, "FIRMWARE_BLOG_AUTOMATION_USERNAME", "mobofirm-automation"
            ),
            help="Username of the automation account (default: settings value).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username:
            raise CommandError("--username must not be empty.")
        user = CustomUser.objects.filter(username=username).first()
        created = user is None
        if user is None:
            user = CustomUser.objects.create_user(
                email=f"{username}@automation.invalid", username=username
            )

        user.set_unusable_password()
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True
        user.full_name = user.full_name or "Mobofirm Automation"
        user.role = CustomUser.Roles.AUTHOR
        user.can_create_blog_posts = True
        user.profile_completed = True
        user.save()
        EmailAddress.objects.filter(user=user).update(verified=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Verified'} system user '{username}'."
            )
        )

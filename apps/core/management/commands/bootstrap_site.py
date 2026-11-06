"""Make a freshly migrated database ready to serve the site (idempotent)."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.signals import ensure_singletons
from apps.site_settings.services import get_site_settings

# Group name -> (app_label, permission codename) pairs. Permissions that do not
# exist in the installed apps are reported, never silently ignored.
GROUP_PERMISSIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "Editors": (
        ("blog", "add_post"),
        ("blog", "change_post"),
        ("blog", "view_post"),
        ("blog", "add_category"),
        ("blog", "change_category"),
        ("blog", "view_category"),
        ("tags", "add_tag"),
        ("tags", "change_tag"),
        ("tags", "view_tag"),
    ),
    "Moderators": (
        ("comments", "view_comment"),
        ("comments", "change_comment"),
        ("comments", "delete_comment"),
    ),
    "Firmware reviewers": (
        ("firmwares", "view_pendingfirmware"),
        ("firmwares", "change_pendingfirmware"),
    ),
}


class Command(BaseCommand):
    help = (
        "Set the Site row from the site settings (SITE_URL/SITE_NAME defaults), "
        "create the AppRegistry and the "
        "other singleton settings rows, the staff permission groups and the "
        "automation system user. Safe to run repeatedly."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        self._ensure_site()
        ensure_singletons()
        self.stdout.write("Singleton settings and AppRegistry are present.")
        self._ensure_groups()
        call_command("ensure_system_user", stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS("Site bootstrap complete."))

    def _ensure_site(self) -> None:
        snapshot = get_site_settings()
        site_url = urlsplit(snapshot.site_url)
        domain = site_url.netloc or next(
            (host for host in settings.ALLOWED_HOSTS if host != "*"), "localhost"
        )
        site, created = Site.objects.update_or_create(
            pk=getattr(settings, "SITE_ID", 1),
            defaults={"domain": domain, "name": snapshot.site_name[:50]},
        )
        Site.objects.clear_cache()
        self.stdout.write(
            f"{'Created' if created else 'Updated'} Site {site.pk}: {site.domain}"
        )

    def _ensure_groups(self) -> None:
        for name, wanted in GROUP_PERMISSIONS.items():
            group, _ = Group.objects.get_or_create(name=name)
            permissions = []
            for app_label, codename in wanted:
                permission = Permission.objects.filter(
                    content_type__app_label=app_label, codename=codename
                ).first()
                if permission is None:
                    self.stderr.write(
                        f"Group '{name}': permission {app_label}.{codename} "
                        "does not exist and was not granted."
                    )
                    continue
                permissions.append(permission)
            group.permissions.add(*permissions)
            self.stdout.write(f"Group '{name}': {len(permissions)} permission(s).")

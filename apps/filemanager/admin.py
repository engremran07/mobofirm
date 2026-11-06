"""File manager administration (Unfold): quotas, trash purging and share control.

Staff never read or add user files here. Destructive operations reuse
``apps.filemanager.services`` (acting as the file's owner) so quota accounting,
version cleanup and byte deletion follow the same code path as the user-facing app.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.db.models import Model as DjangoModel
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
)
from unfold.forms import BaseDialogForm

from apps.core.admin_base import (
    ModelAdmin,
    NoAddModelAdmin,
    TabularInline,
    action,
    change_url,
    changelist_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)

from . import services
from .models import (
    FileItem,
    FileShare,
    FileVersion,
    Folder,
    FolderPermission,
    StorageUsage,
)
from .templatetags.filemanager_tags import filesize

TRASH_BADGES = {"active": "success", "in trash": "warning"}
SHARE_BADGES = {"active": "success", "inactive": "danger"}


@register(Folder)
class FolderAdmin(ModelAdmin):
    list_display = ("name", "owner", "parent", "created_at")
    list_filter = (("created_at", RangeDateTimeFilter),)
    search_fields = ("name", "owner__email", "owner__username")
    raw_id_fields = ("owner", "parent")
    list_select_related = ("owner", "parent")


class FileVersionInline(TabularInline):
    model = FileVersion
    fields = ("version_number", "size", "sha256", "mime_type", "created_by")
    readonly_fields = fields
    can_delete = False
    tab = True
    ordering = ("-version_number",)

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(FileItem)
class FileItemAdmin(NoAddModelAdmin):
    list_display = (
        "name",
        "owner",
        "file_type",
        "size_display",
        "trash_badge",
        "download_count",
        "created_at",
    )
    list_filter = (
        ("file_type", ChoicesDropdownFilter),
        "is_active",
        ("created_at", RangeDateTimeFilter),
        ("deleted_at", RangeDateTimeFilter),
    )
    search_fields = ("name", "owner__email", "owner__username", "sha256")
    list_select_related = ("owner",)
    readonly_fields = (
        "stored_name",
        "size",
        "sha256",
        "mime_type",
        "file_type",
        "download_count",
        "deleted_at",
    )
    exclude = ("file", "thumbnail")  # private storage has no public URL
    raw_id_fields = ("owner", "folder")
    inlines = (FileVersionInline,)
    actions = ("purge_selected_from_trash",)
    actions_list = ("purge_expired_trash",)

    @display(description=_("Stored name"))
    def stored_name(self, obj: FileItem) -> str:
        return obj.file.name or "—"

    @display(description=_("Size"), ordering="size")
    def size_display(self, obj: FileItem) -> str:
        return filesize(obj.size)

    @display(description=_("State"), ordering="is_active", label=TRASH_BADGES)
    def trash_badge(self, obj: FileItem) -> str:
        return "active" if obj.is_active else "in trash"

    @action(
        description=_("Permanently delete selected files already in the trash"),
        permissions=["delete"],
    )
    def purge_selected_from_trash(
        self, request: HttpRequest, queryset: QuerySet[FileItem]
    ) -> None:
        purged = 0
        for item in queryset.select_related("owner"):
            try:
                services.purge_file(item.owner, item)
            except services.FileManagerError as exc:
                self.message_user(request, f"{item.name}: {exc}", messages.ERROR)
            else:
                purged += 1
        self.message_user(
            request, _("%(count)d file(s) permanently deleted.") % {"count": purged}
        )

    @action(
        description=_("Purge expired trash"),
        icon="delete_sweep",
        permissions=["delete"],
        url_path="purge-expired-trash",
        dialog=confirm_dialog(
            _("Purge expired trash"),
            _(
                "Permanently deletes every file that has been in the trash longer "
                "than the retention window. This cannot be undone."
            ),
            submit=_("Purge"),
        ),
    )
    def purge_expired_trash(
        self, request: HttpRequest, form: BaseDialogForm
    ) -> HttpResponse:
        purged = services.purge_deleted_files()
        self.message_user(
            request, _("%(count)d expired file(s) purged.") % {"count": purged}
        )
        return htmx_redirect(return_url(request, changelist_url(self)))


@register(FileShare)
class FileShareAdmin(NoAddModelAdmin):
    list_display = (
        "file_item",
        "created_by",
        "state_badge",
        "expires_at",
        "download_count",
    )
    list_filter = (
        "is_active",
        ("expires_at", RangeDateTimeFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("file_item__name", "created_by__email")
    exclude = ("token", "password_hash")
    readonly_fields = ("download_count", "failed_attempts", "locked_until")
    raw_id_fields = ("file_item", "created_by")
    list_select_related = ("file_item", "created_by")
    actions = ("revoke_selected",)
    actions_row = ("revoke_share",)
    actions_detail = ("revoke_share",)
    actions_list = ("expire_shares",)

    @display(description=_("State"), ordering="is_active", label=SHARE_BADGES)
    def state_badge(self, obj: FileShare) -> str:
        return "active" if obj.is_active else "inactive"

    def _revoke(self, share: FileShare) -> FileShare:
        return services.revoke_share(share.file_item.owner, share)

    @action(description=_("Revoke selected share links"), permissions=["change"])
    def revoke_selected(
        self, request: HttpRequest, queryset: QuerySet[FileShare]
    ) -> None:
        revoked = 0
        for share in queryset.filter(is_active=True).select_related("file_item__owner"):
            self._revoke(share)
            revoked += 1
        self.message_user(
            request, _("%(count)d share link(s) revoked.") % {"count": revoked}
        )

    @action(
        description=_("Revoke link"),
        icon="link_off",
        permissions=["change"],
        url_path="revoke-share",
        dialog=confirm_dialog(
            _("Revoke this share link"),
            _("Anyone holding the link loses access immediately."),
            submit=_("Revoke"),
        ),
    )
    def revoke_share(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        share = self.get_object(request, unquote(object_id))
        if share is None:
            raise Http404
        self._revoke(share)
        self.message_user(request, _("Share link revoked."))
        return htmx_redirect(return_url(request, change_url(self, share.pk)))

    @action(
        description=_("Expire overdue links"),
        icon="schedule",
        permissions=["change"],
        url_path="expire-shares",
        dialog=confirm_dialog(
            _("Expire overdue share links"),
            _("Deactivates every share link whose expiry date has passed."),
            submit=_("Expire"),
        ),
    )
    def expire_shares(self, request: HttpRequest, form: BaseDialogForm) -> HttpResponse:
        count = services.expire_shares()
        self.message_user(
            request, _("%(count)d share link(s) expired.") % {"count": count}
        )
        return htmx_redirect(return_url(request, changelist_url(self)))


@register(FolderPermission)
class FolderPermissionAdmin(ModelAdmin):
    list_display = (
        "folder",
        "user",
        "group",
        "can_read",
        "can_write",
        "can_delete",
        "can_share",
    )
    list_filter = ("can_read", "can_write", "can_delete", "can_share")
    raw_id_fields = ("folder", "user")
    list_select_related = ("folder", "user", "group")


@register(StorageUsage)
class StorageUsageAdmin(ModelAdmin):
    list_display = (
        "user",
        "used_display",
        "quota_display",
        "percent_display",
    )
    list_filter = (("updated_at", RangeDateTimeFilter),)
    search_fields = ("user__email", "user__username")
    readonly_fields = ("used_bytes",)
    raw_id_fields = ("user",)
    list_select_related = ("user",)
    actions = ("recalculate_selected",)
    actions_row = ("recalculate_usage",)
    actions_detail = ("recalculate_usage",)

    @display(description=_("Used"), ordering="used_bytes")
    def used_display(self, obj: StorageUsage) -> str:
        return filesize(obj.used_bytes)

    @display(description=_("Quota"), ordering="quota_bytes")
    def quota_display(self, obj: StorageUsage) -> str:
        suffix = "" if obj.quota_bytes is not None else " (default)"
        return f"{filesize(obj.effective_quota)}{suffix}"

    @display(
        description=_("Used %"),
        label={"ok": "success", "high": "warning", "full": "danger"},
    )
    def percent_display(self, obj: StorageUsage) -> str:
        percent = obj.percent_used
        if percent >= 100:
            return "full"
        return "high" if percent >= 80 else "ok"

    @action(
        description=_("Recalculate usage from stored files"), permissions=["change"]
    )
    def recalculate_selected(
        self, request: HttpRequest, queryset: QuerySet[StorageUsage]
    ) -> None:
        count = 0
        for usage in queryset.select_related("user"):
            services.recalculate_usage(usage.user)
            count += 1
        self.message_user(
            request, _("%(count)d usage record(s) recalculated.") % {"count": count}
        )

    @action(
        description=_("Recalculate usage"),
        icon="calculate",
        permissions=["change"],
        url_path="recalculate-usage",
        dialog=confirm_dialog(
            _("Recalculate storage usage"),
            _("Recounts the user's active files and versions as the source of truth."),
            submit=_("Recalculate"),
        ),
    )
    def recalculate_usage(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        usage = self.get_object(request, unquote(object_id))
        if usage is None:
            raise Http404
        refreshed = services.recalculate_usage(usage.user)
        self.message_user(
            request,
            _("Usage is now %(used)s.") % {"used": filesize(refreshed.used_bytes)},
        )
        return htmx_redirect(return_url(request, change_url(self, usage.pk)))

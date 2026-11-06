from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.decorators import require_app_enabled
from apps.users.request_user import current_user

from . import permissions, services
from .forms import FolderForm, RenameForm, ShareForm, SharePasswordForm, TagsForm
from .models import FileItem, FileShare, FileTag, FileType, FileVersion, Folder

PAGE_SIZE = 48


def _folder_url(folder: Folder | None) -> str:
    url = reverse("filemanager:browser")
    return f"{url}?folder={folder.pk}" if folder else url


def _folder_redirect(folder: Folder | None) -> HttpResponse:
    return redirect(_folder_url(folder))


def _detail_redirect(item: FileItem, anchor: str = "") -> HttpResponse:
    return redirect(reverse("filemanager:file_detail", kwargs={"pk": item.pk}) + anchor)


def _get_required_folder(user, raw_id: str, action: str = "read") -> Folder:
    """Resolve a folder id; missing, hidden or forbidden folders return 404."""
    if not raw_id.isdigit():
        raise Http404
    folder = get_object_or_404(Folder, pk=int(raw_id))
    if not permissions.can_access_folder(user, folder, action):
        raise Http404
    return folder


def _get_folder(user, raw_id: str | None, action: str = "read") -> Folder | None:
    """Resolve an optional folder id; hidden or forbidden folders return 404."""
    if not raw_id:
        return None
    return _get_required_folder(user, raw_id, action)


def _get_file(user, pk: int, action: str) -> FileItem:
    """Fetch a file the user may act on; anything else is a 404 (no existence leak)."""
    item = get_object_or_404(FileItem.objects.select_related("folder"), pk=pk)
    if not item.is_active or not permissions.can_access_file(user, item, action):
        raise Http404
    return item


def _get_trashed(user, pk: int) -> FileItem:
    item = get_object_or_404(FileItem.objects.select_related("folder"), pk=pk)
    if item.is_active or not permissions.can_access_file(user, item, "delete"):
        raise Http404
    return item


def _get_version(item: FileItem, pk: int) -> FileVersion:
    return get_object_or_404(FileVersion, pk=pk, file_item=item)


def _stream(handle, name: str) -> FileResponse:
    return FileResponse(
        handle,
        as_attachment=True,
        filename=name,
        content_type="application/octet-stream",
    )


def _open_or_404(field_file):
    try:
        return field_file.open("rb")
    except FileNotFoundError:
        raise Http404 from None


def _verified_or_redirect(request: HttpRequest, destination: HttpResponse):
    if current_user(request).is_verified:
        return None
    messages.error(request, "Verify your email address to use this feature.")
    return destination


def _attempt(request: HttpRequest, action, success: str) -> bool:
    """Run a service call, turning its user-facing errors into messages."""
    try:
        action()
    except services.FileManagerError as exc:
        messages.error(request, str(exc))
        return False
    messages.success(request, success)
    return True


def _sidebar(user) -> dict[str, object]:
    return {
        "usage": services.get_usage(user),
        "trash_count": services.trashed_files(user).count(),
        "tags": FileTag.objects.filter(owner=user).annotate(
            total=Count("files", filter=Q(files__is_active=True))
        ),
    }


# --- Browsing --------------------------------------------------------------


@require_GET
@require_app_enabled("filemanager")
@login_required
def browser(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    folder = _get_folder(user, request.GET.get("folder"))
    query = request.GET.get("q", "").strip()[:100]
    tag_slug = request.GET.get("tag", "").strip()[:60]
    type_filter = request.GET.get("type", "")
    if type_filter not in FileType.values:
        type_filter = ""
    searching = bool(query or tag_slug or type_filter)

    if searching:
        folders = Folder.objects.none()
        files = permissions.readable_files(user)
        if query:
            files = files.filter(name__icontains=query)
        if tag_slug:
            files = files.filter(tags__slug=tag_slug, tags__owner=user)
        if type_filter:
            files = files.filter(file_type=type_filter)
    elif folder is None:
        readable = permissions.readable_folders(user)
        folders = readable.filter(
            Q(parent__isnull=True) | ~Q(parent__in=readable)
        ).select_related("owner")
        files = FileItem.objects.filter(owner=user, folder__isnull=True, is_active=True)
    else:
        folders = Folder.objects.filter(parent=folder)
        files = FileItem.objects.filter(folder=folder, is_active=True)

    page = Paginator(files.prefetch_related("tags").order_by("-created_at"), PAGE_SIZE)
    return render(
        request,
        "filemanager/browser.html",
        {
            **_sidebar(request.user),
            "folder": folder,
            "breadcrumbs": folder.get_ancestors() if folder else [],
            "folders": folders,
            "page_obj": page.get_page(request.GET.get("page")),
            "can_write": permissions.can_access_folder(request.user, folder, "write"),
            "searching": searching,
            "query": query,
            "tag_filter": tag_slug,
            "type_filter": type_filter,
            "file_types": FileType.choices,
            "folder_form": FolderForm(),
        },
    )


@require_GET
@require_app_enabled("filemanager")
@login_required
def file_detail(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_file(request.user, pk, "read")
    can = {
        action: permissions.can_access_file(request.user, item, action)
        for action in ("write", "delete", "share")
    }
    is_owner = item.owner_id == request.user.pk
    return render(
        request,
        "filemanager/file_detail.html",
        {
            **_sidebar(request.user),
            "item": item,
            "folder": item.folder,
            "breadcrumbs": item.folder.get_ancestors() if item.folder else [],
            "can": can,
            "is_owner": is_owner,
            "versions": item.versions.all(),
            "version_limit": services.MAX_VERSIONS_PER_FILE,
            "shares": item.shares.filter(is_active=True) if can["share"] else [],
            "tags_form": TagsForm(
                initial={"tags": ", ".join(tag.name for tag in item.tags.all())}
            ),
            "share_form": ShareForm(),
            "rename_form": RenameForm(initial={"name": item.name}),
            "folder_choices": services.folder_path_choices(item.owner)
            if can["write"] and is_owner
            else [],
            "total_bytes": services.total_bytes(item),
        },
    )


@require_GET
@require_app_enabled("filemanager")
@login_required
def thumbnail(request: HttpRequest, pk: int) -> FileResponse:
    item = _get_file(request.user, pk, "read")
    if not item.thumbnail:
        raise Http404
    response = FileResponse(_open_or_404(item.thumbnail), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=3600"
    return response


# --- Uploading and organising ----------------------------------------------


@require_POST
@require_app_enabled("filemanager")
@login_required
def upload(request: HttpRequest) -> HttpResponse:
    folder = _get_folder(request.user, request.POST.get("folder"), "write")
    destination = _folder_redirect(folder)
    blocked = _verified_or_redirect(request, destination)
    if blocked is None:
        uploads = request.FILES.getlist("files")
        if not uploads:
            messages.error(request, "Choose at least one file.")
        stored = 0
        for uploaded in uploads:
            try:
                services.store_upload(request.user, uploaded, folder)
            except services.FileManagerError as exc:
                messages.error(request, f"{uploaded.name}: {exc}")
            else:
                stored += 1
        if stored:
            messages.success(request, f"Uploaded {stored} file(s).")
    if request.headers.get("HX-Request"):
        # The drop zone posts with HTMX; a client-side redirect shows the new messages.
        return HttpResponse(headers={"HX-Redirect": destination["Location"]})
    return destination


@require_POST
@require_app_enabled("filemanager")
@login_required
def create_folder(request: HttpRequest) -> HttpResponse:
    parent = _get_folder(request.user, request.POST.get("parent"), "write")
    form = FolderForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a folder name.")
        return _folder_redirect(parent)
    _attempt(
        request,
        lambda: services.create_folder(request.user, form.cleaned_data["name"], parent),
        "Folder created.",
    )
    return _folder_redirect(parent)


@require_POST
@require_app_enabled("filemanager")
@login_required
def rename_folder(request: HttpRequest, pk: int) -> HttpResponse:
    folder = _get_required_folder(request.user, str(pk), "write")
    form = RenameForm(request.POST)
    if form.is_valid():
        _attempt(
            request,
            lambda: services.rename_folder(
                request.user, folder, form.cleaned_data["name"]
            ),
            "Folder renamed.",
        )
    else:
        messages.error(request, "Enter a folder name.")
    return _folder_redirect(folder)


@require_POST
@require_app_enabled("filemanager")
@login_required
def delete_folder(request: HttpRequest, pk: int) -> HttpResponse:
    folder = _get_required_folder(request.user, str(pk), "delete")
    parent = folder.parent
    if _attempt(
        request, lambda: services.delete_folder(request.user, folder), "Folder deleted."
    ):
        return _folder_redirect(parent)
    return _folder_redirect(folder)


@require_POST
@require_app_enabled("filemanager")
@login_required
def rename_file(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_file(request.user, pk, "write")
    form = RenameForm(request.POST)
    if form.is_valid():
        _attempt(
            request,
            lambda: services.rename_file(request.user, item, form.cleaned_data["name"]),
            "File renamed.",
        )
    else:
        messages.error(request, "Enter a file name.")
    return _detail_redirect(item)


@require_POST
@require_app_enabled("filemanager")
@login_required
def move_file(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_file(request.user, pk, "write")
    destination = _get_folder(request.user, request.POST.get("destination"), "write")
    if _attempt(
        request,
        lambda: services.move_file(request.user, item, destination),
        "File moved.",
    ):
        return _folder_redirect(destination)
    return _detail_redirect(item)


@require_POST
@require_app_enabled("filemanager")
@login_required
def set_tags(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_file(request.user, pk, "write")
    form = TagsForm(request.POST)
    if form.is_valid():
        _attempt(
            request,
            lambda: services.set_file_tags(
                request.user, item, form.cleaned_data["tags"]
            ),
            "Tags saved.",
        )
    else:
        messages.error(request, "Check the tags and try again.")
    return _detail_redirect(item, "#tags")


@require_POST
@require_app_enabled("filemanager")
@login_required
def delete_file(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_file(request.user, pk, "delete")
    services.soft_delete_file(request.user, item)
    messages.success(request, "File moved to the trash.")
    return _folder_redirect(item.folder)


@require_GET
@require_app_enabled("filemanager")
@login_required
def download(request: HttpRequest, pk: int) -> FileResponse:
    item = _get_file(request.user, pk, "read")
    response = _stream(_open_or_404(item.file), item.name)
    FileItem.objects.filter(pk=item.pk).update(download_count=F("download_count") + 1)
    return response


# --- Trash -----------------------------------------------------------------


@require_GET
@require_app_enabled("filemanager")
@login_required
def trash(request: HttpRequest) -> HttpResponse:
    files = list(services.trashed_files(request.user))
    for item in files:
        item.purge_on = item.deleted_at + timedelta(days=services.TRASH_RETENTION_DAYS)
    return render(
        request,
        "filemanager/trash.html",
        {
            **_sidebar(request.user),
            "files": files,
            "retention_note": (
                f"Deleted files wait here for {services.TRASH_RETENTION_DAYS} days "
                "and are then removed permanently."
            ),
        },
    )


@require_POST
@require_app_enabled("filemanager")
@login_required
def restore_file(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_trashed(request.user, pk)
    if _attempt(
        request, lambda: services.restore_file(request.user, item), "File restored."
    ):
        return _detail_redirect(item)
    return redirect("filemanager:trash")


@require_POST
@require_app_enabled("filemanager")
@login_required
def purge_file(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_trashed(request.user, pk)
    _attempt(
        request,
        lambda: services.purge_file(request.user, item),
        "File deleted permanently.",
    )
    return redirect("filemanager:trash")


@require_POST
@require_app_enabled("filemanager")
@login_required
def empty_trash(request: HttpRequest) -> HttpResponse:
    count = services.empty_trash(request.user)
    messages.success(request, f"Deleted {count} file(s) permanently.")
    return redirect("filemanager:trash")


# --- Versions --------------------------------------------------------------


@require_POST
@require_app_enabled("filemanager")
@login_required
def upload_version(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_file(request.user, pk, "write")
    destination = _detail_redirect(item, "#versions")
    blocked = _verified_or_redirect(request, destination)
    if blocked:
        return blocked
    uploaded = request.FILES.get("file")
    if uploaded is None:
        messages.error(request, "Choose a file for the new version.")
        return destination
    _attempt(
        request,
        lambda: services.add_version(request.user, item, uploaded),
        "New version uploaded; the previous one is kept in the history.",
    )
    return destination


@require_POST
@require_app_enabled("filemanager")
@login_required
def restore_version(request: HttpRequest, pk: int, version_pk: int) -> HttpResponse:
    item = _get_file(request.user, pk, "write")
    version = _get_version(item, version_pk)
    _attempt(
        request,
        lambda: services.restore_version(request.user, item, version),
        "Version restored; the replaced content is kept in the history.",
    )
    return _detail_redirect(item, "#versions")


@require_POST
@require_app_enabled("filemanager")
@login_required
def delete_version(request: HttpRequest, pk: int, version_pk: int) -> HttpResponse:
    item = _get_file(request.user, pk, "delete")
    version = _get_version(item, version_pk)
    _attempt(
        request,
        lambda: services.delete_version(request.user, item, version),
        "Version deleted.",
    )
    return _detail_redirect(item, "#versions")


@require_GET
@require_app_enabled("filemanager")
@login_required
def download_version(request: HttpRequest, pk: int, version_pk: int) -> FileResponse:
    item = _get_file(request.user, pk, "read")
    version = _get_version(item, version_pk)
    return _stream(_open_or_404(version.file), item.name)


# --- Share links -----------------------------------------------------------


@require_GET
@require_app_enabled("filemanager")
@login_required
def shares(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "filemanager/shares.html",
        {**_sidebar(request.user), "shares": services.user_shares(request.user)},
    )


@require_POST
@require_app_enabled("filemanager")
@login_required
def create_share(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_file(request.user, pk, "share")
    blocked = _verified_or_redirect(request, _detail_redirect(item, "#shares"))
    if blocked:
        return blocked
    form = ShareForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Check the share options and try again.")
        return _detail_redirect(item, "#shares")
    try:
        share = services.create_share(
            request.user,
            item,
            expires_hours=form.cleaned_data["expires_hours"],
            max_downloads=form.cleaned_data["max_downloads"],
            password=form.cleaned_data["password"],
        )
    except services.FileManagerError as exc:
        messages.error(request, str(exc))
    else:
        link = request.build_absolute_uri(
            reverse("filemanager:shared_file", kwargs={"token": share.token})
        )
        messages.success(request, f"Share link created: {link}")
    return _detail_redirect(item, "#shares")


@require_POST
@require_app_enabled("filemanager")
@login_required
def revoke_share(request: HttpRequest, pk: int) -> HttpResponse:
    share = get_object_or_404(FileShare.objects.select_related("file_item"), pk=pk)
    try:
        services.revoke_share(request.user, share)
    except services.PermissionDenied:
        raise Http404 from None
    messages.success(request, "Share link revoked.")
    if request.POST.get("next") == "shares":
        return redirect("filemanager:shares")
    return _detail_redirect(share.file_item, "#shares")


@require_http_methods(["GET", "POST"])
@require_app_enabled("filemanager")
def shared_file(request: HttpRequest, token: str) -> HttpResponseBase:
    """Public share page; the download itself is a CSRF-protected POST."""
    share = FileShare.objects.select_related("file_item").filter(token=token).first()
    context = {"share": share, "item": share.file_item if share else None}
    if request.method == "GET":
        if share is None or not share.is_active or not share.file_item.is_active:
            return render(
                request,
                "filemanager/shared_file.html",
                {"unavailable": True},
                status=404,
            )
        return render(
            request,
            "filemanager/shared_file.html",
            {
                **context,
                "form": SharePasswordForm(),
                "needs_password": bool(share.password_hash),
            },
        )

    form = SharePasswordForm(request.POST)
    password = form.cleaned_data.get("password", "") if form.is_valid() else ""
    try:
        authorised = services.authorize_share(token, password)
    except services.ShareLocked:
        return render(
            request,
            "filemanager/shared_file.html",
            {"unavailable": True, "locked": True},
            status=429,
        )
    except services.ShareUnavailable:
        return render(
            request, "filemanager/shared_file.html", {"unavailable": True}, status=404
        )
    except services.WrongSharePassword:
        return render(
            request,
            "filemanager/shared_file.html",
            {
                **context,
                "form": SharePasswordForm(),
                "needs_password": True,
                "wrong_password": True,
            },
            status=403,
        )
    if not services.consume_share(authorised):
        return render(
            request, "filemanager/shared_file.html", {"unavailable": True}, status=404
        )
    return _stream(_open_or_404(authorised.file_item.file), authorised.file_item.name)

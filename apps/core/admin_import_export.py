"""First-party import and export for console model admins.

Add :class:`ImportExportMixin` to a ``ModelAdmin`` and point ``import_export_resource``
at a :class:`Resource` subclass that documents the columns and validates the rows. No
third-party package is involved; CSV and JSON are always available, XLSX only when
``openpyxl`` is already installed.

Export
    Streamed CSV / JSON (XLSX is built in memory). Scope: the rows matching the
    current changelist filters, the selected rows (bulk actions) or every row. CSV and
    XLSX cells that a spreadsheet could read as a formula (``= + - @``, tab, carriage
    return) are prefixed with ``'``; the importer removes that prefix again.

Import
    upload -> **dry-run preview** (rows to create / update / skip, per-row errors) ->
    confirm -> one atomic apply recorded as a ``LogEntry``. The preview never writes.
    Apply re-plans inside the transaction and refuses to run when any row has an
    error, so a bad row can never leave a partial import. Rows are matched by the
    resource's natural key, which makes importing the same file twice a no-op.

Limits: :data:`MAX_IMPORT_ROWS` rows and :data:`MAX_IMPORT_BYTES` bytes per file.
Permissions: export needs ``view``; import needs ``add`` and/or ``change`` (a row that
would create or update without the matching permission is an error).
"""

from __future__ import annotations

import copy
import csv
import importlib
import importlib.util
import io
import json
import secrets
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlencode, urlsplit

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import models, transaction
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    QueryDict,
    StreamingHttpResponse,
)
from django.http.response import HttpResponseBase
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.core.admin_actions import action
from apps.core.admin_pages import render_admin_page

__all__ = [
    "FORMULA_PREFIXES",
    "MAX_IMPORT_BYTES",
    "MAX_IMPORT_ROWS",
    "Column",
    "ImportExportMixin",
    "ImportRejected",
    "PlannedRow",
    "Resource",
    "RowError",
    "apply_import",
    "escape_cell",
    "parse_upload",
    "plan_import",
    "unescape_cell",
    "xlsx_available",
]

MAX_IMPORT_ROWS = 5000
MAX_IMPORT_BYTES = 2 * 1024 * 1024
PREVIEW_ROW_LIMIT = 200
TOKEN_TTL = 30 * 60
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
LIST_SEPARATOR = "|"
FORMATS = ("csv", "json", "xlsx")
CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class RowError(Exception):
    """A row that cannot be imported; ``messages`` are shown next to it."""

    def __init__(self, *messages_: str) -> None:
        super().__init__("; ".join(messages_))
        self.messages = list(messages_)


class ImportRejected(Exception):
    """The apply step found errors (or the file changed); nothing was written."""

    def __init__(self, plan: list[PlannedRow]) -> None:
        super().__init__("The import has errors.")
        self.plan = plan


@dataclass(frozen=True)
class Column:
    """One documented column of a resource."""

    name: str
    doc: str
    required: bool = False
    key: bool = False


@dataclass
class PlannedRow:
    """The outcome of the dry run for one file row."""

    number: int
    action: str  # create | update | skip | error
    label: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    instance: Any = None
    errors: list[str] = field(default_factory=list)


def xlsx_available() -> bool:
    return importlib.util.find_spec("openpyxl") is not None


def escape_cell(value: object) -> object:
    """Neutralise spreadsheet formula injection in a text cell."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return f"'{value}"
    return value


def unescape_cell(value: str) -> str:
    """Undo :func:`escape_cell` (only the exact ``'`` + formula-prefix form)."""
    if value.startswith("'") and value[1:].startswith(FORMULA_PREFIXES):
        return value[1:]
    return value


def serialise(value: object) -> object:
    """A plain cell value for export (lists joined, booleans as true/false)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return LIST_SEPARATOR.join(str(item) for item in value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def split_list(text: str) -> list[str]:
    return [part.strip() for part in text.split(LIST_SEPARATOR) if part.strip()]


def parse_bool(text: str, *, default: bool, column: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return default
    if lowered in {"true", "1", "yes", "y"}:
        return True
    if lowered in {"false", "0", "no", "n"}:
        return False
    raise RowError(f"{column}: use true or false.")


class Resource(ABC):
    """Columns, validation and persistence of one model's import and export."""

    model: ClassVar[type[models.Model]]
    columns: ClassVar[tuple[Column, ...]] = ()
    #: Shown in the documentation: what identifies a row (idempotent re-import).
    natural_key_doc: ClassVar[str] = ""
    #: Extra notes for the documentation (one sentence each).
    notes: ClassVar[tuple[str, ...]] = ()

    def __init__(self, user: Any) -> None:
        self.user = user

    # -- permissions ------------------------------------------------------

    def _perm(self, verb: str) -> bool:
        opts = self.model._meta
        return bool(self.user.has_perm(f"{opts.app_label}.{verb}_{opts.model_name}"))

    def can_create(self) -> bool:
        return self._perm("add")

    def can_update(self) -> bool:
        return self._perm("change")

    # -- export -----------------------------------------------------------

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]

    def export_queryset(self, queryset: models.QuerySet) -> models.QuerySet:
        """Add ``select_related`` / ``prefetch_related`` for ``export_row``."""
        return queryset

    @abstractmethod
    def export_row(self, obj: Any) -> dict[str, object]:
        raise NotImplementedError

    # -- import -----------------------------------------------------------

    def import_context(self) -> AbstractContextManager[Any]:
        """Wraps the whole apply (e.g. to batch automation until after commit)."""
        return nullcontext()

    def prepare(self, rows: Sequence[Mapping[str, str]]) -> None:
        """Called once before ``clean_row`` (prefetch lookups for the file)."""

    @abstractmethod
    def clean_row(self, raw: Mapping[str, str]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def key_of(self, data: Mapping[str, Any]) -> tuple[Any, ...]:
        raise NotImplementedError

    def label_of(self, data: Mapping[str, Any]) -> str:
        return " / ".join(str(part) for part in self.key_of(data))

    @abstractmethod
    def find(self, data: Mapping[str, Any]) -> Any:
        raise NotImplementedError

    @abstractmethod
    def changed(self, instance: Any, data: Mapping[str, Any]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def create(self, data: Mapping[str, Any]) -> Any:
        raise NotImplementedError

    @abstractmethod
    def update(self, instance: Any, data: Mapping[str, Any]) -> Any:
        raise NotImplementedError

    def allows_update(self) -> bool:
        """False for resources whose existing rows are never overwritten."""
        return True


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return LIST_SEPARATOR.join(str(item) for item in value)
    return unescape_cell(str(value).strip())


def parse_upload(name: str, data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """``(header, rows)`` of an uploaded CSV, JSON or XLSX file.

    Raises ``ValueError`` with a user-facing message for unreadable, oversized or
    unsupported files. Rows are string dictionaries keyed by the header names.
    """
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError(f"The file is larger than {MAX_IMPORT_BYTES // 1024} KB.")
    lowered = name.lower()
    if lowered.endswith(".csv"):
        rows = _read_csv(data)
    elif lowered.endswith(".json"):
        rows = _read_json(data)
    elif lowered.endswith(".xlsx") and xlsx_available():
        rows = _read_xlsx(data)
    else:
        allowed = ".csv, .json" + (", .xlsx" if xlsx_available() else "")
        raise ValueError(f"Upload a file of type {allowed}.")
    if len(rows) > MAX_IMPORT_ROWS:
        raise ValueError(f"The file has more than {MAX_IMPORT_ROWS} rows.")
    header: list[str] = []
    for row in rows:
        for key in row:
            if key not in header:
                header.append(key)
    return header, rows


def _read_csv(data: bytes) -> list[dict[str, str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("The CSV file must be UTF-8 encoded.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("The CSV file has no header row.")
    rows: list[dict[str, str]] = []
    for raw in reader:
        if len(rows) > MAX_IMPORT_ROWS:
            break
        values = {str(k).strip(): _cell(v) for k, v in raw.items() if k is not None}
        if any(values.values()):
            rows.append(values)
    return rows


def _read_json(data: bytes) -> list[dict[str, str]]:
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("The JSON file is not valid UTF-8 JSON.") from exc
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        payload = payload["rows"]
    if not isinstance(payload, list) or not all(isinstance(r, dict) for r in payload):
        raise ValueError("The JSON file must be a list of objects.")
    return [
        {str(k): _cell(v) for k, v in row.items()}
        for row in payload[: MAX_IMPORT_ROWS + 1]
        if any(_cell(v) for v in row.values())
    ]


def _read_xlsx(data: bytes) -> list[dict[str, str]]:
    openpyxl = importlib.import_module("openpyxl")
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError("The XLSX file could not be read.") from exc
    sheet = book.active
    iterator = iter(sheet.iter_rows(values_only=True))
    header = [
        str(cell).strip() if cell is not None else "" for cell in next(iterator, ())
    ]
    rows: list[dict[str, str]] = []
    for values in iterator:
        row = {
            name: _cell(cell)
            for name, cell in zip(header, values, strict=False)
            if name
        }
        if any(row.values()):
            rows.append(row)
        if len(rows) > MAX_IMPORT_ROWS:
            break
    return rows


# ---------------------------------------------------------------------------
# Plan and apply
# ---------------------------------------------------------------------------


def plan_import(
    resource: Resource, rows: Sequence[Mapping[str, str]]
) -> list[PlannedRow]:
    """Dry run: classify every row without writing anything."""
    known = {column.name for column in resource.columns}
    resource.prepare(rows)
    plan: list[PlannedRow] = []
    seen: dict[tuple[Any, ...], int] = {}
    for number, raw in enumerate(rows, start=2):  # row 1 is the header
        row = PlannedRow(number=number, action="error")
        plan.append(row)
        try:
            data = resource.clean_row({k: v for k, v in raw.items() if k in known})
            key = resource.key_of(data)
        except RowError as error:
            row.errors = error.messages
            continue
        row.data, row.label = data, resource.label_of(data)
        if key in seen:
            row.errors = [
                f"Duplicate of row {seen[key]} (same {resource.natural_key_doc})."
            ]
            continue
        seen[key] = number
        instance = resource.find(data)
        if instance is None:
            if resource.can_create():
                row.action = "create"
            else:
                row.errors = ["You do not have permission to add this record."]
        elif not resource.changed(instance, data) or not resource.allows_update():
            row.action, row.instance = "skip", instance
        elif resource.can_update():
            row.action, row.instance = "update", instance
        else:
            row.errors = ["You do not have permission to change this record."]
    return plan


def summarise(plan: Iterable[PlannedRow]) -> dict[str, int]:
    counts = {"create": 0, "update": 0, "skip": 0, "error": 0}
    for row in plan:
        counts[row.action] += 1
    return counts


def apply_import(
    resource: Resource,
    rows: Sequence[Mapping[str, str]],
    *,
    user: Any,
    filename: str,
) -> dict[str, int]:
    """Apply the import in one transaction; raises :class:`ImportRejected` on errors."""
    with resource.import_context(), transaction.atomic():
        plan = plan_import(resource, rows)
        counts = summarise(plan)
        if counts["error"]:
            raise ImportRejected(plan)
        for row in plan:
            if row.action == "create":
                resource.create(row.data)
            elif row.action == "update":
                resource.update(row.instance, row.data)
        _audit(resource, user, filename, counts)
    return counts


def _audit(
    resource: Resource, user: Any, filename: str, counts: Mapping[str, int]
) -> None:
    """One admin log entry per import (not per row: files can hold thousands)."""
    opts = resource.model._meta
    LogEntry.objects.create(
        user_id=user.pk,
        content_type=ContentType.objects.get_for_model(resource.model),
        object_id=None,
        object_repr=f"Import of {filename}"[:200],
        action_flag=CHANGE,
        change_message=(
            f"Imported {opts.verbose_name_plural} from {filename}: "
            f"{counts['create']} created, {counts['update']} updated, "
            f"{counts['skip']} unchanged."
        ),
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class _Echo:
    def write(self, value: str) -> str:
        return value


def _rows(resource: Resource, queryset: models.QuerySet) -> Iterator[dict[str, object]]:
    for obj in resource.export_queryset(queryset).iterator(chunk_size=500):
        row = resource.export_row(obj)
        yield {name: serialise(row.get(name, "")) for name in resource.column_names}


def stream_csv(resource: Resource, queryset: models.QuerySet) -> Iterator[str]:
    writer = csv.writer(_Echo())
    yield "\ufeff" + writer.writerow(resource.column_names)
    for row in _rows(resource, queryset):
        yield writer.writerow(
            [escape_cell(row[name]) for name in resource.column_names]
        )


def stream_json(resource: Resource, queryset: models.QuerySet) -> Iterator[str]:
    yield "[\n"
    first = True
    for row in _rows(resource, queryset):
        yield ("" if first else ",\n") + json.dumps(row, ensure_ascii=False)
        first = False
    yield "\n]\n"


def build_xlsx(resource: Resource, queryset: models.QuerySet) -> bytes:
    openpyxl = importlib.import_module("openpyxl")
    book = openpyxl.Workbook(write_only=True)
    sheet = book.create_sheet(str(resource.model._meta.verbose_name_plural)[:31])
    sheet.append(resource.column_names)
    for row in _rows(resource, queryset):
        sheet.append([escape_cell(row[name]) for name in resource.column_names])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def export_response(
    resource: Resource, queryset: models.QuerySet, export_format: str
) -> HttpResponseBase:
    """A streamed (CSV, JSON) or in-memory (XLSX) download of ``queryset``."""
    opts = resource.model._meta
    filename = f"{opts.app_label}-{opts.model_name}.{export_format}"
    if export_format == "xlsx":
        response: HttpResponseBase = HttpResponse(
            build_xlsx(resource, queryset), content_type=CONTENT_TYPES["xlsx"]
        )
    else:
        stream = stream_csv if export_format == "csv" else stream_json
        response = StreamingHttpResponse(
            stream(resource, queryset), content_type=CONTENT_TYPES[export_format]
        )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


# ---------------------------------------------------------------------------
# Admin integration
# ---------------------------------------------------------------------------


class UploadForm(forms.Form):
    file = forms.FileField(label=_("File"), help_text="")


class ExportForm(forms.Form):
    """Scope and format of an export; the choices depend on the request."""

    def __init__(
        self,
        data: Any,
        *,
        scopes: Sequence[tuple[str, str]],
        formats: Sequence[str],
    ) -> None:
        super().__init__(data)
        self.fields["scope"] = forms.ChoiceField(
            label=_("Rows"), choices=list(scopes), initial=scopes[0][0]
        )
        self.fields["export_format"] = forms.ChoiceField(
            label=_("Format"), choices=[(f, f.upper()) for f in formats]
        )


def _token_key(user_pk: object, token: str) -> str:
    return f"console-import:{user_pk}:{token}"


def _instantiate(resource_class: type[Resource], user: Any) -> Resource:
    return resource_class(user)


class ImportExportMixin(admin.ModelAdmin):
    """Import and export pages plus the export bulk actions for a model admin."""

    import_export_resource: ClassVar[type[Resource] | None]

    # -- wiring -----------------------------------------------------------

    def _resource_class(self) -> type[Resource] | None:
        return getattr(self, "import_export_resource", None)

    def get_resource(self, request: HttpRequest) -> Resource:
        resource_class = self._resource_class()
        if resource_class is None:
            raise Http404("This model has no import or export.")
        return _instantiate(resource_class, request.user)

    def _opts_name(self) -> str:
        return f"{self.opts.app_label}_{self.opts.model_name}"

    def get_urls(self) -> list[Any]:
        name = self._opts_name()
        wrap = self.admin_site.admin_view
        return [
            path("import-data/", wrap(self.import_view), name=f"{name}_import"),
            path(
                "import-data/preview/<str:token>/",
                wrap(self.import_preview_view),
                name=f"{name}_import_preview",
            ),
            path(
                "import-data/confirm/<str:token>/",
                wrap(self.import_confirm_view),
                name=f"{name}_import_confirm",
            ),
            path("export-data/", wrap(self.export_view), name=f"{name}_export"),
            *super().get_urls(),
        ]

    # -- permissions ------------------------------------------------------

    def has_import_data_permission(self, request: HttpRequest) -> bool:
        return self._resource_class() is not None and (
            self.has_add_permission(request) or self.has_change_permission(request)
        )

    def has_export_data_permission(self, request: HttpRequest) -> bool:
        return self._resource_class() is not None and self.has_view_permission(request)

    # -- list actions -----------------------------------------------------

    @action(
        description=_("Import"),
        permissions=["import_data"],
        url_path="import",
        icon="upload",
    )
    def import_data(self, request: HttpRequest) -> HttpResponse:
        return redirect(f"admin:{self._opts_name()}_import")

    @action(
        description=_("Export"),
        permissions=["export_data"],
        url_path="export",
        icon="download",
    )
    def export_data(self, request: HttpRequest) -> HttpResponse:
        return redirect(f"admin:{self._opts_name()}_export")

    # -- bulk export of the selected rows ---------------------------------
    # List "export_selected_csv" and "export_selected_json" in the admin's ``actions``.

    @admin.action(
        description=_("Export selected rows as CSV"), permissions=["export_data"]
    )
    def export_selected_csv(
        self, request: HttpRequest, queryset: models.QuerySet
    ) -> HttpResponseBase:
        return export_response(
            self.get_resource(request), queryset.order_by("pk"), "csv"
        )

    @admin.action(
        description=_("Export selected rows as JSON"), permissions=["export_data"]
    )
    def export_selected_json(
        self, request: HttpRequest, queryset: models.QuerySet
    ) -> HttpResponseBase:
        return export_response(
            self.get_resource(request), queryset.order_by("pk"), "json"
        )

    # -- export page ------------------------------------------------------

    def _filter_query(self, request: HttpRequest) -> str:
        """Changelist filters of the page the staff user came from (same host only)."""
        candidate = request.headers.get("HX-Current-URL") or request.headers.get(
            "Referer", ""
        )
        parts = urlsplit(candidate)
        changelist = reverse(f"admin:{self._opts_name()}_changelist")
        if parts.netloc not in {"", request.get_host()} or parts.path != changelist:
            return ""
        query = parse_qs(parts.query, keep_blank_values=False)
        query.pop("p", None)
        return urlencode(query, doseq=True)

    def _scoped_queryset(self, request: HttpRequest, scope: str, query: str):
        if scope == "filtered" and query:
            scoped = copy.copy(request)
            scoped.GET = QueryDict(query)
            return self.get_changelist_instance(scoped).queryset
        return self.get_queryset(request)

    def export_view(self, request: HttpRequest) -> HttpResponseBase:
        if not self.has_export_data_permission(request):
            raise Http404
        resource = self.get_resource(request)
        formats = [f for f in FORMATS if f != "xlsx" or xlsx_available()]
        query = request.GET.get("filters") or self._filter_query(request)
        scopes = [("all", str(_("Every row")))]
        if query:
            scopes.insert(0, ("filtered", str(_("Rows matching the current filters"))))
        form = ExportForm(request.GET or None, scopes=scopes, formats=formats)
        if request.GET.get("download") and form.is_valid():
            queryset = self._scoped_queryset(
                request, form.cleaned_data["scope"], query
            ).order_by("pk")
            return export_response(
                resource, queryset, form.cleaned_data["export_format"]
            )
        return render_admin_page(
            request,
            "console/import_export/export.html",
            title=str(_("Export %(name)s") % {"name": self.opts.verbose_name_plural}),
            breadcrumbs=self._breadcrumbs(request, _("Export")),
            context={
                "form": form,
                "filters": query,
                "resource_doc": _resource_doc(resource),
                "cancel_url": reverse(f"admin:{self._opts_name()}_changelist"),
                "opts": self.opts,
            },
        )

    # -- import pages -----------------------------------------------------

    def _breadcrumbs(
        self, request: HttpRequest, leaf: object
    ) -> list[tuple[str, str | None]]:
        return [
            (str(_("Dashboard")), reverse("admin:index")),
            (
                str(self.opts.verbose_name_plural).capitalize(),
                reverse(f"admin:{self._opts_name()}_changelist"),
            ),
            (str(leaf), None),
        ]

    def import_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_import_data_permission(request):
            raise Http404
        resource = self.get_resource(request)
        form = UploadForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            upload = form.cleaned_data["file"]
            try:
                _header, rows = parse_upload(
                    upload.name, upload.read(MAX_IMPORT_BYTES + 1)
                )
            except ValueError as error:
                form.add_error("file", str(error))
            else:
                if not rows:
                    form.add_error("file", str(_("The file has no data rows.")))
                else:
                    token = secrets.token_urlsafe(24)
                    cache.set(
                        _token_key(request.user.pk, token),
                        {"rows": rows, "filename": upload.name[:120]},
                        TOKEN_TTL,
                    )
                    return redirect(
                        f"admin:{self._opts_name()}_import_preview", token=token
                    )
        return render_admin_page(
            request,
            "console/import_export/upload.html",
            title=str(_("Import %(name)s") % {"name": self.opts.verbose_name_plural}),
            breadcrumbs=self._breadcrumbs(request, _("Import")),
            context={
                "form": form,
                "resource_doc": _resource_doc(resource),
                "max_rows": MAX_IMPORT_ROWS,
                "max_kb": MAX_IMPORT_BYTES // 1024,
                "formats": ", ".join(
                    f".{f}" for f in FORMATS if f != "xlsx" or xlsx_available()
                ),
                "cancel_url": reverse(f"admin:{self._opts_name()}_changelist"),
                "opts": self.opts,
            },
        )

    def _staged(self, request: HttpRequest, token: str) -> dict[str, Any]:
        staged = cache.get(_token_key(request.user.pk, token))
        if staged is None:
            raise Http404("The import expired. Upload the file again.")
        return staged

    def import_preview_view(self, request: HttpRequest, token: str) -> HttpResponse:
        if not self.has_import_data_permission(request):
            raise Http404
        staged = self._staged(request, token)
        resource = self.get_resource(request)
        plan = plan_import(resource, staged["rows"])
        return self._render_preview(request, token, staged, plan)

    def _render_preview(
        self,
        request: HttpRequest,
        token: str,
        staged: Mapping[str, Any],
        plan: list[PlannedRow],
        status: int = 200,
    ) -> HttpResponse:
        counts = summarise(plan)
        shown = [row for row in plan if row.action == "error"] + [
            row for row in plan if row.action != "error"
        ]
        return render_admin_page(
            request,
            "console/import_export/preview.html",
            title=str(
                _("Preview import of %(name)s")
                % {"name": self.opts.verbose_name_plural}
            ),
            breadcrumbs=self._breadcrumbs(request, _("Preview")),
            status=status,
            context={
                "plan": shown[:PREVIEW_ROW_LIMIT],
                "truncated": max(0, len(shown) - PREVIEW_ROW_LIMIT),
                "counts": counts,
                "total": len(plan),
                "filename": staged["filename"],
                "token": token,
                "can_apply": counts["error"] == 0
                and (counts["create"] + counts["update"]) > 0,
                "confirm_url": reverse(
                    f"admin:{self._opts_name()}_import_confirm", args=[token]
                ),
                "upload_url": reverse(f"admin:{self._opts_name()}_import"),
                "cancel_url": reverse(f"admin:{self._opts_name()}_changelist"),
                "opts": self.opts,
            },
        )

    def import_confirm_view(self, request: HttpRequest, token: str) -> HttpResponse:
        if not self.has_import_data_permission(request):
            raise Http404
        if request.method != "POST":
            return redirect(f"admin:{self._opts_name()}_import_preview", token=token)
        staged = self._staged(request, token)
        resource = self.get_resource(request)
        try:
            counts = apply_import(
                resource, staged["rows"], user=request.user, filename=staged["filename"]
            )
        except ImportRejected as rejected:
            return self._render_preview(
                request, token, staged, rejected.plan, status=422
            )
        cache.delete(_token_key(request.user.pk, token))
        messages.success(
            request,
            _(
                "Import finished: %(create)d created, %(update)d updated, "
                "%(skip)d unchanged."
            )
            % counts,
        )
        return HttpResponseRedirect(reverse(f"admin:{self._opts_name()}_changelist"))


def _resource_doc(resource: Resource) -> dict[str, Any]:
    return {
        "columns": resource.columns,
        "natural_key": resource.natural_key_doc,
        "notes": resource.notes,
    }

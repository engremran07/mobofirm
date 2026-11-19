"""Import and export of the firmware catalogue in the staff console.

Engine: ``apps/core/admin_import_export.py``; resources: ``apps/firmwares/admin_io.py``.
Covers preview (dry run), atomic apply, duplicate imports, bad rows, the permission
matrix, spreadsheet formula escaping, limits and the batched automated-post refresh.
"""

from __future__ import annotations

import csv
import io
import json
from unittest.mock import patch

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.core.admin_import_export import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_ROWS,
    ImportRejected,
    apply_import,
    escape_cell,
    parse_upload,
    unescape_cell,
    xlsx_available,
)
from apps.firmwares.admin_io import BrandResource, ChipsetResource
from apps.firmwares.models import (
    Brand,
    Chipset,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
    PendingFirmware,
    Variant,
)

pytestmark = pytest.mark.django_db


def csv_bytes(header, rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode()


def staff_client(*codenames, superuser=False):
    users = get_user_model().objects
    if superuser:
        user = users.create_superuser(
            username="io-admin", email="io-admin@example.test", password="x"
        )
    else:
        user = users.create_user(
            username=f"io-{'-'.join(codenames) or 'none'}",
            email=f"io-{'-'.join(codenames) or 'none'}@example.test",
            password="x",
            is_staff=True,
        )
        user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    client = Client()
    client.force_login(user)
    return client, user


def upload(client, name, data, model="brand"):
    """Upload ``data`` to the import page; returns the preview response (followed)."""
    url = reverse(f"admin:firmwares_{model}_import")
    return client.post(url, {"file": SimpleUploadedFile(name, data)}, follow=True)


def confirm(client, preview_response, model="brand"):
    token = preview_response.redirect_chain[-1][0].rstrip("/").rsplit("/", 1)[-1]
    return client.post(
        reverse(f"admin:firmwares_{model}_import_confirm", args=[token]), follow=False
    )


BRAND_HEADER = ["slug", "name", "website_url", "is_featured"]


class TestExport:
    def test_csv_is_streamed_with_a_bom_header_and_documented_columns(self):
        Brand.objects.create(name="Acme", slug="acme", is_featured=True)
        client, _user = staff_client(superuser=True)

        response = client.get(
            reverse("admin:firmwares_brand_export"),
            {"download": "1", "scope": "all", "export_format": "csv"},
        )

        assert response.status_code == 200 and response.streaming
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment" in response["Content-Disposition"]
        body = b"".join(response.streaming_content).decode("utf-8")
        rows = list(csv.reader(io.StringIO(body.lstrip("\ufeff"))))
        assert rows[0] == [
            "slug",
            "name",
            "website_url",
            "logo_url",
            "description",
            "is_featured",
        ]
        assert rows[1][:2] == ["acme", "Acme"] and rows[1][-1] == "true"

    def test_json_export_is_a_list_of_objects(self):
        Brand.objects.create(name="Acme", slug="acme")
        client, _user = staff_client(superuser=True)
        response = client.get(
            reverse("admin:firmwares_brand_export"),
            {"download": "1", "scope": "all", "export_format": "json"},
        )
        data = json.loads(b"".join(response.streaming_content))
        assert data[0]["slug"] == "acme"

    def test_spreadsheet_formulas_are_neutralised_and_round_trip(self):
        evil = '=HYPERLINK("http://evil.test","x")'
        Brand.objects.create(name=evil, slug="evil")
        Brand.objects.create(name="+SUM(1)", slug="plus")
        client, _user = staff_client(superuser=True)

        response = client.get(
            reverse("admin:firmwares_brand_export"),
            {"download": "1", "scope": "all", "export_format": "csv"},
        )

        body = b"".join(response.streaming_content).decode().lstrip("\ufeff")
        cells = [c for row in csv.reader(io.StringIO(body)) for c in row]
        assert "'" + evil in cells and "'+SUM(1)" in cells
        assert evil not in cells
        assert unescape_cell("'" + evil) == evil
        assert unescape_cell("'quoted") == "'quoted"
        assert escape_cell("=1") == "'=1" and escape_cell("\t1") == "'\t1"
        assert escape_cell("plain") == "plain" and escape_cell(5) == 5

    def test_the_current_filter_scopes_the_export(self):
        Brand.objects.create(name="Star", slug="star", is_featured=True)
        Brand.objects.create(name="Plain", slug="plain")
        client, _user = staff_client(superuser=True)
        changelist = reverse("admin:firmwares_brand_changelist")

        page = client.get(
            reverse("admin:firmwares_brand_export"),
            HTTP_REFERER=f"http://testserver{changelist}?is_featured__exact=1",
        )
        assert "Rows matching the current filters" in page.content.decode()
        response = client.get(
            reverse("admin:firmwares_brand_export"),
            {
                "download": "1",
                "scope": "filtered",
                "export_format": "csv",
                "filters": "is_featured__exact=1",
            },
        )
        body = b"".join(response.streaming_content).decode()
        assert "star" in body and "plain" not in body

    def test_selected_rows_are_exported_by_a_bulk_action(self):
        keep = Brand.objects.create(name="Keep", slug="keep")
        Brand.objects.create(name="Other", slug="other")
        client, _user = staff_client(superuser=True)

        response = client.post(
            reverse("admin:firmwares_brand_changelist"),
            {
                "action": "export_selected_csv",
                "_selected_action": [keep.pk],
                "index": "0",
            },
        )

        assert response.status_code == 200
        body = b"".join(response.streaming_content).decode()
        assert "keep" in body and "other" not in body

    def test_firmware_export_lists_the_official_files(self):
        brand = Brand.objects.create(name="Acme", slug="acme")
        model = Model.objects.create(brand=brand, name="One", slug="one")
        OfficialFirmware.objects.create(
            original_file_name="a.zip",
            source_type="external",
            external_url="https://downloads.example.invalid/a.zip",
            brand=brand,
            model=model,
            is_verified=True,
        )
        client, _user = staff_client(superuser=True)
        response = client.get(
            reverse("admin:firmwares_officialfirmware_export"),
            {"download": "1", "scope": "all", "export_format": "csv"},
        )
        body = b"".join(response.streaming_content).decode()
        assert "a.zip" in body and "true" in body

    def test_the_export_page_offers_xlsx_only_with_openpyxl(self):
        client, _user = staff_client(superuser=True)
        html = client.get(reverse("admin:firmwares_brand_export")).content.decode()
        assert ('value="xlsx"' in html) == xlsx_available()
        assert 'value="csv"' in html and 'value="json"' in html


class TestImportBrands:
    def test_preview_is_a_dry_run_and_confirm_applies_everything_in_one_go(self):
        client, user = staff_client(superuser=True)
        data = csv_bytes(
            BRAND_HEADER,
            [
                ["acme", "Acme", "https://acme.example", "true"],
                ["", "Beta Mobile", "", ""],
            ],
        )

        preview = upload(client, "brands.csv", data)

        html = preview.content.decode()
        assert preview.status_code == 200 and "data-import-preview" in html
        assert "2 to create" in html and "0 with errors" in html
        assert not Brand.objects.exists()

        done = confirm(client, preview)

        assert done.status_code == 302
        assert done["Location"] == reverse("admin:firmwares_brand_changelist")
        assert set(Brand.objects.values_list("slug", flat=True)) == {
            "acme",
            "beta-mobile",
        }
        assert Brand.objects.get(slug="acme").is_featured is True
        entry = LogEntry.objects.get(object_repr="Import of brands.csv")
        assert entry.user_id == user.pk and "2 created" in entry.change_message

    def test_importing_the_same_file_twice_is_a_no_op(self):
        client, _user = staff_client(superuser=True)
        data = csv_bytes(BRAND_HEADER, [["acme", "Acme", "", ""]])
        confirm(client, upload(client, "b.csv", data))

        again = upload(client, "b.csv", data)

        html = again.content.decode()
        assert "1 unchanged" in html and "0 to create" in html and "0 to update" in html
        assert "Confirm and import" not in html
        assert Brand.objects.count() == 1

    def test_a_changed_row_is_an_update_not_a_duplicate(self):
        Brand.objects.create(name="Acme", slug="acme")
        client, _user = staff_client(superuser=True)
        data = csv_bytes(BRAND_HEADER, [["acme", "Acme", "https://acme.example", ""]])

        preview = upload(client, "b.csv", data)
        assert "1 to update" in preview.content.decode()
        confirm(client, preview)

        assert Brand.objects.get(slug="acme").website_url == "https://acme.example"
        assert Brand.objects.count() == 1

    def test_rows_with_errors_block_the_whole_import(self):
        client, _user = staff_client(superuser=True)
        data = csv_bytes(
            BRAND_HEADER,
            [
                ["good", "Good", "", ""],
                ["bad", "Bad", "http://not-https.example", ""],
                ["dup", "Dup", "", ""],
                ["dup", "Dup Again", "", ""],
            ],
        )

        preview = upload(client, "b.csv", data)

        html = preview.content.decode()
        assert "2 with errors" in html
        assert "enter a valid https URL" in html and "Duplicate of row 4" in html
        assert "Confirm and import" not in html
        rejected = confirm(client, preview)
        assert rejected.status_code == 422
        assert not Brand.objects.exists()
        assert not LogEntry.objects.filter(object_repr__startswith="Import of").exists()

    def test_a_failure_while_applying_rolls_every_row_back(self):
        _client, user = staff_client(superuser=True)
        rows = [
            {"slug": "one", "name": "One"},
            {"slug": "two", "name": "Two"},
            {"slug": "three", "name": "Three"},
        ]
        real_create = BrandResource.create
        calls = []

        def flaky(self, data):
            calls.append(data["slug"])
            if len(calls) == 3:
                raise RuntimeError("disk full")
            return real_create(self, data)

        with (
            patch.object(BrandResource, "create", flaky),
            pytest.raises(RuntimeError),
        ):
            apply_import(BrandResource(user), rows, user=user, filename="x.csv")

        assert calls == ["one", "two", "three"]
        assert not Brand.objects.exists()
        assert not LogEntry.objects.filter(object_repr="Import of x.csv").exists()

    def test_apply_refuses_a_plan_with_errors_without_writing(self):
        _client, user = staff_client(superuser=True)
        with pytest.raises(ImportRejected) as rejected:
            apply_import(
                BrandResource(user),
                [{"slug": "ok", "name": "Ok"}, {"slug": "x", "name": ""}],
                user=user,
                filename="x.csv",
            )
        assert [row.action for row in rejected.value.plan] == ["create", "error"]
        assert not Brand.objects.exists()

    def test_the_preview_expires_with_its_token(self):
        client, _user = staff_client(superuser=True)
        response = client.get(
            reverse("admin:firmwares_brand_import_preview", args=["nope"])
        )
        assert response.status_code == 404

    def test_json_files_are_accepted_and_formula_prefixes_are_restored(self):
        client, _user = staff_client(superuser=True)
        data = json.dumps([{"slug": "eq", "name": "'=Equals"}]).encode()
        confirm(client, upload(client, "b.json", data))
        assert Brand.objects.get(slug="eq").name == "=Equals"

    def test_limits_and_unsupported_files_are_refused(self):
        client, _user = staff_client(superuser=True)
        too_many = csv_bytes(
            ["slug", "name"], [[f"b{i}", f"B{i}"] for i in range(MAX_IMPORT_ROWS + 1)]
        )
        for name, data, needle in (
            ("b.csv", too_many, "more than"),
            ("b.csv", b"x" * (MAX_IMPORT_BYTES + 1), "larger than"),
            ("b.txt", b"slug,name", "Upload a file of type"),
            ("b.csv", b"slug,name\n", "no data rows"),
            ("b.json", b"{not json", "not valid"),
        ):
            response = upload(client, name, data)
            assert needle in response.content.decode(), name
        assert not Brand.objects.exists()
        with pytest.raises(ValueError, match="UTF-8"):
            parse_upload("b.csv", b"\xff\xfe\x00")


class TestPermissions:
    def test_the_matrix_of_view_add_and_change(self):
        Brand.objects.create(name="Old", slug="old")
        data = csv_bytes(
            BRAND_HEADER,
            [["old", "Old", "https://old.example", ""], ["fresh", "Fresh", "", ""]],
        )
        export = reverse("admin:firmwares_brand_export")
        import_url = reverse("admin:firmwares_brand_import")

        none, _u = staff_client()
        assert none.get(export).status_code in {302, 403, 404}
        assert none.get(import_url).status_code in {302, 403, 404}

        viewer, _u = staff_client("view_brand")
        assert viewer.get(export).status_code == 200
        assert viewer.get(import_url).status_code == 404

        adder, _u = staff_client("view_brand", "add_brand")
        html = upload(adder, "b.csv", data).content.decode()
        assert "1 to create" in html
        assert "You do not have permission to change this record." in html
        assert "Confirm and import" not in html

        changer, _u = staff_client("view_brand", "change_brand")
        html = upload(changer, "b.csv", data).content.decode()
        assert "1 to update" in html
        assert "You do not have permission to add this record." in html

        full, _u = staff_client("view_brand", "add_brand", "change_brand")
        done = confirm(full, upload(full, "b.csv", data))
        assert done.status_code == 302
        assert Brand.objects.get(slug="fresh") and (
            Brand.objects.get(slug="old").website_url == "https://old.example"
        )

    def test_anonymous_users_cannot_reach_the_pages(self):
        for name in ("import", "export"):
            response = Client().get(reverse(f"admin:firmwares_brand_{name}"))
            assert response.status_code == 302 and "login" in response["Location"]

    def test_confirm_needs_post_and_csrf_is_enforced(self):
        _client, user = staff_client(superuser=True)
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.post(
            reverse("admin:firmwares_brand_import_confirm", args=["t"])
        )
        assert response.status_code == 403

    def test_the_tokens_are_bound_to_the_user(self):
        owner, _u = staff_client(superuser=True)
        preview = upload(owner, "b.csv", csv_bytes(BRAND_HEADER, [["x", "X", "", ""]]))
        token = preview.redirect_chain[-1][0].rstrip("/").rsplit("/", 1)[-1]
        other = Client()
        other.force_login(
            get_user_model().objects.create_superuser(
                username="other-admin", email="o@example.test", password="x"
            )
        )
        response = other.post(
            reverse("admin:firmwares_brand_import_confirm", args=[token])
        )
        assert response.status_code == 404
        assert not Brand.objects.exists()

    def test_firmware_import_needs_the_pending_submission_permission(self):
        client, _u = staff_client("view_officialfirmware", "add_officialfirmware")
        response = client.get(reverse("admin:firmwares_officialfirmware_import"))
        assert response.status_code == 404


class TestImportChipsetsAndModels:
    def test_chipsets_are_validated_with_the_allow_list(self):
        client, _user = staff_client(superuser=True)
        data = csv_bytes(
            ["vendor", "name", "aliases"],
            [
                ["Allwinner", "A133", "sun50i-a133"],
                ["Intel", "Atom Z3560", ""],
                ["RDA Microelectronics", "RDA8810PL", ""],
            ],
        )

        preview = upload(client, "c.csv", data, model="chipset")

        html = preview.content.decode()
        assert "2 to create" in html and "1 with errors" in html
        assert "not a supported smartphone or tablet chipset vendor" in html
        assert not Chipset.objects.exists()

        brand_rows = csv_bytes(
            ["vendor", "name"],
            [["Allwinner", "A133"], ["RDA Microelectronics", "RDA8810PL"]],
        )
        confirm(
            client,
            upload(client, "c.csv", brand_rows, model="chipset"),
            model="chipset",
        )
        assert set(Chipset.objects.values_list("slug", "vendor")) == {
            ("allwinner-a133", "Allwinner"),
            ("unisoc-rda8810pl", "Unisoc"),
        }
        # Generated vendor categories follow the chipsets.
        assert FlashingToolCategory.objects.filter(vendor="Allwinner").exists()

    def test_a_wrong_chipset_slug_is_reported(self):
        _client, user = staff_client(superuser=True)
        with pytest.raises(ImportRejected) as rejected:
            apply_import(
                ChipsetResource(user),
                [{"vendor": "MediaTek", "name": "Helio G99", "slug": "wrong"}],
                user=user,
                filename="c.csv",
            )
        assert "should be" in rejected.value.plan[0].errors[0]

    def test_models_resolve_their_brand_chipset_and_model_code(self):
        Brand.objects.create(name="Acme", slug="acme")
        client, _user = staff_client(superuser=True)
        header = ["brand", "name", "model_code", "chipset", "release_date"]
        data = csv_bytes(
            header,
            [
                ["acme", "Phone One", "AC-1", "Qualcomm Snapdragon 680", "2024-05-01"],
                ["ghost", "Phone Two", "", "", ""],
                ["acme", "Phone Three", "", "Intel Atom", ""],
                ["acme", "Phone Four", "", "", "May 2024"],
            ],
        )

        preview = upload(client, "m.csv", data, model="model")
        html = preview.content.decode()
        assert "1 to create" in html and "3 with errors" in html
        assert "no brand with the slug" in html and "not a supported" in html
        assert "ISO date" in html

        ok = csv_bytes(
            header,
            [["acme", "Phone One", "AC-1", "Qualcomm Snapdragon 680", "2024-05-01"]],
        )
        confirm(client, upload(client, "m.csv", ok, model="model"), model="model")
        model = Model.objects.get(brand__slug="acme", slug="phone-one")
        assert model.chipset_ref is not None and model.chipset_ref.vendor == "Qualcomm"
        # The same model code with no slug matches the existing model: unchanged.
        again = csv_bytes(
            header,
            [["acme", "Phone One", "AC-1", "Qualcomm Snapdragon 680", "2024-05-01"]],
        )
        assert (
            "1 unchanged"
            in upload(client, "m.csv", again, model="model").content.decode()
        )

    def test_a_bulk_import_refreshes_each_automated_post_target_once(
        self, django_capture_on_commit_callbacks
    ):
        brand = Brand.objects.create(name="Acme", slug="acme")
        _client, user = staff_client(superuser=True)
        rows = [
            {"brand": "acme", "name": f"Phone {i}", "chipset": "MediaTek Helio G99"}
            for i in range(4)
        ]
        runs = []

        with (
            patch(
                "apps.firmwares.signal_handlers._run_blog_sync",
                side_effect=lambda models, brands, chipsets: runs.append(
                    (set(models), set(brands), set(chipsets))
                ),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            from apps.firmwares.admin_io import ModelResource

            apply_import(ModelResource(user), rows, user=user, filename="m.csv")

        assert len(runs) == 1
        models, brands, chipsets = runs[0]
        assert len(models) == 4 and brands <= {brand.pk} and len(chipsets) == 1


class TestImportFirmware:
    def make_device(self, chipset="Qualcomm Snapdragon 680"):
        brand = Brand.objects.create(name="Acme", slug="acme")
        model = Model.objects.create(
            brand=brand, name="One", slug="one", chipset=chipset
        )
        return brand, model

    HEADER = ("external_url", "brand", "model", "build_number", "package_format")

    def test_imported_firmware_is_pending_and_never_verified(self):
        self.make_device()
        client, user = staff_client(superuser=True)
        url = "https://downloads.example.invalid/acme/one/build-1.zip"
        data = csv_bytes(self.HEADER, [[url, "acme", "one", "B1", "qfil_package"]])

        with patch(
            "apps.firmwares.admin_io.validate_external_url", side_effect=lambda u: u
        ):
            preview = upload(client, "f.csv", data, model="officialfirmware")
            assert "1 to create" in preview.content.decode()
            confirm(client, preview, model="officialfirmware")

        pending = PendingFirmware.objects.get(external_url=url)
        assert pending.admin_decision == "pending" and pending.uploader_id == user.pk
        assert pending.package_format == "qfil_package"
        assert pending.metadata["build_number"] == "B1"
        assert not OfficialFirmware.objects.exists()

    def test_a_format_of_another_chipset_vendor_is_rejected(self):
        self.make_device()
        client, _user = staff_client(superuser=True)
        url = "https://downloads.example.invalid/acme/one/build-1.zip"
        data = csv_bytes(self.HEADER, [[url, "acme", "one", "B1", "scatter_firmware"]])
        with patch(
            "apps.firmwares.admin_io.validate_external_url", side_effect=lambda u: u
        ):
            html = upload(
                client, "f.csv", data, model="officialfirmware"
            ).content.decode()
        assert "1 with errors" in html and "Qualcomm" in html
        assert not PendingFirmware.objects.exists()

    def test_hosts_outside_the_allow_list_and_unknown_models_are_rejected(self):
        self.make_device()
        client, _user = staff_client(superuser=True)
        hosts = csv_bytes(
            self.HEADER,
            [["https://evil.example/x.zip", "acme", "one", "B1", "unknown"]],
        )
        html = upload(client, "f.csv", hosts, model="officialfirmware").content.decode()
        assert "1 with errors" in html and "not allowed from this host" in html
        models = csv_bytes(
            self.HEADER,
            [
                [
                    "https://downloads.example.invalid/y.zip",
                    "acme",
                    "missing",
                    "B1",
                    "unknown",
                ]
            ],
        )
        with patch(
            "apps.firmwares.admin_io.validate_external_url", side_effect=lambda u: u
        ):
            html = upload(
                client, "f.csv", models, model="officialfirmware"
            ).content.decode()
        assert "1 with errors" in html and "unknown model slug" in html
        assert not PendingFirmware.objects.exists()

    def test_reimporting_a_known_link_changes_nothing(self):
        brand, model = self.make_device()
        url = "https://downloads.example.invalid/acme/one/build-1.zip"
        OfficialFirmware.objects.create(
            original_file_name="build-1.zip",
            source_type="external",
            external_url=url,
            brand=brand,
            model=model,
            is_verified=True,
            build_number="OLD",
        )
        client, _user = staff_client(superuser=True)
        data = csv_bytes(self.HEADER, [[url, "acme", "one", "NEW", "unknown"]])
        with patch(
            "apps.firmwares.admin_io.validate_external_url", side_effect=lambda u: u
        ):
            html = upload(
                client, "f.csv", data, model="officialfirmware"
            ).content.decode()
        assert "1 unchanged" in html and "Confirm and import" not in html
        assert OfficialFirmware.objects.get().build_number == "OLD"

    def test_a_model_with_variants_needs_the_variant_column(self):
        _brand, model = self.make_device()
        Variant.objects.create(model=model, name="Global", slug="global")
        client, _user = staff_client(superuser=True)
        url = "https://downloads.example.invalid/a.zip"
        data = csv_bytes(self.HEADER, [[url, "acme", "one", "B1", "unknown"]])
        with patch(
            "apps.firmwares.admin_io.validate_external_url", side_effect=lambda u: u
        ):
            html = upload(
                client, "f.csv", data, model="officialfirmware"
            ).content.decode()
        assert "this model has variants" in html


class TestImportToolsCategoriesAndGuides:
    TOOL_HEADER = (
        "slug",
        "name",
        "category",
        "tool_type",
        "official_url",
        "brands",
        "chipsets",
    )

    def test_tools_are_imported_with_links_and_cracks_are_refused(self):
        FlashingToolCategory.objects.create(name="OEM", slug="oem")
        Brand.objects.create(name="Acme", slug="acme")
        Chipset.objects.create(
            vendor="MediaTek", name="Helio G99", slug="mediatek-helio-g99"
        )
        client, _user = staff_client(superuser=True)
        data = csv_bytes(
            self.TOOL_HEADER,
            [
                [
                    "acme-tool",
                    "Acme Tool",
                    "oem",
                    "oem",
                    "https://acme.example/t",
                    "acme",
                    "mediatek-helio-g99",
                ],
                ["cracked", "Cracked", "oem", "crack", "", "", ""],
                ["bad-brand", "Bad", "oem", "oem", "", "ghost", ""],
            ],
        )

        html = upload(client, "t.csv", data, model="flashingtool").content.decode()
        assert "1 to create" in html and "2 with errors" in html
        assert "cracked or patched tools are not accepted" in html
        assert "unknown brand slug" in html
        assert not FlashingTool.objects.exists()

        ok = csv_bytes(
            self.TOOL_HEADER,
            [
                data_row
                for data_row in [
                    [
                        "acme-tool",
                        "Acme Tool",
                        "oem",
                        "oem",
                        "https://acme.example/t",
                        "acme",
                        "mediatek-helio-g99",
                    ]
                ]
            ],
        )
        confirm(
            client,
            upload(client, "t.csv", ok, model="flashingtool"),
            model="flashingtool",
        )
        tool = FlashingTool.objects.get(slug="acme-tool")
        assert tool.download_url == ""
        assert [b.slug for b in tool.supported_brands.all()] == ["acme"]
        assert [c.slug for c in tool.chipsets.all()] == ["mediatek-helio-g99"]
        assert (
            "1 unchanged"
            in upload(client, "t.csv", ok, model="flashingtool").content.decode()
        )

    def test_categories_import_creates_general_ones_and_protects_generated_ones(self):
        from apps.firmwares.tool_categories import sync_reference_categories

        sync_reference_categories()
        client, _user = staff_client(superuser=True)
        header = ["slug", "name", "description", "sort_order", "parent"]
        data = csv_bytes(
            header,
            [
                ["my-tools", "My tools", "Mine", "5", ""],
                ["chipsets-apple", "Renamed", "Edited description", "7", ""],
            ],
        )
        confirm(
            client,
            upload(client, "c.csv", data, model="flashingtoolcategory"),
            model="flashingtoolcategory",
        )

        mine = FlashingToolCategory.objects.get(slug="my-tools")
        assert (mine.kind, mine.is_auto, mine.sort_order) == ("general", False, 5)
        apple = FlashingToolCategory.objects.get(slug="chipsets-apple")
        assert apple.description == "Edited description" and apple.sort_order == 7
        assert apple.name == "Apple chipsets" and apple.is_auto

    def test_guide_templates_never_enable_auto_generation(self):
        Brand.objects.create(name="Acme", slug="acme")
        client, _user = staff_client(superuser=True)
        header = [
            "title_template",
            "guide_type",
            "brand",
            "body_template",
            "auto_generate",
        ]
        bad = csv_bytes(
            header, [["How to {brand}", "stock_flash", "acme", "{steps}", "true"]]
        )
        assert (
            "cannot be enabled by an import"
            in upload(
                client, "g.csv", bad, model="flashingguidetemplate"
            ).content.decode()
        )
        good = csv_bytes(
            header, [["How to {brand}", "stock_flash", "acme", "{steps}", ""]]
        )
        confirm(
            client,
            upload(client, "g.csv", good, model="flashingguidetemplate"),
            model="flashingguidetemplate",
        )
        template = FlashingGuideTemplate.objects.get()
        assert template.auto_generate is False and template.generated_count == 0
        assert (
            "1 unchanged"
            in upload(
                client, "g.csv", good, model="flashingguidetemplate"
            ).content.decode()
        )

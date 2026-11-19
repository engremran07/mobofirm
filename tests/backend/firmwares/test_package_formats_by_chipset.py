"""Phone/tablet chipset allow-list and package formats by chipset vendor.

Covers ``apps.firmwares.package_formats`` (the single source of truth), the model,
service and form validation built on it, the HTMX package-format select of the upload
page, the Apple restore tools and guides, the "Service / Engineering" wording and the
``prune_unsupported_chipsets`` command.
"""

from __future__ import annotations

import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.firmwares import blog_automation, guide_service
from apps.firmwares.catalog import FIRMWARE_TYPE_DISPLAY
from apps.firmwares.chipsets import (
    SUPPORTED_CHIPSET_VENDORS,
    canonical_vendor,
    normalize_chipset,
    resolve_chipset,
)
from apps.firmwares.constants import MAIN_CATEGORIES
from apps.firmwares.models import (
    Brand,
    Chipset,
    EngineeringFirmware,
    FirmwarePackageFormat,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
    PendingFirmware,
    Variant,
)
from apps.firmwares.package_formats import (
    GENERIC_FORMATS,
    VENDOR_PROFILES,
    allowed_formats,
    default_format,
    format_choices,
    tool_for_format,
)
from apps.firmwares.seed_data import APPLE_BRAND, CHIPSETS, GUIDES, TOOLS
from apps.firmwares.services import (
    approve_pending_firmware,
    handle_upload,
    submit_external_firmware,
)
from apps.firmwares.tool_matcher import (
    get_guide_tools_for_template,
    is_tool_compatible,
)

F = FirmwarePackageFormat


@pytest.fixture(autouse=True)
def stage(settings):
    with (
        tempfile.TemporaryDirectory() as directory,
        patch(
            "apps.firmwares.ai_analysis.FirmwareAIAnalyzer._ai_based_analysis",
            return_value={"confidence": 0.0, "method": "ai"},
        ),
    ):
        settings.FIRMWARE_STORAGE_ROOT = Path(directory)
        yield


@pytest.fixture(name="device")
def device_factory(request):
    """Create ``(brand, model)`` for a chipset text (resolved to a Chipset row)."""
    request.getfixturevalue("db")

    def factory(brand_name: str, chipset: str):
        brand = Brand.objects.get_or_create(
            slug=brand_name.lower(), defaults={"name": brand_name}
        )[0]
        model = Model.objects.create(
            brand=brand,
            name=f"{chipset} phone",
            slug=f"{chipset}-phone".lower().replace(" ", "-"),
            chipset=chipset,
        )
        return brand, model

    return factory


def client_for(user):
    client = Client()
    client.force_login(user)
    return client


def pending_for(model, package_format, **extra):
    return PendingFirmware(
        original_file_name="build.zip",
        stored_file_path="/f/build.zip",
        uploaded_model=model,
        uploaded_brand=model.brand,
        package_format=package_format,
        **extra,
    )


class TestAllowList:
    def test_only_phone_and_tablet_vendors_are_supported(self):
        assert set(SUPPORTED_CHIPSET_VENDORS) == {
            "MediaTek",
            "Qualcomm",
            "Samsung",
            "Unisoc",
            "HiSilicon",
            "Google",
            "Rockchip",
            "Allwinner",
            "Apple",
        }

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "raw", ["Intel Atom Z3560", "Intel Core i5", "Acme X1 Pro", "Nvidia Tegra X1"]
    )
    def test_other_vendors_never_resolve_to_a_chipset(self, raw):
        assert normalize_chipset(raw) is None
        assert resolve_chipset(raw) is None
        assert not Chipset.objects.exists()

    @pytest.mark.parametrize(
        ("raw", "vendor", "family"),
        [
            ("Allwinner A133", "Allwinner", "Allwinner A"),
            ("Allwinner A523", "Allwinner", "Allwinner A"),
            ("Allwinner T507", "Allwinner", "Allwinner T"),
            ("RDA Microelectronics RDA8810PL", "Unisoc", "RDA"),
            ("RDA8810PL", "Unisoc", "RDA"),
            ("Spreadtrum SC9863A", "Unisoc", ""),
            ("Unisoc T618", "Unisoc", "Unisoc T"),
            ("Rockchip RK3566", "Rockchip", ""),
            ("Google Tensor G4", "Google", "Tensor"),
            ("HiSilicon Kirin 990 5G", "HiSilicon", "Kirin"),
            ("Exynos 2400", "Samsung", "Exynos"),
            ("Apple A17 Pro", "Apple", "A-series"),
            ("Apple A15 Bionic", "Apple", "A-series"),
            ("Apple M2", "Apple", "M-series"),
        ],
    )
    def test_vendor_and_family_are_normalised(self, raw, vendor, family):
        normalized = normalize_chipset(raw)
        assert normalized is not None
        assert (normalized.vendor, normalized.family) == (vendor, family)

    def test_the_allwinner_t_series_is_not_a_unisoc_part(self):
        allwinner = normalize_chipset("Allwinner T507")
        unisoc = normalize_chipset("Unisoc T606")
        assert allwinner is not None and unisoc is not None
        assert (allwinner.vendor, unisoc.vendor) == ("Allwinner", "Unisoc")

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("RDA", "Unisoc"),
            ("Spreadtrum", "Unisoc"),
            ("Apple", "Apple"),
            ("Intel", ""),
        ],
    )
    def test_vendor_aliases(self, label, expected):
        assert canonical_vendor(label) == expected

    def test_the_seed_catalogue_covers_every_allowed_vendor_and_only_those(self):
        vendors = {chip.vendor for chip in CHIPSETS}
        assert vendors == set(SUPPORTED_CHIPSET_VENDORS)
        names = {chip.name for chip in CHIPSETS if chip.vendor == "Allwinner"}
        assert {"A133", "A523", "T507"} <= names
        assert any(chip.family == "RDA" for chip in CHIPSETS)
        apple = {chip.name for chip in CHIPSETS if chip.vendor == "Apple"}
        assert {"A12 Bionic", "A17 Pro", "M1", "M4"} <= apple


class TestFormatsByVendor:
    @pytest.mark.parametrize(
        ("vendor", "expected"),
        [
            ("MediaTek", {F.SCATTER_FIRMWARE, F.MTK_DOWNLOAD_AGENT}),
            ("Qualcomm", {F.QFIL_PACKAGE, F.FASTBOOT_ROM}),
            ("Samsung", {F.ODIN_PACKAGE}),
            ("Unisoc", {F.PAC_PACKAGE}),
            ("Rockchip", {F.ROCKCHIP_UPDATE_IMG}),
            ("Allwinner", {F.ALLWINNER_IMG}),
            ("HiSilicon", {F.KIRIN_UPDATE_APP}),
            ("Google", {F.FACTORY_IMAGE, F.FULL_OTA}),
            ("Apple", {F.APPLE_IPSW, F.APPLE_OTA}),
        ],
    )
    def test_each_vendor_offers_its_own_format_and_the_generic_ones(
        self, vendor, expected
    ):
        allowed = set(allowed_formats(vendor))
        assert expected <= allowed
        assert set(GENERIC_FORMATS) <= allowed
        assert F.UNKNOWN in allowed
        assert VENDOR_PROFILES[vendor].default in expected

    def test_vendor_formats_do_not_leak_to_other_vendors(self):
        assert F.SCATTER_FIRMWARE not in allowed_formats("Qualcomm")
        assert F.QFIL_PACKAGE not in allowed_formats("MediaTek")
        assert F.APPLE_IPSW not in allowed_formats("MediaTek")
        assert F.ODIN_PACKAGE not in allowed_formats("Unisoc")
        assert F.PAC_PACKAGE not in allowed_formats("Apple")

    def test_an_unknown_chipset_allows_every_format(self):
        assert set(allowed_formats("")) == set(F.values)

    def test_samsung_devices_use_odin_whatever_the_soc(self):
        assert F.ODIN_PACKAGE in allowed_formats("Qualcomm", "samsung")
        assert default_format("Qualcomm", "samsung") == F.ODIN_PACKAGE
        assert F.ODIN_PACKAGE not in allowed_formats("Qualcomm", "xiaomi")

    def test_apple_brand_alone_selects_the_apple_formats(self):
        assert F.APPLE_IPSW in allowed_formats("", "apple")
        assert default_format("", "apple") == F.APPLE_IPSW

    def test_choices_start_with_not_specified_and_keep_the_stored_values(self):
        choices = format_choices("MediaTek")
        assert choices[0][0] == F.UNKNOWN
        assert choices[1][0] == F.SCATTER_FIRMWARE
        assert F.RECOVERY_ROM.value == "recovery_rom"
        assert F.SERVICE_PACKAGE.value == "service_package"

    def test_formats_map_to_their_flashing_tools(self):
        def slug_of(package_format):
            tool = tool_for_format(package_format)
            assert tool is not None
            return tool.slug

        assert slug_of(F.SCATTER_FIRMWARE) == "sp-flash-tool"
        assert slug_of(F.QFIL_PACKAGE) == "qpst-qfil"
        assert slug_of(F.ODIN_PACKAGE) == "odin"
        assert slug_of(F.PAC_PACKAGE) == "spd-researchdownload"
        assert slug_of(F.ROCKCHIP_UPDATE_IMG) == "rkdevtool"
        assert slug_of(F.ALLWINNER_IMG) == "phoenixsuit"
        assert slug_of(F.KIRIN_UPDATE_APP) == "huawei-hisuite"
        assert slug_of(F.APPLE_IPSW) == "apple-finder-devices-restore"
        assert tool_for_format(F.UNKNOWN) is None
        seeded = {tool.slug for tool in TOOLS}
        assert {
            "spd-researchdownload",
            "rkdevtool",
            "phoenixsuit",
            "apple-finder-devices-restore",
        } <= seeded


@pytest.mark.django_db
class TestValidation:
    def test_scatter_is_rejected_for_a_qualcomm_model(self, device):
        _, model = device("Acme", "Qualcomm Snapdragon 680")

        with pytest.raises(ValidationError) as error:
            pending_for(model, F.SCATTER_FIRMWARE).clean()

        message = " ".join(error.value.message_dict["package_format"])
        assert "Qualcomm" in message and "QFIL" in message

    def test_qfil_is_accepted_for_a_qualcomm_model(self, device):
        _, model = device("Acme", "Qualcomm Snapdragon 680")
        pending_for(model, F.QFIL_PACKAGE).clean()
        pending_for(model, F.FASTBOOT_ROM).clean()

    def test_scatter_is_accepted_for_a_mediatek_model(self, device):
        _, model = device("Acme", "MediaTek Helio G99")
        pending_for(model, F.SCATTER_FIRMWARE).clean()

    def test_qfil_is_rejected_for_a_mediatek_model(self, device):
        _, model = device("Acme", "MediaTek Helio G99")
        with pytest.raises(ValidationError):
            pending_for(model, F.QFIL_PACKAGE).clean()

    def test_the_chipset_row_of_the_model_decides(self, device):
        _, model = device("Acme", "")
        model.chipset_ref = Chipset.objects.create(
            vendor="Allwinner", name="A133", slug="allwinner-a133"
        )
        model.save()
        pending_for(model, F.ALLWINNER_IMG).clean()
        with pytest.raises(ValidationError):
            pending_for(model, F.PAC_PACKAGE).clean()

    def test_allwinner_and_rda_text_normalise_to_their_vendor_formats(self, device):
        _, allwinner = device("Boardco", "Allwinner A133")
        _, rda = device("Featureco", "RDA Microelectronics RDA8810PL")
        pending_for(allwinner, F.ALLWINNER_IMG).clean()
        pending_for(rda, F.PAC_PACKAGE).clean()
        with pytest.raises(ValidationError):
            pending_for(rda, F.ALLWINNER_IMG).clean()

    def test_apple_ipsw_is_accepted_and_android_formats_are_rejected(self, device):
        _, model = device("Apple", "Apple A17 Pro")
        pending_for(model, F.APPLE_IPSW).clean()
        pending_for(model, F.APPLE_OTA).clean()
        with pytest.raises(ValidationError):
            pending_for(model, F.SCATTER_FIRMWARE).clean()

    def test_ipsw_is_rejected_for_an_android_model(self, device):
        _, model = device("Acme", "MediaTek Helio G99")
        with pytest.raises(ValidationError):
            pending_for(model, F.APPLE_IPSW).clean()

    def test_unknown_and_service_formats_stay_valid_everywhere(self, device):
        _, model = device("Acme", "Qualcomm Snapdragon 680")
        pending_for(model, F.UNKNOWN).clean()
        pending_for(model, F.SERVICE_PACKAGE).clean()

    def test_the_firmware_record_validates_like_the_pending_upload(self, device):
        brand, model = device("Acme", "Qualcomm Snapdragon 680")
        firmware = OfficialFirmware(
            original_file_name="x.zip",
            stored_file_path="/f/x.zip",
            brand=brand,
            model=model,
            package_format=F.SCATTER_FIRMWARE,
        )
        with pytest.raises(ValidationError) as error:
            firmware.clean()
        assert "package_format" in error.value.message_dict

    def test_upload_service_rejects_the_wrong_format_before_storing_a_file(
        self, device, settings
    ):
        brand, model = device("Acme", "Qualcomm Snapdragon 680")
        with pytest.raises(ValueError, match="Qualcomm"):
            handle_upload(
                uploader=None,
                uploaded_brand=brand,
                uploaded_model=model,
                uploaded_variant=None,
                file_obj=SimpleUploadedFile("a.zip", b"data"),
                is_password_protected=False,
                password=None,
                package_format=F.SCATTER_FIRMWARE,
            )
        assert not (Path(settings.FIRMWARE_STORAGE_ROOT) / "pending").exists()
        assert not PendingFirmware.objects.exists()

    def test_external_submission_rejects_the_wrong_format(self, device):
        brand, model = device("Acme", "MediaTek Helio G99")
        with pytest.raises(ValueError, match="MediaTek"):
            submit_external_firmware(
                uploader=None,
                url="https://example.com/a.zip",
                original_file_name="a.zip",
                uploaded_brand=brand,
                uploaded_model=model,
                package_format=F.QFIL_PACKAGE,
            )

    def test_approval_rejects_a_format_that_does_not_fit_the_device(
        self, device, make_user
    ):
        brand, model = device("Acme", "Qualcomm Snapdragon 680")
        pending = PendingFirmware.objects.create(
            original_file_name="a.zip",
            source_type="external",
            external_url="https://example.com/a.zip",
            uploaded_brand=brand,
            uploaded_model=model,
            package_format=F.UNKNOWN,
        )
        reviewer = make_user("format-reviewer", staff=True, superuser=True)
        with pytest.raises(ValidationError):
            approve_pending_firmware(
                pending,
                reviewer,
                category="official",
                package_format=F.SCATTER_FIRMWARE,
            )
        pending.refresh_from_db()
        assert pending.admin_decision == "pending"


@pytest.mark.django_db
class TestUploadFormat:
    def test_the_fragment_filters_the_format_select_by_the_models_chipset(
        self, make_user, device
    ):
        _, qualcomm = device("Acme", "Qualcomm Snapdragon 680")
        _, mediatek = device("Beta", "MediaTek Helio G99")
        client = client_for(make_user("format-fragment"))
        url = reverse("firmwares:upload_variant_field")

        for_qualcomm = client.get(url, {"model": qualcomm.pk}).content.decode()
        for_mediatek = client.get(url, {"model": mediatek.pk}).content.decode()
        waiting = client.get(url).content.decode()

        assert 'id="upload-format-region"' in for_qualcomm
        assert 'hx-swap-oob="true"' in for_qualcomm
        assert 'value="qfil_package"' in for_qualcomm
        assert 'value="scatter_firmware"' not in for_qualcomm
        assert 'value="odin_package"' not in for_qualcomm
        assert 'value="scatter_firmware"' in for_mediatek
        assert 'value="qfil_package"' not in for_mediatek
        assert 'value="recovery_rom"' in for_mediatek
        assert 'value="scatter_firmware"' in waiting
        assert 'value="qfil_package"' in waiting

    def test_the_fragment_preselects_the_default_format(self, make_user, device):
        _, model = device("Acme", "MediaTek Helio G99")
        html = (
            client_for(make_user("format-default"))
            .get(reverse("firmwares:upload_variant_field"), {"model": model.pk})
            .content.decode()
        )
        assert 'value="scatter_firmware" selected' in html

    def test_the_page_renders_the_filtered_select_for_a_preselected_model(
        self, make_user, device
    ):
        _, model = device("Acme", "Qualcomm Snapdragon 680")
        page = client_for(make_user("format-page")).get(
            reverse("firmwares:contribute"), {"model": model.pk}
        )
        html = page.content.decode()
        assert html.count('id="upload-format-region"') == 1
        assert 'value="qfil_package"' in html
        assert 'value="scatter_firmware"' not in html

    def test_a_mismatched_format_is_a_field_error_not_a_pending_upload(
        self, make_user, device
    ):
        brand, model = device("Acme", "Qualcomm Snapdragon 680")
        response = client_for(make_user("format-mismatch")).post(
            reverse("firmwares:upload"),
            {
                "brand": brand.pk,
                "model": model.pk,
                "source": "file",
                "file": SimpleUploadedFile(
                    "build.zip", b"firmware", content_type="application/zip"
                ),
                "build_number": "B1",
                "package_format": F.SCATTER_FIRMWARE,
            },
        )
        assert response.status_code == 200
        assert "is not a package format for Qualcomm" in strip_tags(
            response.content.decode()
        ).replace("&ldquo;", "“")
        assert not PendingFirmware.objects.exists()

    def test_a_matching_format_is_submitted_and_redirects(self, make_user, device):
        brand, model = device("Acme", "Qualcomm Snapdragon 680")
        response = client_for(make_user("format-match")).post(
            reverse("firmwares:upload"),
            {
                "brand": brand.pk,
                "model": model.pk,
                "source": "file",
                "file": SimpleUploadedFile(
                    "build.zip", b"firmware", content_type="application/zip"
                ),
                "build_number": "B1",
                "package_format": F.QFIL_PACKAGE,
            },
        )
        assert response.status_code == 302
        assert PendingFirmware.objects.get().package_format == F.QFIL_PACKAGE

    def test_an_apple_ipsw_upload_is_accepted(self, make_user, device):
        brand, model = device("Apple", "Apple A17 Pro")
        response = client_for(make_user("ipsw-uploader")).post(
            reverse("firmwares:upload"),
            {
                "brand": brand.pk,
                "model": model.pk,
                "source": "file",
                "file": SimpleUploadedFile(
                    "iPhone16,1_17.0_Restore.ipsw",
                    b"ipsw",
                    content_type="application/octet-stream",
                ),
                "build_number": "21A329",
                "package_format": F.APPLE_IPSW,
            },
        )
        assert response.status_code == 302
        assert PendingFirmware.objects.get().package_format == F.APPLE_IPSW


@pytest.mark.django_db
class TestFirmwarePages:
    def test_the_detail_page_shows_the_format_and_its_tool(self, device):
        brand, model = device("Acme", "MediaTek Helio G99")
        category = FlashingToolCategory.objects.create(name="Chip", slug="chip-tools")
        FlashingTool.objects.create(
            category=category, name="SP Flash Tool", slug="sp-flash-tool"
        )
        firmware = OfficialFirmware.objects.create(
            original_file_name="MT6789_Android_scatter.zip",
            stored_file_path="/f/scatter.zip",
            brand=brand,
            model=model,
            is_verified=True,
            package_format=F.SCATTER_FIRMWARE,
        )

        html = (
            Client()
            .get(
                reverse(
                    "firmwares:firmware_detail",
                    kwargs={
                        "firmware_type": "official",
                        "firmware_id": str(firmware.pk),
                    },
                )
            )
            .content.decode()
        )

        assert "Scatter firmware (SP Flash Tool, MediaTek)" in html
        assert "Flashing tool for this format" in html
        assert (
            reverse("firmwares:flashing_tool_detail", kwargs={"slug": "sp-flash-tool"})
            in html
        )


@pytest.mark.django_db
class TestServiceEngineeringWording:
    def test_user_visible_labels_say_service_engineering(self):
        assert (
            EngineeringFirmware._meta.verbose_name == "Service / Engineering firmware"
        )
        assert dict(MAIN_CATEGORIES)["engineering"] == "Service / Engineering Firmware"
        assert FIRMWARE_TYPE_DISPLAY["engineering"] == "Service / Engineering"
        assert FIRMWARE_TYPE_DISPLAY["engineeringfirmware"] == "Service / Engineering"
        assert F.SERVICE_PACKAGE.label == "Service / Engineering package"

    def test_stored_values_and_urls_are_unchanged(self):
        assert F.SERVICE_PACKAGE.value == "service_package"
        assert dict(MAIN_CATEGORIES).keys() >= {"engineering"}
        assert EngineeringFirmware._meta.db_table == "firmwares_engineeringfirmware"

    def test_the_browse_page_filters_by_the_unchanged_slug(self, device):
        brand, model = device("Acme", "MediaTek Helio G99")
        EngineeringFirmware.objects.create(
            original_file_name="svc.zip",
            stored_file_path="/f/svc.zip",
            brand=brand,
            model=model,
            is_verified=True,
            package_format=F.SERVICE_PACKAGE,
        )
        html = Client().get(reverse("firmwares:browse"), {"type": "engineering"})
        assert html.status_code == 200
        assert "svc.zip" in html.content.decode()
        assert "Service / Engineering" in html.content.decode()


class TestApple:
    def test_apple_tools_state_the_signed_ipsw_limit_and_no_bypass(self):
        apple = {tool.slug: tool for tool in TOOLS if "apple" in tool.brands}
        assert {
            "apple-finder-devices-restore",
            "apple-itunes",
            "apple-configurator-2",
            "idevicerestore",
            "futurerestore",
        } == set(apple)
        for tool in apple.values():
            text = f"{tool.description} {tool.instructions}".lower()
            assert "signs" in text or "signed" in text or "signing" in text
        assert "shsh" in apple["futurerestore"].description.lower()
        assert "Windows" in " ".join(apple["apple-finder-devices-restore"].requirements)
        assert "iCloud" not in " ".join(
            tool.name for tool in TOOLS
        )  # no bypass tool is catalogued
        for tool in TOOLS:
            assert "bypass tool" not in tool.name.lower()

    def test_apple_guides_cover_restore_dfu_recovery_configurator_and_ota(self):
        titles = " | ".join(g.title for g in GUIDES if g.brand == "apple").lower()
        for needle in (
            "finder or the apple devices app",
            "dfu mode",
            "recovery mode",
            "apple configurator 2",
            "ota update or ipsw",
            "downgrade",
        ):
            assert needle in titles
        assert not any(
            g.guide_type
            in {
                FlashingGuideTemplate.GuideType.FRP_BYPASS,
                FlashingGuideTemplate.GuideType.ROOT,
                FlashingGuideTemplate.GuideType.CUSTOM_ROM,
                FlashingGuideTemplate.GuideType.UNLOCK_BOOTLOADER,
            }
            for g in GUIDES
            if g.brand == "apple"
        )

    def test_the_apple_brand_seed_has_ios_and_ipados_models_without_firmware(self):
        assert APPLE_BRAND.slug == "apple"
        labels = {seed.os_label.split()[0] for seed in APPLE_BRAND.models}
        assert {"iOS", "iPadOS"} <= labels
        assert all(seed.android == 0 for seed in APPLE_BRAND.models)
        assert any(seed.chipset.endswith("-m4") for seed in APPLE_BRAND.models)

    def test_android_tools_are_not_compatible_with_apple_and_vice_versa(self):
        assert not is_tool_compatible("Odin", "Apple")
        assert not is_tool_compatible("SP Flash Tool", "Apple")
        assert is_tool_compatible("Finder / Apple Devices app", "Apple")
        assert is_tool_compatible("Apple Configurator 2", "Apple")
        assert not is_tool_compatible("Apple Configurator 2", "Samsung")
        assert is_tool_compatible("SP Flash Tool", "Infinix")

    def test_apple_guide_tools_are_apple_tools_only(self):
        tools = get_guide_tools_for_template("stock_flash", "Apple", "Apple A17 Pro")
        assert tools[0] == "Finder / Apple Devices app"
        assert "ADB & Fastboot" not in tools
        assert get_guide_tools_for_template("root", "Apple", "") == []

    def test_the_blog_guide_section_for_apple_names_finder_and_signed_ipsw(self):
        html = str(blog_automation.guide_section("Apple iPhone 15 Pro", "", "Apple"))
        assert "Finder" in html and "DFU" in html
        assert "signed" in html or "signs" in html
        assert "IPSW" in html
        assert "ADB" not in html

    def test_the_blog_guide_section_names_the_package_format_per_vendor(self):
        qualcomm = str(
            blog_automation.guide_section("Acme One", "Qualcomm Snapdragon 680", "Acme")
        )
        mediatek = str(
            blog_automation.guide_section("Acme Two", "MediaTek Helio G99", "Acme")
        )
        assert "rawprogram" in qualcomm and "EDL 9008" in qualcomm
        assert "_Android_scatter.txt" in mediatek

    @pytest.mark.django_db
    def test_a_non_restore_guide_is_never_generated_for_apple(self):
        brand = Brand.objects.create(name="Apple", slug="apple")
        template = FlashingGuideTemplate.objects.create(
            title_template="Bypass {brand}",
            body_template="{steps}",
            guide_type=FlashingGuideTemplate.GuideType.FRP_BYPASS,
            brand=brand,
        )
        assert guide_service.generate_guide_for_brand_model(template, brand) is None

    def test_apple_steps_describe_dfu_per_generation_and_signed_only(self):
        steps = guide_service.build_standard_steps("repair", "Apple")
        assert "DFU" in steps and "iPhone 7" in steps and "iPhone 6s" in steps
        assert "Revive" in steps
        downgrade = guide_service.build_standard_steps("downgrade", "Apple")
        assert "signed" in downgrade and "SHSH" in downgrade
        assert "bypass" not in steps.lower()


@pytest.mark.django_db
class TestPruneUnsupportedChipsets:
    def make_rows(self):
        unused = Chipset.objects.create(
            vendor="Intel", name="Atom Z3560", slug="intel-z"
        )
        used = Chipset.objects.create(vendor="Intel", name="Core i5", slug="intel-i5")
        brand = Brand.objects.create(name="Tabco", slug="tabco")
        Model.objects.create(
            brand=brand, name="Tab", slug="tab", chipset="Intel Core i5"
        )
        # ``chipset`` text is not resolved for an unsupported vendor; reference it.
        Model.objects.filter(slug="tab").update(chipset_ref=used)
        supported = Chipset.objects.create(
            vendor="Apple", family="A-series", name="A17 Pro", slug="apple-a17-pro"
        )
        return unused, used, supported

    def run(self, *args):
        out = StringIO()
        call_command("prune_unsupported_chipsets", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_is_the_default_and_deletes_nothing(self):
        unused, used, supported = self.make_rows()

        output = self.run()

        assert "DRY RUN" in output
        assert "Vendors outside the allow-list: Intel" in output
        assert "Intel Atom Z3560" in output and "unreferenced" in output
        assert "referenced by 1 models" in output
        assert "Apple A17 Pro" not in output.split("Vendors outside")[1]
        assert (
            Chipset.objects.filter(pk__in=[unused.pk, used.pk, supported.pk]).count()
            == 3
        )

    def test_apply_deletes_only_unreferenced_unsupported_rows(self):
        unused, used, supported = self.make_rows()

        output = self.run("--apply")

        assert "APPLY" in output and "Deleted 1 unreferenced" in output
        assert not Chipset.objects.filter(pk=unused.pk).exists()
        assert Chipset.objects.filter(pk=used.pk).exists()
        assert Chipset.objects.filter(pk=supported.pk).exists()
        assert Model.objects.get(slug="tab").chipset_ref_id == used.pk

    def test_apply_is_idempotent(self):
        self.make_rows()
        self.run("--apply")
        output = self.run("--apply")
        assert "Deleted 0 unreferenced" in output

    def test_a_clean_database_reports_nothing_to_do(self):
        Chipset.objects.create(vendor="Apple", name="M2", slug="apple-m2")
        assert "No chipset rows outside the allow-list" in self.run()


@pytest.mark.django_db
class TestSeedPlatformCoverage:
    def test_the_full_catalogue_seeds_apple_chipsets_tools_and_brand(self):
        from apps.firmwares.seeding import (
            seed_brands_and_models,
            seed_chipsets,
            seed_tools,
        )

        chipsets = [chip for chip in CHIPSETS if chip.vendor == "Apple"]
        seed_chipsets(chipsets)
        seed_brands_and_models((APPLE_BRAND,))
        seed_tools()

        assert Chipset.objects.filter(vendor="Apple").count() == len(chipsets)
        assert Brand.objects.filter(slug="apple").exists()
        iphone = Model.objects.get(brand__slug="apple", slug="iphone-15-pro")
        assert iphone.os_version == "iOS 17"
        assert iphone.chipset_ref is not None and iphone.chipset_ref.vendor == "Apple"
        finder = FlashingTool.objects.get(slug="apple-finder-devices-restore")
        assert finder.chipsets.filter(vendor="Apple").exists()
        assert finder.supported_brands.filter(slug="apple").exists()
        # Re-seeding is additive and idempotent.
        seed_tools()
        assert (
            FlashingTool.objects.filter(slug="apple-finder-devices-restore").count()
            == 1
        )
        assert Variant.objects.filter(model=iphone).count() == 1

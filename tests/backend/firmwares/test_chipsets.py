from __future__ import annotations

from django.test import TestCase

from apps.firmwares.chipsets import (
    NormalizedChipset,
    normalize_chipset,
    resolve_chipset,
    vendor_for,
)
from apps.firmwares.models import (
    Brand,
    Chipset,
    FlashingTool,
    FlashingToolCategory,
    Model,
    Variant,
)
from apps.firmwares.tool_matcher import (
    filter_tools_for_brand,
    get_compatible_tools_from_db,
)


def _chipset_of(case: TestCase, device: Model | Variant) -> Chipset:
    chipset = device.chipset_ref
    if chipset is None:
        case.fail(f"{device!r} should reference a chipset.")
    return chipset


class NormalizationTests(TestCase):
    def normalized(self, raw: str) -> NormalizedChipset:
        result = normalize_chipset(raw)
        if result is None:
            self.fail(f"{raw!r} should normalise to a chipset.")
        return result

    def test_vendor_family_and_name_are_extracted(self):
        cases = {
            "Mediatek Helio G100 Ultra (6nm)": (
                "MediaTek",
                "Helio",
                "Helio G100 Ultra",
                "mediatek-helio-g100-ultra",
            ),
            "Qualcomm Snapdragon 8 Gen 3": (
                "Qualcomm",
                "Snapdragon",
                "Snapdragon 8 Gen 3",
                "qualcomm-snapdragon-8-gen-3",
            ),
            "MediaTek MT6739": ("MediaTek", "", "MT6739", "mediatek-mt6739"),
            "Exynos 2400": ("Samsung", "Exynos", "Exynos 2400", "samsung-exynos-2400"),
            "Unisoc T618": ("Unisoc", "Unisoc T", "T618", "unisoc-t618"),
        }
        for raw, (vendor, family, name, slug) in cases.items():
            with self.subTest(raw=raw):
                normalized = self.normalized(raw)
                self.assertEqual(
                    (
                        normalized.vendor,
                        normalized.family,
                        normalized.name,
                        normalized.slug,
                    ),
                    (vendor, family, name, slug),
                )

    def test_noise_empty_and_single_unknown_words_do_not_create_chipsets(self):
        self.assertIsNone(normalize_chipset(""))
        self.assertIsNone(normalize_chipset("   "))
        self.assertIsNone(normalize_chipset("Technology"))
        self.assertEqual(vendor_for("Technology"), "")
        self.assertEqual(vendor_for("Snapdragon 680"), "Qualcomm")

    def test_unknown_vendors_use_their_first_word(self):
        normalized = self.normalized("Acme X1 Pro")

        self.assertEqual((normalized.vendor, normalized.name), ("Acme", "X1 Pro"))


class ResolutionTests(TestCase):
    def test_equivalent_spellings_resolve_to_one_chipset_and_remember_aliases(self):
        first = resolve_chipset("Mediatek Helio G100 Ultra (6nm)")
        second = resolve_chipset("MediaTek Helio G100 Ultra")
        third = resolve_chipset("mediatek helio g100 ultra (6 nm)")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.pk, third.pk)
        self.assertEqual(Chipset.objects.count(), 1)
        first.refresh_from_db()
        self.assertIn("Mediatek Helio G100 Ultra (6nm)", first.aliases)
        self.assertEqual(len(first.aliases), len(set(first.aliases)))

    def test_alias_lookup_finds_a_chipset_with_a_different_normalised_name(self):
        chipset = Chipset.objects.create(
            vendor="MediaTek",
            name="Helio G99",
            slug="mediatek-helio-g99",
            aliases=["MT6789"],
        )

        self.assertEqual(resolve_chipset("MT6789").pk, chipset.pk)
        self.assertEqual(Chipset.objects.count(), 1)

    def test_create_false_never_writes(self):
        self.assertIsNone(resolve_chipset("Qualcomm Snapdragon 680", create=False))
        self.assertFalse(Chipset.objects.exists())


class ModelAndVariantReferenceTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Chip Brand", slug="chip-brand")

    def test_saving_a_model_resolves_its_chipset_reference(self):
        model = Model.objects.create(
            brand=self.brand,
            name="Chip Phone",
            slug="chip-phone",
            chipset="Qualcomm Snapdragon 680 (6 nm)",
        )

        model.refresh_from_db()
        chipset = _chipset_of(self, model)
        self.assertEqual(chipset.slug, "qualcomm-snapdragon-680")
        self.assertEqual(list(chipset.models.all()), [model])

    def test_partial_saves_of_the_chipset_text_still_update_the_reference(self):
        model = Model.objects.create(
            brand=self.brand, name="Chip Phone", slug="chip-phone"
        )
        model.chipset = "MediaTek Dimensity 9300"

        model.save(update_fields=["chipset"])

        model.refresh_from_db()
        self.assertEqual(
            _chipset_of(self, model).display_name, "MediaTek Dimensity 9300"
        )

    def test_unrelated_partial_saves_do_not_touch_the_reference(self):
        model = Model.objects.create(
            brand=self.brand,
            name="Chip Phone",
            slug="chip-phone",
            chipset="Unisoc T618",
        )
        before = model.chipset_ref_id

        model.description = "Updated"
        model.save(update_fields=["description"])

        model.refresh_from_db()
        self.assertEqual(model.chipset_ref_id, before)

    def test_a_reference_without_text_fills_the_display_text(self):
        chipset = Chipset.objects.create(
            vendor="Google", name="Tensor G3", slug="google-tensor-g3"
        )

        model = Model.objects.create(
            brand=self.brand, name="Pixel", slug="pixel", chipset_ref=chipset
        )

        self.assertEqual(model.chipset, "Google Tensor G3")

    def test_variants_resolve_to_the_same_chipset_row(self):
        model = Model.objects.create(
            brand=self.brand,
            name="Chip Phone",
            slug="chip-phone",
            chipset="Qualcomm Snapdragon 680",
        )

        variant = Variant.objects.create(
            model=model, name="Global", slug="global", chipset="Snapdragon 680 4G"
        )

        self.assertEqual(Chipset.objects.filter(vendor="Qualcomm").count(), 2)
        variant.refresh_from_db()
        self.assertEqual(_chipset_of(self, variant).vendor, "Qualcomm")

    def test_deleting_a_chipset_keeps_the_device_and_its_text(self):
        model = Model.objects.create(
            brand=self.brand,
            name="Chip Phone",
            slug="chip-phone",
            chipset="Unisoc T618",
        )

        _chipset_of(self, model).delete()

        model.refresh_from_db()
        self.assertIsNone(model.chipset_ref)
        self.assertEqual(model.chipset, "Unisoc T618")


class ToolMatchingTests(TestCase):
    def setUp(self):
        self.category = FlashingToolCategory.objects.create(
            name="Matching", slug="matching"
        )
        self.mediatek = Chipset.objects.create(
            vendor="MediaTek", name="Helio G99", slug="mediatek-helio-g99"
        )
        self.qualcomm = Chipset.objects.create(
            vendor="Qualcomm", name="Snapdragon 680", slug="qualcomm-snapdragon-680"
        )
        self.mtk_tool = FlashingTool.objects.create(
            category=self.category, name="MTK Tool", slug="mtk-tool"
        )
        self.mtk_tool.chipsets.add(self.mediatek)
        self.qc_tool = FlashingTool.objects.create(
            category=self.category, name="QC Tool", slug="qc-tool"
        )
        self.qc_tool.chipsets.add(self.qualcomm)

    def test_database_matching_uses_the_chipset_relation_on_every_backend(self):
        tools = get_compatible_tools_from_db("Acme", "MediaTek Helio G99")

        self.assertEqual([tool.slug for tool in tools], ["mtk-tool"])

    def test_filtering_a_tool_list_respects_declared_chipsets(self):
        tools = list(FlashingTool.objects.all())

        filtered = filter_tools_for_brand(tools, "Acme", "Qualcomm Snapdragon 680")

        self.assertEqual([tool.slug for tool in filtered], ["qc-tool"])

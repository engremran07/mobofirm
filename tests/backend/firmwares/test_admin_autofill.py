from django.test import SimpleTestCase

from apps.firmwares.admin_autofill import FirmwareAutoFill


class FirmwareAutoFillCacheKeyTests(SimpleTestCase):
    def test_cache_keys_are_valid_deterministic_and_namespaced(self):
        brand_key = FirmwareAutoFill._cache_key("brand_autofill", "Samsung Galaxy")
        model_key = FirmwareAutoFill._cache_key(
            "model_autofill", "Samsung", "Galaxy S24 Ultra"
        )
        variant_key = FirmwareAutoFill._cache_key(
            "variant_autofill", "Samsung", "Galaxy S24 Ultra", "United States"
        )

        self.assertRegex(brand_key, r"^brand_autofill_[0-9a-f]{64}$")
        self.assertRegex(model_key, r"^model_autofill_[0-9a-f]{64}$")
        self.assertRegex(variant_key, r"^variant_autofill_[0-9a-f]{64}$")
        self.assertEqual(
            model_key,
            FirmwareAutoFill._cache_key(
                "model_autofill", "Samsung", "Galaxy S24 Ultra"
            ),
        )
        self.assertEqual(
            len({brand_key, model_key, variant_key}),
            3,
        )

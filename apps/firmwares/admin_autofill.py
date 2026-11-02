"""
Firmware Admin Auto-Fill System
Automatically fills missing fields using AI and internet data
"""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


class FirmwareAutoFill:
    """
    Automatically fills firmware metadata from multiple sources:
    1. AI analysis of firmware file
    2. Internet lookup (GSMArena, etc.)
    3. Database patterns
    """

    # Cache timeout (24 hours)
    CACHE_TIMEOUT = 86400

    @classmethod
    def autofill_brand(cls, brand_name: str) -> dict:
        """
        Auto-fill brand information from internet sources
        """
        cache_key = f"brand_autofill_{brand_name.lower()}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        try:
            # Try GSMArena API
            data = cls._fetch_from_gsmarena(brand_name)
            if not data:
                # Fallback to AI
                data = cls._generate_with_ai(f"Brand: {brand_name}")

            cache.set(cache_key, data, cls.CACHE_TIMEOUT)
            return data
        except Exception as e:
            logger.exception(f"Error autofilling brand {brand_name}: {e}")
            return {}

    @classmethod
    def autofill_model(cls, brand_name: str, model_name: str) -> dict:
        """
        Auto-fill model information
        """
        cache_key = f"model_autofill_{brand_name}_{model_name}".lower()
        cached = cache.get(cache_key)
        if cached:
            return cached

        try:
            # Try GSMArena
            data = cls._fetch_model_from_gsmarena(brand_name, model_name)
            if not data:
                # Fallback to AI
                data = cls._generate_with_ai(
                    f"Device: {brand_name} {model_name}. "
                    f"Provide: marketing_name, model_code, release_date, description"
                )

            cache.set(cache_key, data, cls.CACHE_TIMEOUT)
            return data
        except Exception as e:
            logger.exception(f"Error autofilling model {brand_name} {model_name}: {e}")
            return {}

    @classmethod
    def autofill_variant(
        cls, brand_name: str, model_name: str, region: str | None = None
    ) -> dict:
        """
        Auto-fill variant information
        """
        cache_key = f"variant_autofill_{brand_name}_{model_name}_{region}".lower()
        cached = cache.get(cache_key)
        if cached:
            return cached

        try:
            # Try GSMArena
            data = cls._fetch_variant_from_gsmarena(
                brand_name, model_name, region or ""
            )
            if not data:
                # Fallback to AI
                data = cls._generate_with_ai(
                    f"Device variant: {brand_name} {model_name} ({region}). "
                    f"Provide: chipset, ram_options, storage_options, board_id"
                )

            cache.set(cache_key, data, cls.CACHE_TIMEOUT)
            return data
        except Exception as e:
            logger.exception(f"Error autofilling variant: {e}")
            return {}

    @classmethod
    def _fetch_from_gsmarena(cls, brand_name: str) -> dict:
        """
        Fetch brand data from existing GSMArenaDevice records.
        """
        try:
            from .gsmarena_service import lookup_gsmarena_brand

            return lookup_gsmarena_brand(brand_name)
        except Exception as e:
            logger.warning(f"GSMArena fetch failed: {e}")
            return {}

    @classmethod
    def _fetch_model_from_gsmarena(cls, brand_name: str, model_name: str) -> dict:
        """
        Fetch model data from existing GSMArenaDevice records.
        """
        try:
            from .gsmarena_service import lookup_gsmarena_model

            return lookup_gsmarena_model(brand_name, model_name)
        except Exception as e:
            logger.warning(f"GSMArena model fetch failed: {e}")
            return {}

    @classmethod
    def _fetch_variant_from_gsmarena(
        cls, brand_name: str, model_name: str, region: str
    ) -> dict:
        """
        Fetch variant data from existing GSMArenaDevice records.
        """
        try:
            from .gsmarena_service import lookup_gsmarena_variant

            return lookup_gsmarena_variant(brand_name, model_name, region)
        except Exception as e:
            logger.warning(f"GSMArena variant fetch failed: {e}")
            return {}

    @classmethod
    def _generate_with_ai(cls, prompt: str) -> dict:
        """
        Generate missing data using AI
        """
        try:
            # Try to use AI services if available
            try:
                from apps.ai.services import test_completion

                response = test_completion(prompt)
                if response and "content" in response:
                    data = cls._parse_ai_response(response["content"])
                    return data
            except Exception as ai_error:
                logger.warning(f"AI service not available: {ai_error}")

            # Fallback: return empty dict
            return {}
        except Exception as e:
            logger.exception(f"AI generation failed: {e}")
            return {}

    @classmethod
    def _parse_ai_response(cls, response: str) -> dict:
        """
        Parse AI response into structured data
        """
        # Simple parsing - can be enhanced
        data = {}
        lines = response.split("\n")
        for line in lines:
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip().lower()] = value.strip()
        return data


def autofill_on_save(sender, instance, created, **kwargs):
    """
    Signal handler to auto-fill fields on model save
    """
    if not created:
        return  # Only autofill on creation

    if sender.__name__ == "Brand":
        # Auto-fill brand fields
        if not instance.description:
            data = FirmwareAutoFill.autofill_brand(instance.name)
            instance.description = data.get("description", "")
            instance.save(update_fields=["description"])

    elif sender.__name__ == "Model":
        # Auto-fill model fields
        if not instance.marketing_name or not instance.model_code:
            data = FirmwareAutoFill.autofill_model(instance.brand.name, instance.name)
            if not instance.marketing_name:
                instance.marketing_name = data.get("marketing_name", instance.name)
            if not instance.model_code:
                instance.model_code = data.get("model_code", "")
            if not instance.description:
                instance.description = data.get("description", "")
            instance.save(update_fields=["marketing_name", "model_code", "description"])

    elif sender.__name__ == "Variant":
        # Auto-fill variant fields
        if not instance.chipset or not instance.ram_options:
            data = FirmwareAutoFill.autofill_variant(
                instance.model.brand.name, instance.model.name, instance.region
            )
            if not instance.chipset:
                instance.chipset = data.get("chipset", "")
            if not instance.ram_options:
                instance.ram_options = data.get("ram_options", [])
            if not instance.storage_options:
                instance.storage_options = data.get("storage_options", [])
            instance.save(update_fields=["chipset", "ram_options", "storage_options"])

"""GSMArena spec autofill helpers.

Read-only helpers that materialize approved :class:`GSMArenaDevice` spec
records into the firmware catalogue (Brand/Model) and provide best-effort
spec lookups used by the firmware auto-fill forms.  These helpers operate
purely on cached ``GSMArenaDevice`` rows — there is no live scraping.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def materialize_approved_device(gsm_device_id: int) -> dict[str, Any]:
    """Create or link Brand and Model records for an approved GSMArenaDevice.

    Called after admin approves a scraped GSMArena device.  Creates the
    Brand (if it doesn't exist) and Model (if it doesn't exist) in the
    firmwares catalogue and links the GSMArenaDevice to the local Device
    record when possible.

    Returns a summary dict with keys: brand_created, model_created, brand, model.
    """
    from django.utils.text import slugify

    from .models import Brand, GSMArenaDevice, Model

    result: dict[str, Any] = {
        "brand_created": False,
        "model_created": False,
        "brand": "",
        "model": "",
    }

    dev = GSMArenaDevice.objects.filter(pk=gsm_device_id).first()
    if not dev:
        return result

    brand_name = dev.brand.strip()
    model_name = dev.model_name.strip()
    if not brand_name or not model_name:
        return result

    # --- Brand ---
    brand_slug = slugify(brand_name)
    if not brand_slug:
        return result
    brand_obj, brand_created = Brand.objects.get_or_create(
        slug=brand_slug,
        defaults={"name": brand_name},
    )
    result["brand_created"] = brand_created
    result["brand"] = brand_obj.name

    # --- Model ---
    model_slug = slugify(model_name)
    if not model_slug:
        return result
    model_defaults: dict[str, Any] = {
        "name": model_name,
        "marketing_name": dev.marketed_as or "",
        "model_code": (dev.model_codes or "")[:64],
        "chipset": (dev.chipset or "")[:256],
        "os_version": (dev.os_version or "")[:128],
        "image_url": (dev.image_url or "")[:500],
    }
    model_obj, model_created = Model.objects.get_or_create(
        brand=brand_obj,
        slug=model_slug,
        defaults=model_defaults,
    )
    result["model_created"] = model_created
    result["model"] = model_obj.name

    # Update fields if model already existed but was sparse
    if not model_created:
        changed = False
        _sync_pairs: list[tuple[str, str, int]] = [
            ("marketing_name", dev.marketed_as or "", 255),
            ("model_code", (dev.model_codes or "")[:64], 64),
            ("chipset", (dev.chipset or "")[:256], 256),
            ("os_version", (dev.os_version or "")[:128], 128),
            ("image_url", (dev.image_url or "")[:500], 500),
        ]
        update_fields: list[str] = []
        for field_name, new_val, _max_len in _sync_pairs:
            if not getattr(model_obj, field_name, "") and new_val:
                setattr(model_obj, field_name, new_val)
                update_fields.append(field_name)
                changed = True
        if changed:
            model_obj.save(update_fields=update_fields)

    return result


def lookup_gsmarena_brand(brand_name: str) -> dict[str, Any]:
    """
    Look up brand info from existing GSMArenaDevice records.

    Returns dict with description extracted from specs.
    """
    from .models import GSMArenaDevice

    device = (
        GSMArenaDevice.objects.filter(brand__iexact=brand_name)
        .order_by("-last_synced_at")
        .first()
    )
    if not device:
        return {}

    specs = device.specs or {}
    brand_desc = f"{brand_name} — {specs.get('full_name', device.model_name)}"
    return {"description": brand_desc}


def lookup_gsmarena_model(brand_name: str, model_name: str) -> dict[str, Any]:
    """
    Look up model info from existing GSMArenaDevice records.

    Returns dict with marketing_name, model_code, release_date, description.
    """
    from .models import GSMArenaDevice

    device = (
        GSMArenaDevice.objects.filter(
            brand__iexact=brand_name,
            model_name__icontains=model_name,
        )
        .order_by("-last_synced_at")
        .first()
    )
    if not device:
        return {}

    specs = device.specs or {}
    result: dict[str, Any] = {}

    if specs.get("full_name"):
        result["marketing_name"] = specs["full_name"]

    model_codes = specs.get("misc_models", "")
    if model_codes:
        first_code = model_codes.split(",")[0].strip()
        result["model_code"] = first_code

    announced = specs.get("launch_announced", "")
    if announced:
        result["release_date"] = _parse_launch_date(announced)

    desc_parts = []
    if specs.get("display_size"):
        desc_parts.append(specs["display_size"])
    if specs.get("platform_chipset"):
        desc_parts.append(specs["platform_chipset"])
    if specs.get("battery_type"):
        desc_parts.append(specs["battery_type"])
    if desc_parts:
        result["description"] = " | ".join(desc_parts)

    return result


def lookup_gsmarena_variant(
    brand_name: str, model_name: str, region: str
) -> dict[str, Any]:
    """
    Look up variant info from existing GSMArenaDevice records.

    Returns dict with chipset, ram_options, storage_options, board_id.
    """
    from .models import GSMArenaDevice

    device = (
        GSMArenaDevice.objects.filter(
            brand__iexact=brand_name,
            model_name__icontains=model_name,
        )
        .order_by("-last_synced_at")
        .first()
    )
    if not device:
        return {}

    specs = device.specs or {}
    result: dict[str, Any] = {}

    if specs.get("platform_chipset"):
        result["chipset"] = specs["platform_chipset"]

    memory = specs.get("memory_internal", "")
    if memory:
        ram_set: set[str] = set()
        storage_set: set[str] = set()
        for part in memory.split(","):
            part = part.strip()
            tokens = part.split()
            for token in tokens:
                if "GB" in token and "RAM" in part:
                    ram_set.add(token)
                elif "GB" in token or "TB" in token:
                    storage_set.add(token)
        if ram_set:
            result["ram_options"] = ", ".join(sorted(ram_set))
        if storage_set:
            result["storage_options"] = ", ".join(sorted(storage_set))

    model_codes = specs.get("misc_models", "")
    if model_codes:
        codes = [c.strip() for c in model_codes.split(",")]
        if region:
            region_lower = region.lower()
            for code in codes:
                if region_lower in code.lower():
                    result["board_id"] = code
                    break
        if "board_id" not in result and codes:
            result["board_id"] = codes[0]

    return result


def _parse_launch_date(announced: str) -> str:
    """
    Parse GSMArena 'launch_announced' string into YYYY-MM-DD or YYYY.

    Examples: "2024, January 17" → "2024-01-17", "2024" → "2024"
    """
    announced = announced.strip()
    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

    parts = [p.strip() for p in announced.replace(",", " ").split()]
    year = ""
    month = 0
    day = 0

    for part in parts:
        if part.isdigit() and len(part) == 4:
            year = part
        elif part.lower() in months:
            month = months[part.lower()]
        elif part.isdigit() and len(part) <= 2:
            day = int(part)

    if year and month and day:
        try:
            dt = datetime(int(year), month, day)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    if year and month:
        return f"{year}-{month:02d}-01"
    return year

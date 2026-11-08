MAIN_CATEGORIES = (
    ("official", "Official Firmware"),
    ("engineering", "Engineering Firmware"),
    ("readback", "Readback Firmware"),
    ("modified", "Modified Firmware"),
    ("other", "Other Firmware"),
    ("unclassified", "Others (Unclassified)"),
)

# Brand slugs that would collide with fixed /firmwares/<prefix>/ routes.
RESERVED_BRAND_SLUGS = (
    "api",
    "brands",
    "browse",
    "chipsets",
    "file",
    "guides",
    "moderate",
    "requests",
    "search",
    "serve",
    "tools",
    "upload",
)

MODIFIED_SUBTYPES = (
    "cn_to_global",
    "region_unlock",
    "debloated",
    "gpc_fix",
    "pre_rooted",
    "bootloader_unlocked",
    "multi_language",
    "security_repair",
    "partition_mod",
    "patched_firehose",
    "edl_unbrick",
    "region_restriction",
    "hybrid",
    "rebuilt_package",
    "other",
)

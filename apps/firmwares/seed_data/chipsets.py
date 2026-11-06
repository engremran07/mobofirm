"""System-on-chip catalogue shared by the seeded device models."""

from __future__ import annotations

from .types import ChipsetSeed

_SERVICE_NOTES = {
    "Qualcomm": (
        "Devices built on Qualcomm platforms are usually serviced through Fastboot "
        "or, for deeper recovery, Emergency Download (EDL) mode with the "
        "manufacturer's authorised tooling."
    ),
    "MediaTek": (
        "Devices built on MediaTek platforms are usually serviced through Fastboot "
        "or, for deeper recovery, the BootROM/Preloader USB mode with scatter-file "
        "based flashing tools."
    ),
    "Samsung": (
        "Samsung Galaxy devices with this processor are flashed in Download mode "
        "using the AP, BL, CP and CSC package files."
    ),
    "Google": (
        "Pixel devices with this processor are updated or restored with Google's "
        "factory images through Fastboot or the Android Flash Tool."
    ),
    "Unisoc": (
        "Devices built on Unisoc platforms are usually serviced in the Unisoc USB "
        "download mode with .pac firmware packages."
    ),
    "HiSilicon": (
        "Huawei devices with this Kirin processor are restored through the "
        "manufacturer's recovery and update mechanisms."
    ),
}


def _chipset(
    vendor: str,
    name: str,
    *,
    family: str = "",
    aliases: tuple[str, ...] = (),
    slug_hint: str = "",
) -> ChipsetSeed:
    key = slug_hint or f"{vendor}-{name}".lower().replace(" ", "-")
    return ChipsetSeed(
        key=key,
        vendor=vendor,
        name=name,
        family=family,
        aliases=aliases,
        description=f"{vendor} {name} system-on-chip. {_SERVICE_NOTES[vendor]}",
    )


def _snapdragon(name: str, *aliases: str) -> ChipsetSeed:
    return _chipset(
        "Qualcomm", f"Snapdragon {name}", family="Snapdragon", aliases=aliases
    )


def _dimensity(name: str, *aliases: str) -> ChipsetSeed:
    return _chipset(
        "MediaTek", f"Dimensity {name}", family="Dimensity", aliases=aliases
    )


def _helio(name: str, *aliases: str) -> ChipsetSeed:
    return _chipset("MediaTek", f"Helio {name}", family="Helio", aliases=aliases)


CHIPSETS: tuple[ChipsetSeed, ...] = (
    _snapdragon("8 Gen 3 for Galaxy", "Snapdragon 8 Gen 3 for Galaxy (4nm)"),
    _snapdragon("8 Gen 3", "Snapdragon 8 Gen 3 (4nm)"),
    _snapdragon("8 Gen 2", "Snapdragon 8 Gen 2 (4nm)"),
    _snapdragon("8 Plus Gen 1", "Snapdragon 8+ Gen 1", "Snapdragon 8+ Gen 1 (4nm)"),
    _snapdragon("8 Gen 1", "Snapdragon 8 Gen 1 (4nm)"),
    _snapdragon("8s Gen 3", "Snapdragon 8s Gen 3 (4nm)"),
    _snapdragon("7 Plus Gen 2", "Snapdragon 7+ Gen 2"),
    _snapdragon("7 Gen 3"),
    _snapdragon("7s Gen 3"),
    _snapdragon("7s Gen 2"),
    _snapdragon("7 Gen 1"),
    _snapdragon("778G", "Snapdragon 778G 5G"),
    _snapdragon("778G Plus", "Snapdragon 778G+", "Snapdragon 778G+ 5G"),
    _snapdragon("695", "Snapdragon 695 5G"),
    _snapdragon("680", "Snapdragon 680 4G"),
    _snapdragon("4 Gen 1"),
    _snapdragon("480 Plus 5G", "Snapdragon 480+ 5G"),
    _snapdragon("480 5G"),
    _snapdragon("888", "Snapdragon 888 5G"),
    _snapdragon("870", "Snapdragon 870 5G"),
    _snapdragon("855"),
    _snapdragon("750G", "Snapdragon 750G 5G"),
    _snapdragon("632"),
    _chipset("Qualcomm", "QCM6490", family="Snapdragon"),
    _dimensity("9300"),
    _dimensity("9200 Plus", "Dimensity 9200+"),
    _dimensity("9200"),
    _dimensity("9000"),
    _dimensity("8300 Ultra", "Dimensity 8300-Ultra"),
    _dimensity("8100 Ultra", "Dimensity 8100-Ultra"),
    _dimensity("8020"),
    _dimensity("7350 Pro", "Dimensity 7350 Pro 5G"),
    _dimensity("7300"),
    _dimensity("7200 Pro", "Dimensity 7200 Pro 5G"),
    _dimensity("7200 Ultra", "Dimensity 7200-Ultra"),
    _dimensity("7200"),
    _dimensity("7050"),
    _dimensity("6080"),
    _dimensity("6020"),
    _dimensity("930"),
    _helio("G99", "Helio G99 (6nm)"),
    _helio("G99 Ultra", "Helio G99-Ultra"),
    _helio("G88", "Helio G88 (12nm)"),
    _helio("G85", "Helio G85 (12nm)"),
    _helio("G70"),
    _helio("G37"),
    _helio("G36"),
    _helio("G35"),
    _helio("A22"),
    _chipset("Samsung", "Exynos 2400", family="Exynos"),
    _chipset("Samsung", "Exynos 2200", family="Exynos"),
    _chipset("Samsung", "Exynos 1380", family="Exynos"),
    _chipset("Google", "Tensor", family="Tensor", aliases=("Google Tensor G1",)),
    _chipset("Google", "Tensor G2", family="Tensor"),
    _chipset("Google", "Tensor G3", family="Tensor"),
    _chipset("Google", "Tensor G4", family="Tensor"),
    _chipset("Unisoc", "T606", family="Unisoc T", aliases=("Unisoc T606 (12nm)",)),
    _chipset("Unisoc", "T610", family="Unisoc T"),
    _chipset("Unisoc", "T612", family="Unisoc T"),
    _chipset("Unisoc", "T616", family="Unisoc T"),
    _chipset("Unisoc", "SC9863A", aliases=("Unisoc SC9863a",)),
    _chipset("HiSilicon", "Kirin 990 5G", family="Kirin"),
    _chipset("HiSilicon", "Kirin 980", family="Kirin"),
    _chipset("HiSilicon", "Kirin 970", family="Kirin"),
    _chipset("HiSilicon", "Kirin 810", family="Kirin"),
)

CHIPSETS_BY_KEY: dict[str, ChipsetSeed] = {chip.key: chip for chip in CHIPSETS}


def chipset_key(vendor: str, name: str) -> str:
    return f"{vendor}-{name}".lower().replace(" ", "-")


def sd(name: str) -> str:
    return chipset_key("Qualcomm", f"Snapdragon {name}")


def dim(name: str) -> str:
    return chipset_key("MediaTek", f"Dimensity {name}")


def helio(name: str) -> str:
    return chipset_key("MediaTek", f"Helio {name}")


def exynos(name: str) -> str:
    return chipset_key("Samsung", f"Exynos {name}")


def tensor(name: str = "") -> str:
    return chipset_key("Google", f"Tensor {name}".strip())


def unisoc(name: str) -> str:
    return chipset_key("Unisoc", name)


def kirin(name: str) -> str:
    return chipset_key("HiSilicon", f"Kirin {name}")

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
    "Rockchip": (
        "Tablets and phones built on Rockchip platforms are usually serviced in "
        "Maskrom or Loader mode with an update.img package and RKDevTool."
    ),
    "Allwinner": (
        "Tablets and phones built on Allwinner platforms are usually serviced over "
        "USB in FEL mode with a PhoenixSuit or LiveSuit .img package."
    ),
    "Apple": (
        "iPhone and iPad models with this chip are restored with an IPSW image "
        "through Finder, the Apple Devices app or Apple Configurator 2, using "
        "recovery or DFU mode. Only IPSW files that Apple still signs install."
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


_CORE_CHIPSETS: tuple[ChipsetSeed, ...] = (
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
    _chipset("Unisoc", "T618", family="Unisoc T"),
    _chipset("Unisoc", "SC7731E", aliases=("Spreadtrum SC7731E",)),
    # RDA Microelectronics is a Unisoc-owned vendor label.
    _chipset(
        "Unisoc",
        "RDA8810PL",
        family="RDA",
        aliases=("RDA Microelectronics RDA8810PL", "RDA8810PL"),
    ),
    _chipset("Allwinner", "A33", family="Allwinner A"),
    _chipset("Allwinner", "A64", family="Allwinner A"),
    _chipset("Allwinner", "A83T", family="Allwinner A"),
    _chipset("Allwinner", "A100", family="Allwinner A"),
    _chipset("Allwinner", "A133", family="Allwinner A"),
    _chipset("Allwinner", "A523", family="Allwinner A"),
    _chipset("Allwinner", "T507", family="Allwinner T"),
    _chipset("Rockchip", "RK3288"),
    _chipset("Rockchip", "RK3326"),
    _chipset("Rockchip", "RK3399"),
    _chipset("Rockchip", "RK3566"),
    _chipset("Rockchip", "RK3568"),
    # Apple A-series (iPhone, iPad) and M-series (iPad) system-on-chips.
    _chipset("Apple", "A12 Bionic", family="A-series"),
    _chipset("Apple", "A13 Bionic", family="A-series"),
    _chipset("Apple", "A14 Bionic", family="A-series"),
    _chipset("Apple", "A15 Bionic", family="A-series"),
    _chipset("Apple", "A16 Bionic", family="A-series"),
    _chipset("Apple", "A17 Pro", family="A-series"),
    _chipset("Apple", "A18", family="A-series"),
    _chipset("Apple", "A18 Pro", family="A-series"),
    _chipset("Apple", "A19", family="A-series"),
    _chipset("Apple", "A19 Pro", family="A-series"),
    _chipset("Apple", "M1", family="M-series"),
    _chipset("Apple", "M2", family="M-series"),
    _chipset("Apple", "M3", family="M-series"),
    _chipset("Apple", "M4", family="M-series"),
    _chipset("Apple", "M5", family="M-series"),
    _chipset("HiSilicon", "Kirin 990 5G", family="Kirin"),
    _chipset("HiSilicon", "Kirin 980", family="Kirin"),
    _chipset("HiSilicon", "Kirin 970", family="Kirin"),
    _chipset("HiSilicon", "Kirin 810", family="Kirin"),
)


def _plus_aliases(name: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    """The marketing spelling with ``+`` for names stored with ``Plus``."""
    if " Plus" not in name:
        return aliases
    return (*aliases, name.replace(" Plus", "+"))


def _family(
    vendor: str, family: str, prefix: str, names: tuple[str, ...]
) -> tuple[ChipsetSeed, ...]:
    return tuple(
        _chipset(
            vendor,
            f"{prefix}{name}".strip(),
            family=family,
            aliases=_plus_aliases(f"{prefix}{name}".strip(), ()),
        )
        for name in names
    )


# Every smartphone and tablet SoC family of the supported vendors, so brands, models
# and firmware can be attached to a chipset that exists even before a device uses it.
_REFERENCE_CHIPSETS: tuple[ChipsetSeed, ...] = (
    *_family(
        "Qualcomm",
        "Snapdragon",
        "Snapdragon ",
        (
            "8 Elite Gen 5",
            "8 Elite for Galaxy",
            "8 Elite",
            "8s Gen 4",
            "7 Plus Gen 3",
            "7 Gen 4",
            "6 Gen 4",
            "6 Gen 3",
            "6 Gen 1",
            "6s Gen 3",
            "6s 4G Gen 1",
            "4 Gen 2",
            "4s Gen 2",
            "888 Plus",
            "865 Plus",
            "865",
            "855 Plus",
            "845",
            "835",
            "821",
            "820",
            "810",
            "808",
            "805",
            "801",
            "800",
            "782G",
            "780G",
            "768G",
            "765G",
            "732G",
            "730G",
            "730",
            "720G",
            "712",
            "710",
            "690",
            "685",
            "678",
            "675",
            "670",
            "665",
            "662",
            "660",
            "653",
            "652",
            "636",
            "630",
            "626",
            "625",
            "617",
            "616",
            "615",
            "612",
            "460",
            "450",
            "439",
            "435",
            "430",
            "429",
            "425",
            "415",
            "410",
            "400",
            "215",
            "212",
            "210",
        ),
    ),
    *_family(
        "MediaTek",
        "Dimensity",
        "Dimensity ",
        (
            "9500",
            "9400 Plus",
            "9400",
            "9300 Plus",
            "9000 Plus",
            "8450",
            "8400",
            "8350",
            "8300",
            "8250",
            "8200",
            "8100",
            "8050",
            "8000",
            "7400",
            "7300X",
            "7030",
            "7025",
            "7020",
            "6300",
            "6100 Plus",
            "6100",
            "1300",
            "1200",
            "1100",
            "1080",
            "1050",
            "1000 Plus",
            "1000",
            "920",
            "900",
            "820",
            "810",
            "800",
            "720",
            "700",
        ),
    ),
    *_family(
        "MediaTek",
        "Helio",
        "Helio ",
        (
            "G25",
            "G50",
            "G52",
            "G60S",
            "G80",
            "G81",
            "G90",
            "G90T",
            "G91",
            "G95",
            "G96",
            "G100",
            "P10",
            "P20",
            "P22",
            "P23",
            "P25",
            "P30",
            "P35",
            "P60",
            "P65",
            "P70",
            "P90",
            "P95",
            "X10",
            "X20",
            "X23",
            "X25",
            "X27",
            "X30",
            "A20",
            "A25",
        ),
    ),
    *_family(
        "MediaTek",
        "Kompanio",
        "Kompanio ",
        ("500", "800T", "900T", "1200", "1300T"),
    ),
    *_family(
        "MediaTek",
        "MT65xx",
        "",
        (
            "MT6572",
            "MT6580",
            "MT6582",
            "MT6592",
            "MT6735",
            "MT6737",
            "MT6739",
            "MT6750",
            "MT6753",
        ),
    ),
    *_family(
        "Samsung",
        "Exynos",
        "Exynos ",
        (
            "2600",
            "2500",
            "2400e",
            "2100",
            "1580",
            "1480",
            "1330",
            "1280",
            "1080",
            "990",
            "980",
            "880",
            "850",
            "9825",
            "9820",
            "9810",
            "9611",
            "9610",
            "9609",
            "8895",
            "8890",
            "7904",
            "7885",
            "7884",
            "7880",
            "7870",
            "7580",
            "7420",
            "5433",
        ),
    ),
    *_family(
        "Google",
        "Tensor",
        "Tensor ",
        ("G5",),
    ),
    *_family(
        "HiSilicon",
        "Kirin",
        "Kirin ",
        (
            "9020",
            "9010E",
            "9010",
            "9000WL",
            "9000S1",
            "9000S",
            "9000E",
            "9000",
            "8000",
            "990",
            "990E",
            "985 5G",
            "820 5G",
            "830",
            "720",
            "710A",
            "710F",
            "710",
            "659",
            "655",
            "650",
            "620",
            "960",
            "955",
            "950",
            "935",
            "930",
            "925",
            "920",
            "910",
        ),
    ),
    *_family(
        "Unisoc",
        "Unisoc T",
        "",
        (
            "T310",
            "T700",
            "T760",
            "T770",
            "T820",
            "T7200",
            "T7225",
            "T7250",
            "T7510",
            "T7520",
            "T9300",
            "UMS312",
            "UMS512",
            "UMS9230",
            "UMS9620",
        ),
    ),
    *_family(
        "Unisoc",
        "Spreadtrum",
        "",
        (
            "SC7731",
            "SC7731C",
            "SC7731G",
            "SC7730S",
            "SC8830",
            "SC9830",
            "SC9832E",
            "SC9850",
            "SC9853I",
            "SC9860",
            "SC9860G",
            "SC9863",
        ),
    ),
    *_family(
        "Rockchip",
        "Rockchip RK",
        "",
        (
            "RK3026",
            "RK3066",
            "RK3126",
            "RK3126C",
            "RK3128",
            "RK3188",
            "RK3368",
            "RK3562",
            "RK3576",
            "RK3588",
            "RK3588S",
            "PX30",
        ),
    ),
    *_family(
        "Allwinner",
        "Allwinner A",
        "",
        (
            "A13",
            "A23",
            "A31",
            "A31S",
            "A50",
            "A80",
            "A133P",
            "A527",
            "A537",
        ),
    ),
    *_family("Allwinner", "Allwinner T", "", ("T517", "T527")),
    *_family(
        "Apple",
        "A-series",
        "",
        (
            "A6",
            "A7",
            "A8",
            "A8X",
            "A9",
            "A9X",
            "A10 Fusion",
            "A10X Fusion",
            "A11 Bionic",
            "A12X Bionic",
            "A12Z Bionic",
        ),
    ),
)

_CORE_KEYS = frozenset(chip.key for chip in _CORE_CHIPSETS)
_REFERENCE_UNIQUE: tuple[ChipsetSeed, ...] = tuple(
    {
        chip.key: chip for chip in _REFERENCE_CHIPSETS if chip.key not in _CORE_KEYS
    }.values()
)

CHIPSETS: tuple[ChipsetSeed, ...] = (*_CORE_CHIPSETS, *_REFERENCE_UNIQUE)

CHIPSETS_BY_KEY: dict[str, ChipsetSeed] = {chip.key: chip for chip in CHIPSETS}

# Tablet, low-end phone and reference platforms that no seeded model uses yet. They are
# seeded with the catalogue so the vendor, family and flashing-tool matching exist from
# day one.
PLATFORM_COVERAGE_KEYS: frozenset[str] = frozenset(
    chip.key
    for chip in CHIPSETS
    if chip.vendor in {"Allwinner", "Rockchip", "Apple"}
    or chip.family == "RDA"
    or chip.key in {"unisoc-t618", "unisoc-sc7731e"}
) | frozenset(chip.key for chip in _REFERENCE_UNIQUE)


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


def allwinner(name: str) -> str:
    return chipset_key("Allwinner", name)


def rockchip(name: str) -> str:
    return chipset_key("Rockchip", name)


def apple(name: str) -> str:
    return chipset_key("Apple", name)

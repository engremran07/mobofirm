"""Typed building blocks for the device catalogue seed data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from apps.firmwares.models import FirmwarePackageFormat


@dataclass(frozen=True)
class ChipsetSeed:
    key: str
    vendor: str
    name: str
    description: str
    family: str = ""
    aliases: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        return f"{self.vendor} {self.name}"


@dataclass(frozen=True)
class VariantSeed:
    name: str
    region: str
    model_code: str
    chipset: str = ""


@dataclass(frozen=True)
class ModelSeed:
    """One marketed device. Dates carry month precision only (day is always 1)."""

    name: str
    model_code: str
    chipset: str
    released: date
    android: int
    codename: str = ""
    marketing_name: str = ""
    os_label: str = ""
    variants: tuple[VariantSeed, ...] = ()


@dataclass(frozen=True)
class BuildScheme:
    """How a manufacturer formats build numbers and packages its releases."""

    key: str
    package_formats: tuple[str, str]
    extensions: tuple[str, str]


@dataclass(frozen=True)
class BrandSeed:
    name: str
    slug: str
    website_url: str
    description: str
    scheme: BuildScheme
    models: tuple[ModelSeed, ...]
    featured: bool = False


@dataclass(frozen=True)
class FirmwarePlan:
    """The release metadata of one external firmware listing."""

    build_number: str
    android_version: str
    security_patch: date
    build_date: date
    package_format: str
    file_name: str
    url: str


def model(
    name: str,
    model_code: str,
    chipset: str,
    released: tuple[int, int],
    android: int,
    *,
    codename: str = "",
    marketing_name: str = "",
    os_label: str = "",
    variants: tuple[VariantSeed, ...] = (),
) -> ModelSeed:
    return ModelSeed(
        name=name,
        model_code=model_code,
        chipset=chipset,
        released=date(released[0], released[1], 1),
        android=android,
        codename=codename,
        marketing_name=marketing_name,
        os_label=os_label,
        variants=variants,
    )


def variant(name: str, region: str, model_code: str, chipset: str = "") -> VariantSeed:
    return VariantSeed(name=name, region=region, model_code=model_code, chipset=chipset)


def _scheme(
    key: str, first: str, second: str, ext_first: str = ".zip", ext_second: str = ".zip"
) -> BuildScheme:
    return BuildScheme(key, (first, second), (ext_first, ext_second))


F = FirmwarePackageFormat
SCHEME_SAMSUNG = _scheme(
    "samsung", F.SERVICE_PACKAGE, F.SERVICE_PACKAGE, ".tar.md5", ".tar.md5"
)
SCHEME_XIAOMI = _scheme("xiaomi", F.FASTBOOT_ROM, F.RECOVERY_ROM, ".tgz", ".zip")
SCHEME_PIXEL = _scheme("pixel", F.FACTORY_IMAGE, F.FULL_OTA)
SCHEME_OPLUS = _scheme("oplus", F.FULL_OTA, F.FULL_OTA)
SCHEME_VIVO = _scheme("vivo", F.SERVICE_PACKAGE, F.FULL_OTA)
SCHEME_HUAWEI = _scheme("huawei", F.FULL_OTA, F.FULL_OTA)
SCHEME_HONOR = _scheme("honor", F.FULL_OTA, F.FULL_OTA)
SCHEME_MOTOROLA = _scheme("motorola", F.FASTBOOT_ROM, F.FASTBOOT_ROM)
SCHEME_HMD = _scheme("hmd", F.SERVICE_PACKAGE, F.FULL_OTA)
SCHEME_SONY = _scheme("sony", F.SERVICE_PACKAGE, F.SERVICE_PACKAGE)
SCHEME_ASUS = _scheme("asus", F.FULL_OTA, F.FULL_OTA)
SCHEME_LENOVO = _scheme("lenovo", F.SERVICE_PACKAGE, F.SERVICE_PACKAGE)
SCHEME_TRANSSION = _scheme("transsion", F.SERVICE_PACKAGE, F.FULL_OTA)
SCHEME_ZTE = _scheme("zte", F.FULL_OTA, F.FULL_OTA)
SCHEME_NOTHING = _scheme("nothing", F.FASTBOOT_ROM, F.FULL_OTA)
SCHEME_FAIRPHONE = _scheme("fairphone", F.FULL_OTA, F.FULL_OTA)

PIXEL_PREFIX = {12: "SQ3A", 13: "TQ3A", 14: "UQ1A", 15: "AP3A", 16: "BP2A"}
MOTOROLA_LETTER = {11: "R", 12: "S", 13: "T", 14: "U", 15: "V"}


@dataclass(frozen=True)
class _Release:
    """The inputs every manufacturer's build-number notation draws from."""

    code: str
    android: int
    seq: int
    when: date


def _samsung(r: _Release) -> str:
    year_letter = chr(ord("A") + r.when.year - 2001)
    month_letter = chr(ord("A") + r.when.month - 1)
    return (
        f"{r.code.removeprefix('SM-')}XXU{r.seq}{chr(64 + r.seq)}"
        f"{year_letter}{month_letter}{2 * r.seq + 1}"
    )


def _xiaomi(r: _Release) -> str:
    if r.when >= date(2024, 10, 1):
        return f"OS2.0.{r.seq + 1}.0"
    if r.when >= date(2023, 10, 1):
        return f"OS1.0.{r.seq + 2}.0"
    if r.when >= date(2022, 12, 1):
        return f"V14.0.{r.seq + 1}.0"
    if r.when >= date(2022, 1, 1):
        return f"V13.0.{r.seq + 1}.0"
    return f"V12.5.{r.seq + 1}.0"


def _pixel(r: _Release) -> str:
    prefix = PIXEL_PREFIX.get(r.android, "UQ1A")
    return f"{prefix}.{r.when:%y%m}05.{r.seq:03d}"


def _oplus(r: _Release) -> str:
    return f"{r.code}_{r.android}.0.0.{300 + 55 * r.seq}"


def _vivo(r: _Release) -> str:
    return f"{r.code}_A_{r.android}.{r.seq}.{3 + r.seq}.{10 * r.seq}"


def _motorola(r: _Release) -> str:
    letter = MOTOROLA_LETTER.get(r.android, "T")
    return f"{letter}{r.seq}SA{20 + r.android}.{18 + 9 * r.seq}-{10 + 6 * r.seq}"


def _hmd(r: _Release) -> str:
    return f"00WW_{r.seq}_{20 + 7 * r.seq}{chr(64 + r.when.month)}"


def _sony(r: _Release) -> str:
    return f"{50 + r.android}.{r.seq}.A.{r.seq + 1}.{50 + 11 * r.seq}"


def _asus(r: _Release) -> str:
    return f"WW_{30 + r.android}.{1004 + 10 * r.seq}.{r.when:%m}05.{90 + r.seq}"


def _lenovo(r: _Release) -> str:
    return f"{r.code}_S{r.when:%y}{r.seq:02d}{r.when:%m}0_{r.when:%y%m}05_ROW"


def _transsion(r: _Release) -> str:
    return f"{r.code}-{r.android}-{r.when:%y%m}05V{100 * r.seq + 17}"


def _zte(r: _Release) -> str:
    return f"{r.code}V1.0.0B{5 + 2 * r.seq:02d}"


def _nothing(r: _Release) -> str:
    return f"V{r.android - 11}.{r.seq}.{r.seq + 3}-{r.when:%y%m}05"


def _fairphone(r: _Release) -> str:
    return f"{r.code}.B.{100 + 20 * r.seq}.{r.when:%Y%m}05"


def _emui_like(seed: ModelSeed, seq: int) -> str:
    return f"{seed.model_code} {seed.os_label or seed.android}.0.{100 + 37 * seq}"


def build_number(scheme: BuildScheme, seed: ModelSeed, seq: int, when: date) -> str:
    """A build identifier in the manufacturer's usual notation for one release."""
    if scheme.key in {"huawei", "honor"}:
        return _emui_like(seed, seq)
    builders = {
        "samsung": _samsung,
        "xiaomi": _xiaomi,
        "pixel": _pixel,
        "oplus": _oplus,
        "vivo": _vivo,
        "motorola": _motorola,
        "hmd": _hmd,
        "sony": _sony,
        "asus": _asus,
        "lenovo": _lenovo,
        "transsion": _transsion,
        "zte": _zte,
        "nothing": _nothing,
        "fairphone": _fairphone,
    }
    release = _Release(seed.model_code or seed.name, seed.android, seq, when)
    return builders[scheme.key](release)


def release_dates(released: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """(build date, security patch) of the launch build and a later update."""
    launch_build = released - timedelta(days=21)
    update_build = released + timedelta(days=150)
    return (
        (launch_build, date(launch_build.year, launch_build.month, 1)),
        (update_build, date(update_build.year, update_build.month, 1)),
    )

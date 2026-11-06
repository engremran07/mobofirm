"""Live firmware summary shown beside automated model/brand/chipset posts."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.urls import reverse

from apps.blog.models import Post


@dataclass
class CategorySummary:
    key: str
    label: str
    count: int
    browse_url: str


@dataclass
class FirmwareSummary:
    total: int = 0
    external: int = 0
    categories: list[CategorySummary] = field(default_factory=list)
    model_url: str = ""
    brand_url: str = ""
    brand_name: str = ""
    model_name: str = ""
    chipsets: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class InventoryRow:
    name: str
    variant: str
    build: str
    android: str
    package: str
    size: str
    external: bool
    detail_url: str
    download_url: str


@dataclass
class InventoryGroup:
    key: str
    label: str
    rows: list[InventoryRow]


def firmware_inventory(post: Post) -> list[InventoryGroup]:
    """Approved firmware of the post's model grouped by category, with download links."""
    from apps.firmwares import catalog

    model = post.firmware_model
    if model is None:
        return []
    groups = []
    for key, label, items in catalog.group_by_category(
        catalog.get_all_firmwares_for_model(model)
    ):
        kwargs = [
            {"firmware_type": fw.firmware_type, "firmware_id": str(fw.pk)}
            for fw in items
        ]
        groups.append(
            InventoryGroup(
                key=key,
                label=label,
                rows=[
                    InventoryRow(
                        name=fw.original_file_name,
                        variant=fw.variant.name if fw.variant_id else "",
                        build=fw.build_number or "",
                        android=fw.android_version or "",
                        package=fw.get_package_format_display(),
                        size=catalog.format_file_size(fw.file_size),
                        external=bool(fw.is_external),
                        detail_url=reverse("firmwares:firmware_detail", kwargs=args),
                        download_url=reverse(
                            "firmwares:firmware_download", kwargs=args
                        ),
                    )
                    for fw, args in zip(items, kwargs, strict=True)
                ],
            )
        )
    return groups


def firmware_summary(post: Post) -> FirmwareSummary | None:
    """Approved-firmware counts per category for the post's model, or ``None``."""
    from apps.firmwares import catalog

    model = post.firmware_model
    brand = post.firmware_brand
    if model is None and brand is None:
        return None
    if model is not None:
        firmwares = catalog.get_all_firmwares_for_model(model)
    else:
        firmwares = catalog.get_public_firmwares(brand=brand)
    summary = FirmwareSummary(
        total=len(firmwares),
        external=sum(1 for firmware in firmwares if firmware.is_external),
    )
    if brand is not None:
        summary.brand_name = brand.name
        summary.brand_url = reverse(
            "firmwares:brand_detail", kwargs={"slug": brand.slug}
        )
    if model is not None and brand is not None:
        summary.model_name = model.name
        summary.model_url = reverse(
            "firmwares:model_detail",
            kwargs={"brand_slug": brand.slug, "model_slug": model.slug},
        )
        chipset = model.chipset_ref
        if chipset is not None:
            summary.chipsets.append(
                (
                    chipset.display_name,
                    reverse("firmwares:chipset_detail", kwargs={"slug": chipset.slug}),
                )
            )
    for key, label, items in catalog.group_by_category(firmwares):
        params = {"type": key, "q": model.name if model is not None else ""}
        if brand is not None:
            params["brand"] = brand.slug
        summary.categories.append(
            CategorySummary(
                key=key,
                label=label,
                count=len(items),
                browse_url=f"{reverse('firmwares:browse')}?{urlencode(params)}",
            )
        )
    return summary

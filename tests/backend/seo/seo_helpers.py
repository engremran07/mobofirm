from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.blog.models import Category, Post, PostStatus
from apps.firmwares.models import (
    Brand,
    Chipset,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
)
from apps.pages.models import Page
from apps.tags.models import Tag

SITE = "https://example.org"


@dataclass
class Catalog:
    brand: Brand
    chipset: Chipset
    model: Model
    firmware: OfficialFirmware
    tool: FlashingTool
    post: Post
    category: Category
    tag: Tag
    page: Page


def make_author(name: str = "seo_author"):
    return get_user_model().objects.create_user(
        username=name, email=f"{name}@example.com", password=uuid.uuid4().hex
    )


def make_catalog() -> Catalog:
    brand = Brand.objects.create(
        name="Nova", slug="nova", description="Nova makes phones."
    )
    chipset = Chipset.objects.create(
        vendor="MediaTek", name="Helio G100", slug="mediatek-helio-g100"
    )
    model = Model.objects.create(
        brand=brand,
        name="Atlas One",
        slug="atlas-one",
        model_code="NS-A1",
        chipset_ref=chipset,
        chipset="MediaTek Helio G100",
        description="The Atlas One is a mid-range phone.",
    )
    firmware = OfficialFirmware.objects.create(
        original_file_name="ATLAS_ONE_V1.zip",
        stored_file_path="/firmware/ATLAS_ONE_V1.zip",
        brand=brand,
        model=model,
        build_number="V1.0.3",
        android_version="14",
        is_verified=True,
    )
    category = Category.objects.create(name="Guides", slug="guides")
    author = make_author()
    post = Post.objects.create(
        title="How to flash the Atlas One",
        slug="how-to-flash-the-atlas-one",
        body="<p>Back up first, then flash the stock firmware.</p>",
        summary="A short flashing guide.",
        author=author,
        category=category,
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now(),
        firmware_brand=brand,
        firmware_model=model,
    )
    tag = Tag.objects.create(name="Flashing", slug="flashing", is_curated=True)
    post.tags.add(tag)
    tool_category = FlashingToolCategory.objects.create(name="Open", slug="open")
    tool = FlashingTool.objects.create(
        category=tool_category,
        name="Flasher",
        slug="flasher",
        description="Flashes stock firmware.",
        version="2.1",
    )
    tool.chipsets.add(chipset)
    tool.supported_brands.add(brand)
    page = Page.objects.create(
        slug="about",
        title="About",
        content="<p>About us.</p>",
        status="published",
        seo_description="About this site.",
    )
    return Catalog(brand, chipset, model, firmware, tool, post, category, tag, page)


def make_staff(username: str = "reviewer", *codenames: str, superuser: bool = False):
    """A staff account holding exactly the named model permissions."""
    from django.contrib.auth.models import Permission

    user = get_user_model().objects.create_user(
        username=username, email=f"{username}@example.com", password="pass-12345-x"
    )
    user.is_staff = True
    user.is_superuser = superuser
    user.save()
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


def make_post(slug: str, title: str, author, category, **fields) -> Post:
    return Post.objects.create(
        title=title,
        slug=slug,
        author=author,
        category=category,
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now(),
        **fields,
    )

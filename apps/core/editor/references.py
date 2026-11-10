"""Where rich text is stored, so unreferenced editor images can be found.

Each entry is ``"app_label.ModelName.field"`` (strings only: ``apps.core`` must not
import the owning apps). The orphan cleanup scans exactly these fields for
``editor/...`` image paths; adding an editor to a new model field means listing it
here, otherwise its images would look orphaned.
"""

from __future__ import annotations

RICH_TEXT_FIELDS: tuple[str, ...] = (
    "blog.Post.body",
    "blog.PostTranslation.body",
    "blog.GuideRequest.description",
    "blog.GuideRequest.tried",
    "comments.Comment.body",
    "pages.Page.content",
    "marketplace.Listing.description",
    "promotions.Promotion.description",
    "promotions.Promotion.terms",
    "firmwares.ReleaseNote.description",
    "firmwares.BrandCreationRequest.details",
    "firmwares.ModelCreationRequest.details",
    "firmwares.VariantCreationRequest.details",
    "firmwares.ChipsetCreationRequest.details",
    "firmwares.FlashingTool.description",
    "firmwares.FlashingTool.instructions",
)

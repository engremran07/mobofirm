"""
Firmware Blog Automation Service
Automatically creates blog posts for firmware uploads
Generates well-structured content with SEO optimization

TWO TYPES OF BLOG POSTS:
1. Manual Posts: Created by admins/users through blog interface
2. Auto-Generated Posts: Created automatically when firmware is uploaded
   - Marked with is_ai_generated=True flag
   - Category structure: Brand (parent) → Model (child)
   - Auto-updates when new firmware files added
"""

import logging

from django.apps import apps
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

logger = logging.getLogger(__name__)


class FirmwareBlogService:
    """Handles automatic blog post generation for firmware uploads"""

    @classmethod
    def get_auto_generated_posts(cls):
        """Get all auto-generated firmware posts (vs manual posts)"""
        if not apps.is_installed("apps.blog"):
            return None

        try:
            Post = apps.get_model("blog", "Post")
            return Post.objects.filter(is_ai_generated=True, is_published=True)
        except Exception as e:
            logger.exception(f"Error getting auto-generated posts: {e}")
            return None

    @classmethod
    def get_manual_posts(cls):
        """Get all manually created posts (vs auto-generated)"""
        if not apps.is_installed("apps.blog"):
            return None

        try:
            Post = apps.get_model("blog", "Post")
            return Post.objects.filter(is_ai_generated=False, is_published=True)
        except Exception as e:
            logger.exception(f"Error getting manual posts: {e}")
            return None

    @classmethod
    def ensure_brand_category(cls, brand):
        """Create or get blog category for brand.

        Name is the brand name only — no extra text like "Firmware".
        Slug: ``<brand-slug>`` (e.g. ``samsung``, ``xiaomi``, ``acer``).
        Migrates legacy ``brand-<slug>`` and ``<slug>-firmware`` entries.
        """
        if not apps.is_installed("apps.blog"):
            return None

        try:
            Category = apps.get_model("blog", "Category")

            clean_slug = brand.slug

            # Try clean slug first (new canonical format)
            category = Category.objects.filter(slug=clean_slug).first()
            if category:
                # Fix name if it still has "Firmware" suffix
                if category.name != brand.name:
                    category.name = brand.name
                    category.save(update_fields=["name"])
                return category

            # Check for legacy slug variants and migrate
            for legacy_slug in [f"{brand.slug}-firmware", f"brand-{brand.slug}"]:
                legacy = Category.objects.filter(slug=legacy_slug).first()
                if legacy:
                    legacy.slug = clean_slug
                    legacy.name = brand.name
                    legacy.save(update_fields=["slug", "name"])
                    logger.info(
                        "Migrated brand category: %s → %s (%s)",
                        legacy_slug,
                        clean_slug,
                        brand.name,
                    )
                    return legacy

            # Create new — brand name only, no "Firmware" suffix
            category = Category.objects.create(
                slug=clean_slug,
                name=brand.name,
            )
            logger.info("Created blog category for brand: %s", brand.name)
            return category
        except Exception as e:
            logger.exception(f"Error creating brand category: {e}")
            return None

    @classmethod
    def ensure_model_category(cls, model):
        """Create or get blog category for model (child of brand category).

        Name is the model name only — no brand prefix, no redundant text.
        If the model has a marketing name different from its code name,
        the display is ``Model Name (Marketing Name)``.
        Slug: ``<brand>-<model>`` for uniqueness.
        Migrates legacy ``model-<slug>`` entries.
        """
        if not apps.is_installed("apps.blog"):
            return None

        try:
            Category = apps.get_model("blog", "Category")

            # Ensure parent brand category exists first
            parent_category = cls.ensure_brand_category(model.brand)

            # Slug keeps brand prefix for global uniqueness
            new_slug = slugify(f"{model.brand.slug}-{model.slug}")

            # Name is model-only — brand is already the parent category
            # Only add marketing name if it contributes new info beyond brand+name
            name = model.name
            if model.marketing_name and model.marketing_name != model.name:
                # Strip brand prefix from marketing name to check if it's actually different
                mkt_clean = model.marketing_name
                brand_prefix = f"{model.brand.name} "
                if mkt_clean.startswith(brand_prefix):
                    mkt_clean = mkt_clean[len(brand_prefix) :]
                if mkt_clean != model.name:
                    name = f"{model.name} ({mkt_clean})"

            # Try new slug first
            category = Category.objects.filter(slug=new_slug).first()
            if category:
                changed = False
                # Fix name if it still has brand prefix
                if category.name != name:
                    category.name = name
                    changed = True
                if category.parent != parent_category:
                    category.parent = parent_category
                    changed = True
                if changed:
                    category.save(update_fields=["name", "parent"])
                return category

            # Check for legacy slug (model-<slug>)
            legacy = Category.objects.filter(slug=f"model-{model.slug}").first()
            if legacy:
                legacy.slug = new_slug
                legacy.name = name
                if parent_category:
                    legacy.parent = parent_category
                legacy.save(update_fields=["slug", "name", "parent"])
                logger.info(
                    "Migrated model category: model-%s → %s (%s)",
                    model.slug,
                    new_slug,
                    name,
                )
                return legacy

            # Create new — model name only, brand is the parent
            category = Category.objects.create(
                slug=new_slug,
                name=name,
                parent=parent_category,
            )
            logger.info(
                "Created blog category for model: %s (slug: %s)", name, new_slug
            )
            return category
        except Exception as e:
            logger.exception(f"Error creating model category: {e}")
            return None

    @classmethod
    def generate_firmware_post(cls, model, force_update=False):
        """
        Create or refresh a firmware post's editorial copy before governance review.

        Args:
            model: Model instance
            force_update: If True, create a post even when the model has no listings.
        """
        if not apps.is_installed("apps.blog"):
            logger.warning("Blog app not installed - skipping post generation")
            return None
        if not apps.is_installed("apps.governance"):
            logger.error("Governance app is required for firmware blog publication")
            return None

        try:
            Post = apps.get_model("blog", "Post")
            apps.get_model("tags", "Tag")

            # Gather all firmwares for this model across all variants
            firmware_data = cls._collect_firmware_data(model)

            if not firmware_data["total_files"] and not force_update:
                logger.info(f"No firmware files for {model.name} - skipping post")
                return None

            # Generate post content
            post_slug = slugify(f"{model.brand.slug}-{model.slug}-firmware-download")

            # Smart title — include marketing name when it differs
            device_label = f"{model.brand.name} {model.name}"
            if model.marketing_name and model.marketing_name != model.name:
                device_label = (
                    f"{model.brand.name} {model.name} ({model.marketing_name})"
                )
            title = f"{device_label} Firmware Download — All Variants"

            # Check if post exists
            existing_post = Post.objects.filter(slug=post_slug).first()

            if existing_post:
                if not existing_post.is_ai_generated:
                    logger.warning(
                        "Firmware post slug %s belongs to a manually authored post",
                        post_slug,
                    )
                    return None
                if existing_post.status != "draft":
                    logger.info(
                        "Leaving non-draft firmware post %s unchanged; "
                        "its firmware table is loaded from the live catalog",
                        existing_post.pk,
                    )
                    return existing_post

                with transaction.atomic():
                    category = cls.ensure_model_category(model)
                    existing_post.body = cls._generate_post_body(model, firmware_data)
                    existing_post.summary = cls._generate_summary(model)
                    existing_post.category = category
                    existing_post.firmware_brand = model.brand
                    existing_post.firmware_model = model
                    existing_post.save()
                    cls._update_post_tags(existing_post, model, firmware_data)
                    cls._update_post_seo(existing_post, model)
                    from apps.governance.services import submit_for_review

                    submit_for_review(existing_post, actor=existing_post.author)

                logger.info(
                    "Submitted updated firmware post for review: %s", model.name
                )
                return existing_post

            # Get system user for author (or first superuser)
            from django.contrib.auth import get_user_model

            User = get_user_model()
            author = User.objects.filter(is_staff=True).first()

            if not author:
                logger.warning("No staff user found for blog post author")
                return None

            with transaction.atomic():
                category = cls.ensure_model_category(model)
                cls.ensure_brand_category(model.brand)
                post = Post.objects.create(
                    title=title,
                    slug=post_slug,
                    body=cls._generate_post_body(model, firmware_data),
                    summary=cls._generate_summary(model),
                    author=author,
                    category=category,
                    firmware_brand=model.brand,
                    firmware_model=model,
                    status="draft",
                    allow_comments=True,
                    is_ai_generated=True,
                )
                cls._update_post_tags(post, model, firmware_data)
                cls._update_post_seo(post, model)
                from apps.governance.services import submit_for_review

                submit_for_review(post, actor=author)

            logger.info(
                "Created and submitted firmware post for review: %s", model.name
            )
            return post

        except Exception as e:
            logger.exception(f"Error generating firmware post: {e}")
            raise

    @classmethod
    def _collect_firmware_data(cls, model):
        """Collect public firmware data for a model, grouped by variant."""
        data = {
            "variants": [],
            "total_files": 0,
            "firmware_types": set(),
        }
        from .public_views import get_all_firmwares_for_model

        variants = {}
        for firmware in get_all_firmwares_for_model(model):
            display_name = firmware.firmware_type_display
            data["firmware_types"].add(display_name)
            data["total_files"] += 1
            if firmware.variant_id is None:
                continue
            variant_data = variants.setdefault(
                firmware.variant_id,
                {"variant": firmware.variant, "firmwares": []},
            )
            variant_data["firmwares"].append(
                {
                    "type": display_name,
                    "firmware": firmware,
                    "chipset": firmware.chipset or "N/A",
                    "file_name": firmware.original_file_name,
                    "is_password_protected": firmware.is_password_protected,
                    "storage_info": cls._get_firmware_storage_info(firmware),
                }
            )
        data["variants"] = list(variants.values())

        return data

    @classmethod
    def _get_firmware_storage_info(cls, firmware):
        """Return upload metadata owned by the firmware model."""
        return {
            "is_uploaded": bool(getattr(firmware, "stored_file_path", "")),
            "file_size": getattr(firmware, "file_size", None),
            "download_count": getattr(firmware, "download_count", 0),
        }

    @classmethod
    def _generate_post_body(cls, model, firmware_data):
        """Generate SamFW/MiFirm-inspired HTML content for firmware blog post.

        Structure:
        1. Device summary block (model name, marketing name, model codes)
        2. Live firmware inventory supplied by the post-detail view
        3. Device specifications summary (from GSMArena if available)
        4. Safety notes
        """
        html = []
        brand_name = model.brand.name
        model_name = model.name
        device_label = f"{brand_name} {model_name}"

        # Marketing name / alternate names
        alt_names = []
        if model.marketing_name and model.marketing_name != model_name:
            alt_names.append(model.marketing_name)
        if model.model_code:
            alt_names.append(model.model_code)

        # --- Header ---
        html.append(f"<h2>Download {device_label} Firmware — All Variants</h2>")
        html.append(
            f"<p>Complete firmware collection for <strong>{device_label}</strong>"
        )
        if alt_names:
            html.append(f" (also known as: {', '.join(alt_names)})")
        html.append(
            ". Approved firmware files are listed below from the live catalog.</p>"
        )

        html.append(
            '<div style="background:rgba(59,130,246,0.08);border-left:4px solid '
            'rgba(59,130,246,0.6);padding:12px 16px;border-radius:4px;margin:16px 0">'
            "<strong>Auto-Updated:</strong> This firmware list updates automatically "
            "when new files are uploaded. Check back for the latest versions.</div>"
        )

        # --- GSMArena specs summary (if we can find it) ---
        try:
            GSMArenaDevice = apps.get_model("firmwares", "GSMArenaDevice")
            gsm = GSMArenaDevice.objects.filter(
                brand__iexact=brand_name,
                model_name__iexact=model_name,
                review_status="approved",
            ).first()
            if gsm and gsm.specs:
                html.append('<h3 style="margin-top:24px">📋 Device Specifications</h3>')
                html.append(
                    '<table style="width:100%;border-collapse:collapse;text-align:left">'
                    "<tbody>"
                )
                skip_keys = {
                    "url",
                    "brand",
                    "full_name",
                    "brand_slug",
                    "scraped_at",
                    "image_url",
                    "review_url",
                }
                for k, v in gsm.specs.items():
                    if k in skip_keys or not v:
                        continue
                    label = k.replace("_", " ").title()
                    display = "Yes" if v is True else ("No" if v is False else str(v))
                    html.append(
                        f'<tr style="border-bottom:1px solid #333">'
                        f'<td style="padding:6px 12px;font-weight:600;'
                        f"min-width:140px;color:#999;font-size:12px;"
                        f'text-transform:uppercase">{label}</td>'
                        f'<td style="padding:6px 12px">{display}</td></tr>'
                    )
                html.append("</tbody></table>")
        except Exception as e:
            logger.debug(f"Skipped GSMArena specs in blog post: {e}")

        # --- Recommended Flashing Tools ---
        chipsets = cls._extract_chipsets(firmware_data)
        tools_html = cls._generate_flashing_tools_section(model, chipsets)
        if tools_html:
            html.append(tools_html)

        # --- Flashing Guide ---
        guide_html = cls._generate_flashing_guide_section(model, chipsets)
        if guide_html:
            html.append(guide_html)

        # --- Safety notes ---
        html.append(
            '<div style="margin-top:24px;background:rgba(245,158,11,0.08);'
            "border-left:4px solid rgba(245,158,11,0.6);padding:12px 16px;"
            'border-radius:4px">'
            "<strong>⚠️ Important:</strong>"
            "<ul style='margin:8px 0 0;padding-left:20px'>"
            "<li>Always back up your data before flashing firmware</li>"
            "<li>Verify your device variant and region match the firmware</li>"
            "<li>Check chipset compatibility before flashing</li>"
            "<li>Password-protected files require an extraction password</li>"
            "</ul></div>"
        )

        html.append(
            f'<p style="margin-top:16px;color:#666;font-size:12px">'
            f"Last updated: {timezone.now().strftime('%B %d, %Y')}</p>"
        )

        return "".join(html)

    @classmethod
    def _generate_summary(cls, model):
        """Generate post summary/excerpt with smart naming."""
        device_label = f"{model.brand.name} {model.name}"
        if model.marketing_name and model.marketing_name != model.name:
            device_label += f" ({model.marketing_name})"

        return f"Download {device_label} firmware for all variants."

    @classmethod
    def _extract_chipsets(cls, firmware_data: dict) -> list[str]:
        """Extract unique chipset names from firmware variant data."""
        chipsets: set[str] = set()
        for variant_data in firmware_data.get("variants", []):
            variant = variant_data.get("variant")
            if variant and getattr(variant, "chipset", ""):
                chipsets.add(variant.chipset)
            for fw in variant_data.get("firmwares", []):
                chipset = fw.get("chipset", "")
                if chipset and chipset not in ("N/A", "Unknown", ""):
                    chipsets.add(chipset)
        return sorted(chipsets)

    @classmethod
    def _generate_flashing_tools_section(cls, model, chipsets: list[str]) -> str:
        """Generate HTML section listing recommended flashing tools for a brand/chipset.

        Groups tools into: OEM Official → Free/Open Source → Crack/Local Market.
        Each tool shows name, platform, risk level, and link to detail page.
        """
        try:
            FlashingTool = apps.get_model("firmwares", "FlashingTool")
        except Exception:
            return ""

        brand = model.brand

        # Query tools matching this brand OR chipset
        from django.db.models import Q

        tool_q = Q(is_active=True)
        brand_q = Q(supported_brands=brand)
        chipset_q = Q()
        for chip in chipsets:
            chipset_q |= Q(supported_chipsets__contains=chip)

        # Brand-specific tools + chipset-specific tools
        tools_qs = FlashingTool.objects.filter(tool_q).filter(brand_q | chipset_q)
        tools = list(tools_qs.distinct().select_related("category")[:20])

        if not tools:
            # Fallback: get generic popular tools
            tools = list(
                FlashingTool.objects.filter(
                    is_active=True, is_featured=True
                ).select_related("category")[:8]
            )
        if not tools:
            return ""

        # Group by tool_type for display order
        type_order = ["oem", "free", "open_source", "local_market", "crack"]
        type_labels = {
            "oem": (
                "🏭 OEM Official Tools",
                "Official manufacturer tools — safest option",
            ),
            "free": ("🆓 Free Tools", "Freeware tools — no cost, community-supported"),
            "open_source": (
                "🔓 Open Source Tools",
                "Open source — auditable and trustworthy",
            ),
            "local_market": (
                "🏪 Local Market Tools",
                "Regional service tools — use with caution",
            ),
            "crack": (
                "⚠️ Crack / Patched Tools",
                "Modified tools — advanced users only, use at your own risk",
            ),
        }
        grouped: dict[str, list] = {}
        for tool in tools:
            tt = tool.tool_type
            grouped.setdefault(tt, []).append(tool)

        html_parts: list[str] = []
        brand_name = brand.name
        chipset_str = ", ".join(chipsets) if chipsets else "various chipsets"

        html_parts.append(
            f'<h3 style="margin-top:24px">🔧 Recommended Flashing Tools for {brand_name}</h3>'
        )
        html_parts.append(
            f"<p>These tools are compatible with <strong>{brand_name}</strong> devices"
        )
        if chipsets:
            html_parts.append(f" ({chipset_str} chipset)")
        html_parts.append(
            ". Choose the right tool based on your experience level and needs.</p>"
        )

        for tt in type_order:
            if tt not in grouped:
                continue
            label, desc = type_labels.get(tt, (tt.title(), ""))
            html_parts.append(f'<h4 style="margin-top:16px">{label}</h4>')
            html_parts.append(f'<p style="font-size:13px;color:#999">{desc}</p>')
            html_parts.append(
                '<table style="width:100%;border-collapse:collapse;text-align:left">'
                "<thead><tr>"
                '<th style="padding:6px 12px;border-bottom:2px solid #444">Tool</th>'
                '<th style="padding:6px 12px;border-bottom:2px solid #444">Platform</th>'
                '<th style="padding:6px 12px;border-bottom:2px solid #444">Risk</th>'
                '<th style="padding:6px 12px;border-bottom:2px solid #444">Price</th>'
                '<th style="padding:6px 12px;border-bottom:2px solid #444">Action</th>'
                "</tr></thead><tbody>"
            )
            for tool in grouped[tt]:
                risk_color = {
                    "safe": "#10b981",
                    "moderate": "#f59e0b",
                    "advanced": "#f97316",
                    "risky": "#ef4444",
                }.get(tool.risk_level, "#64748b")
                platform_icon = {
                    "windows": "🪟",
                    "macos": "🍎",
                    "linux": "🐧",
                    "android": "🤖",
                    "multi": "🌐",
                }.get(tool.platform, "💻")
                price_text = "Free" if tool.is_free else "Paid"
                price_color = "#10b981" if tool.is_free else "#f59e0b"

                # Link to tool detail page
                tool_url = f"/firmwares/tools/{tool.slug}/"

                html_parts.append(
                    f'<tr style="border-bottom:1px solid #333">'
                    f'<td style="padding:6px 12px"><strong>{tool.name}</strong>'
                )
                if tool.version:
                    html_parts.append(
                        f' <span style="font-size:11px;color:#666">v{tool.version}</span>'
                    )
                html_parts.append("</td>")
                html_parts.append(
                    f'<td style="padding:6px 12px">{platform_icon} {tool.get_platform_display()}</td>'
                )
                html_parts.append(
                    f'<td style="padding:6px 12px">'
                    f'<span style="color:{risk_color};font-weight:600">'
                    f"{tool.get_risk_level_display()}</span></td>"
                )
                html_parts.append(
                    f'<td style="padding:6px 12px">'
                    f'<span style="color:{price_color}">{price_text}</span></td>'
                )
                html_parts.append(
                    f'<td style="padding:6px 12px">'
                    f'<a href="{tool_url}" style="color:#06b6d4;text-decoration:none;'
                    f'font-weight:600;font-size:13px">View Details →</a></td>'
                )
                html_parts.append("</tr>")
            html_parts.append("</tbody></table>")

        return "".join(html_parts)

    @classmethod
    def _generate_flashing_guide_section(cls, model, chipsets: list[str]) -> str:
        """Generate a quick-start flashing guide section based on chipset/brand.

        Detects the primary chipset and provides chipset-specific instructions
        plus links to full guide pages.
        """
        brand_name = model.brand.name
        model_name = model.name
        device = f"{brand_name} {model_name}"

        # Detect primary chipset family for tailored instructions
        chipset_lower = " ".join(chipsets).lower() if chipsets else ""
        guide_steps: list[str] = []
        tool_name = ""
        boot_mode = ""

        if "mediatek" in chipset_lower or "mtk" in chipset_lower:
            tool_name = "SP Flash Tool"
            boot_mode = "BROM/Preloader mode"
            guide_steps = [
                f"Download the correct stock firmware for your {device}",
                "Install MediaTek USB VCOM drivers on your PC",
                f"Open <strong>{tool_name}</strong> and load the scatter file from the firmware package",
                "Select <strong>Download Only</strong> (preserves data) or <strong>Firmware Upgrade</strong> (full wipe)",
                f"Power off your device and connect via USB to boot into {boot_mode}",
                "Click <strong>Download</strong> and wait for the green checkmark ✓",
                "Disconnect and reboot — first boot may take 5-10 minutes",
            ]
        elif "qualcomm" in chipset_lower or "snapdragon" in chipset_lower:
            tool_name = "QFIL (Qualcomm Flash Image Loader)"
            boot_mode = "EDL mode (Qualcomm 9008)"
            guide_steps = [
                f"Download the correct stock firmware for your {device}",
                "Install Qualcomm HS-USB QDLoader 9008 drivers",
                f"Open <strong>{tool_name}</strong> and select Flat Build",
                "Browse to <code>rawprogram0.xml</code> and <code>patch0.xml</code>",
                f"Boot your device into {boot_mode} (hold Vol Up + Vol Down while connecting USB)",
                "Click <strong>Download</strong> and wait for completion",
                "Device will reboot automatically when done",
            ]
        elif "spreadtrum" in chipset_lower or "unisoc" in chipset_lower:
            tool_name = "SPD Research Tool"
            boot_mode = "Download mode"
            guide_steps = [
                f"Download the correct .pac firmware for your {device}",
                "Install Spreadtrum/Unisoc USB drivers",
                f"Open <strong>{tool_name}</strong> and load the .pac file",
                f"Power off device, hold Vol Down and connect USB ({boot_mode})",
                "Click <strong>Start Downloading</strong>",
                "Wait for the progress bar to complete (do not disconnect)",
                "Device reboots automatically when flashing is done",
            ]
        elif "samsung" in brand_name.lower():
            tool_name = "Odin"
            boot_mode = "Download mode"
            guide_steps = [
                f"Download the correct firmware for your {device} (AP, BL, CP, CSC files)",
                f"Open <strong>{tool_name}</strong> on your PC",
                "Load firmware files: AP → AP slot, BL → BL slot, CP → CP slot, CSC → CSC slot",
                f"Boot into {boot_mode}: Power Off → hold Vol Down + Home + Power (or Vol Down + Bixby + Power)",
                "Connect device via USB — Odin should show <strong>Added!!</strong>",
                'Click <strong>Start</strong> and wait for the <strong style="color:#10b981">PASS!</strong> message',
                "Device reboots automatically — first boot may take several minutes",
            ]
        elif "xiaomi" in brand_name.lower():
            tool_name = "MiFlash Tool"
            boot_mode = "Fastboot mode"
            guide_steps = [
                f"Download the Fastboot ROM for your {device}",
                f"Install <strong>{tool_name}</strong> and Xiaomi USB drivers",
                f"Boot into {boot_mode}: Power Off → hold Vol Down + Power",
                "Connect device via USB",
                "Select firmware folder in MiFlash and click <strong>Refresh</strong>",
                "Choose flash option: <strong>clean all</strong> or <strong>save user data</strong>",
                "Click <strong>Flash</strong> and wait for completion",
            ]
        else:
            # Generic Android flashing guide
            tool_name = "manufacturer's flash tool"
            boot_mode = "Download/Fastboot mode"
            guide_steps = [
                f"Download the correct stock firmware for your {device}",
                "Install USB drivers for your device brand",
                f"Open the {tool_name} on your PC",
                "Load the firmware file(s) into the tool",
                f"Power off device and boot into {boot_mode}",
                "Connect device via USB and start the flashing process",
                "Wait for completion and reboot — first boot may take several minutes",
            ]

        html_parts: list[str] = []
        html_parts.append(
            f'<h3 style="margin-top:24px">📖 How to Flash Firmware on {device}</h3>'
        )
        if tool_name and tool_name != "manufacturer's flash tool":
            html_parts.append(f"<p>The recommended tool for flashing {brand_name} ")
            if chipsets:
                html_parts.append(f"({', '.join(chipsets)}) ")
            html_parts.append(
                f"devices is <strong>{tool_name}</strong>. "
                f"Follow these steps to flash firmware on your {device}:</p>"
            )
        else:
            html_parts.append(
                f"<p>Follow these general steps to flash firmware on your {device}:</p>"
            )

        html_parts.append('<ol style="padding-left:20px;line-height:1.8">')
        for step in guide_steps:
            html_parts.append(f"<li>{step}</li>")
        html_parts.append("</ol>")

        # Prerequisites box
        html_parts.append(
            '<div style="margin-top:12px;background:rgba(59,130,246,0.08);'
            "border-left:4px solid rgba(59,130,246,0.6);padding:12px 16px;"
            'border-radius:4px">'
            "<strong>📋 Prerequisites:</strong>"
            "<ul style='margin:8px 0 0;padding-left:20px'>"
            "<li>Battery charged to at least 50%</li>"
            "<li>Original USB cable (avoid cheap/third-party cables)</li>"
            "<li>USB drivers installed for your device</li>"
            "<li>Full data backup (flashing may erase all data)</li>"
            "</ul></div>"
        )

        return "".join(html_parts)

    @classmethod
    def _update_post_tags(cls, post, model, firmware_data):
        """
        Auto-generate and assign tags to the firmware post.
        """
        if not apps.is_installed("apps.tags"):
            return

        try:
            Tag = apps.get_model("tags", "Tag")
            max_auto_tags = 15

            tags_to_add = []

            def curated_tag(name):
                slug = slugify(name)
                tag = Tag.objects.filter(
                    slug=slug,
                    is_curated=True,
                    is_active=True,
                    is_deleted=False,
                    merge_into__isnull=True,
                ).first()
                if tag:
                    return tag
                Tag.objects.get_or_create(
                    slug=slug,
                    defaults={
                        "name": name,
                        "ai_suggested": True,
                        "is_curated": False,
                    },
                )
                return None

            # Brand tag (priority 1)
            brand_tag = curated_tag(model.brand.name)
            if brand_tag:
                tags_to_add.append(brand_tag)

            # Model tag (priority 2)
            model_tag = curated_tag(f"{model.brand.name} {model.name}")
            if model_tag:
                tags_to_add.append(model_tag)

            # Firmware type tags (priority 3) - limit to 3 most common types
            firmware_types = list(firmware_data["firmware_types"])[:3]
            for fw_type in firmware_types:
                type_tag = curated_tag(f"{fw_type} Firmware")
                if type_tag:
                    tags_to_add.append(type_tag)

            # Chipset tags (priority 4) - extract from firmware variants
            chipsets = set()
            for variant_data in firmware_data.get("variants", []):
                for fw in variant_data.get("firmwares", []):
                    chipset = fw.get("chipset", "")
                    if chipset and chipset not in ("N/A", "Unknown", ""):
                        chipsets.add(chipset)

            for chipset in list(chipsets)[:3]:  # Limit to 3 chipsets
                chipset_tag = curated_tag(chipset)
                if chipset_tag:
                    tags_to_add.append(chipset_tag)

            # Generic tags (priority 5) - only essential ones
            generic_tags = ["firmware", "download"]
            for tag_name in generic_tags:
                tag = curated_tag(tag_name.replace("-", " ").title())
                if tag:
                    tags_to_add.append(tag)

            # Apply limit from admin settings (configurable)
            tags_to_add = list({tag.pk: tag for tag in tags_to_add}.values())[
                :max_auto_tags
            ]

            post.tags.set(tags_to_add)
            logger.info(
                "Added %s tags to post including %s chipsets (maximum %s)",
                len(tags_to_add),
                len(chipsets),
                max_auto_tags,
            )

            # Sync tag usage counts
            try:
                from apps.tags.tasks import (
                    sync_tag_usage_counts,  # type: ignore[attr-defined]
                )

                tag_ids = list(post.tags.values_list("id", flat=True))
                if tag_ids:
                    sync_tag_usage_counts.delay(tag_ids)
            except Exception as e:
                logger.debug(f"Tag usage sync skipped: {e}")

        except Exception as e:
            logger.exception(f"Error adding tags: {e}")

    @classmethod
    def _update_post_seo(cls, post, model):
        """
        Generate bounded title and description fields on the local blog post.
        """
        try:
            max_seo_title = 60
            max_seo_desc = 160

            # SEO Title
            seo_title = f"{model.brand.name} {model.name} Firmware Download"
            if len(seo_title) > max_seo_title:
                seo_title = f"{model.brand.name} {model.name} Firmware"
            if len(seo_title) > max_seo_title:
                seo_title = seo_title[: max_seo_title - 3] + "..."
            post.seo_title = seo_title

            # SEO Description
            seo_desc = (
                f"Download {model.brand.name} {model.name} firmware files "
                "for all variants."
            )

            if len(seo_desc) > max_seo_desc:
                seo_desc = seo_desc[: max_seo_desc - 3] + "..."

            post.seo_description = seo_desc
            post.save(update_fields=["seo_title", "seo_description"])

            logger.info(
                "Updated SEO (title: %s/%s, description: %s/%s)",
                len(post.seo_title),
                max_seo_title,
                len(post.seo_description),
                max_seo_desc,
            )

        except Exception as e:
            logger.exception(f"Error updating SEO: {e}")

"""
Auto-Fill API Endpoints
Provides endpoints for auto-filling firmware metadata
"""

import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.firmwares.admin_autofill import FirmwareAutoFill
from apps.firmwares.models import Brand, Model, Variant

logger = logging.getLogger(__name__)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def autofill_brand(request, brand_id):
    """
    Auto-fill brand metadata
    """
    try:
        brand = get_object_or_404(Brand, pk=brand_id)

        # Fetch data from sources
        data = FirmwareAutoFill.autofill_brand(brand.name)

        # Update brand
        updated = False
        if data.get("description") and not getattr(brand, "description", ""):
            brand.description = data["description"]  # type: ignore[attr-defined]
            updated = True

        if updated:
            brand.save()
            logger.info(f"Auto-filled brand: {brand.name}")

        return Response(
            {
                "success": True,
                "message": "Brand auto-filled successfully",
                "data": data,
                "updated": updated,
            }
        )

    except Exception as e:
        logger.exception(f"Auto-fill brand failed: {e}")
        return Response(
            {"success": False, "error": str(e)}, status=status.HTTP_400_BAD_REQUEST
        )


@api_view(["POST"])
@permission_classes([IsAdminUser])
def autofill_model(request, model_id):
    """
    Auto-fill model metadata
    """
    try:
        model = get_object_or_404(Model, pk=model_id)

        # Fetch data from sources
        data = FirmwareAutoFill.autofill_model(model.brand.name, model.name)

        # Update model
        updated = False
        update_fields = []

        if data.get("marketing_name") and not model.marketing_name:
            model.marketing_name = data["marketing_name"]
            update_fields.append("marketing_name")
            updated = True

        if data.get("model_code") and not model.model_code:
            model.model_code = data["model_code"]
            update_fields.append("model_code")
            updated = True

        if data.get("description") and not model.description:
            model.description = data["description"]
            update_fields.append("description")
            updated = True

        if updated:
            model.save(update_fields=update_fields)
            logger.info(f"Auto-filled model: {model.brand.name} {model.name}")

        return Response(
            {
                "success": True,
                "message": "Model auto-filled successfully",
                "data": data,
                "updated": updated,
            }
        )

    except Exception as e:
        logger.exception(f"Auto-fill model failed: {e}")
        return Response(
            {"success": False, "error": str(e)}, status=status.HTTP_400_BAD_REQUEST
        )


@api_view(["POST"])
@permission_classes([IsAdminUser])
def autofill_variant(request, variant_id):
    """
    Auto-fill variant metadata
    """
    try:
        variant = get_object_or_404(Variant, pk=variant_id)

        # Fetch data from sources
        data = FirmwareAutoFill.autofill_variant(
            variant.model.brand.name, variant.model.name, variant.region
        )

        # Update variant
        updated = False
        update_fields = []

        if data.get("chipset") and not variant.chipset:
            variant.chipset = data["chipset"]
            update_fields.append("chipset")
            updated = True

        if data.get("ram_options") and not variant.ram_options:
            variant.ram_options = data["ram_options"]
            update_fields.append("ram_options")
            updated = True

        if data.get("storage_options") and not variant.storage_options:
            variant.storage_options = data["storage_options"]
            update_fields.append("storage_options")
            updated = True

        if updated:
            variant.save(update_fields=update_fields)
            logger.info(
                f"Auto-filled variant: {variant.model.brand.name} {variant.model.name} {variant.name}"
            )

        return Response(
            {
                "success": True,
                "message": "Variant auto-filled successfully",
                "data": data,
                "updated": updated,
            }
        )

    except Exception as e:
        logger.exception(f"Auto-fill variant failed: {e}")
        return Response(
            {"success": False, "error": str(e)}, status=status.HTTP_400_BAD_REQUEST
        )

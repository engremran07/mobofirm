"""
AI-Powered Firmware Analysis Module
Automatically extracts metadata from firmware binaries using AI
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FirmwareAIAnalyzer:
    """
    AI-powered firmware metadata extraction and analysis.

    Capabilities:
    - Chipset detection (MediaTek, Qualcomm, Unisoc, etc.)
    - Android version identification
    - Security patch level extraction
    - Build date detection
    - Firmware type classification
    - Partition structure analysis
    """

    # Known chipset signatures (hex patterns)
    CHIPSET_SIGNATURES = {
        "MediaTek MT6765": b"MT6765",
        "MediaTek MT6768": b"MT6768",
        "MediaTek MT6833": b"MT6833",
        "MediaTek MT6853": b"MT6853",
        "MediaTek MT6877": b"MT6877",
        "MediaTek MT6893": b"MT6893",
        "Qualcomm SM6225": b"SM6225",
        "Qualcomm SM6375": b"SM6375",
        "Qualcomm SM7325": b"SM7325",
        "Qualcomm SM8350": b"SM8350",
        "Unisoc T610": b"T610",
        "Unisoc T618": b"T618",
    }

    # Android version patterns
    ANDROID_VERSION_PATTERNS = [
        (rb"Android\s+(\d+)", "Android {}"),
        (rb"android-(\d+)", "Android {}"),
        (rb"ro\.build\.version\.release=(\d+)", "Android {}"),
    ]

    # Partition names
    COMMON_PARTITIONS = [
        "boot",
        "system",
        "vendor",
        "product",
        "odm",
        "recovery",
        "cache",
        "userdata",
        "metadata",
        "vbmeta",
        "dtbo",
        "super",
        "persist",
    ]

    @classmethod
    def analyze_firmware(cls, firmware_file_path: str) -> dict[str, Any]:
        """
        Analyze firmware file and extract metadata.

        Args:
            firmware_file_path: Path to firmware file

        Returns:
            Dictionary containing extracted metadata:
            {
                'chipset': str,
                'android_version': str,
                'security_patch': str,
                'build_date': str,
                'firmware_type': str,
                'partitions': list,
                'confidence': float,
                'method': str  # 'pattern' or 'ai'
            }
        """
        try:
            # First try pattern-based extraction (faster, more reliable)
            result = cls._pattern_based_analysis(firmware_file_path)

            # If confidence is low, try AI-based analysis
            if result.get("confidence", 0) < 0.7:
                ai_result = cls._ai_based_analysis(firmware_file_path)
                if ai_result.get("confidence", 0) > result.get("confidence", 0):
                    result = ai_result

            return result

        except Exception as e:
            logger.error(f"Firmware analysis failed: {e}", exc_info=True)
            return {"error": str(e), "confidence": 0.0, "method": "error"}

    @classmethod
    def _pattern_based_analysis(cls, firmware_file_path: str) -> dict[str, Any]:
        """
        Fast pattern-based metadata extraction.
        Reads first 10MB of file and searches for known patterns.
        """
        result = {
            "chipset": None,
            "android_version": None,
            "security_patch": None,
            "build_date": None,
            "firmware_type": "unknown",
            "partitions": [],
            "confidence": 0.0,
            "method": "pattern",
        }

        try:
            # Read first 10MB for analysis
            with open(firmware_file_path, "rb") as f:
                header = f.read(10 * 1024 * 1024)

            confidence_score = 0.0

            # Detect chipset
            for chipset_name, signature in cls.CHIPSET_SIGNATURES.items():
                if signature in header:
                    result["chipset"] = chipset_name
                    confidence_score += 0.3
                    break

            # Detect Android version
            for pattern, template in cls.ANDROID_VERSION_PATTERNS:
                match = re.search(pattern, header)
                if match:
                    version = match.group(1).decode("utf-8", errors="ignore")
                    result["android_version"] = template.format(version)
                    confidence_score += 0.2
                    break

            # Detect security patch level
            patch_pattern = rb"ro\.build\.version\.security_patch=(\d{4}-\d{2}-\d{2})"
            match = re.search(patch_pattern, header)
            if match:
                result["security_patch"] = match.group(1).decode("utf-8")
                confidence_score += 0.2

            # Detect build date
            date_pattern = rb"ro\.build\.date=([A-Za-z]{3}\s+\d{1,2}\s+\d{4})"
            match = re.search(date_pattern, header)
            if match:
                result["build_date"] = match.group(1).decode("utf-8")
                confidence_score += 0.1

            # Detect partitions
            for partition in cls.COMMON_PARTITIONS:
                if partition.encode() in header:
                    result["partitions"].append(partition)

            if result["partitions"]:
                confidence_score += 0.2

            result["confidence"] = min(confidence_score, 1.0)

            # Classify firmware type
            if b"engineering" in header.lower() or b"eng" in header.lower():
                result["firmware_type"] = "engineering"
            elif b"official" in header.lower() or b"release" in header.lower():
                result["firmware_type"] = "official"
            elif b"modified" in header.lower() or b"custom" in header.lower():
                result["firmware_type"] = "modified"

            return result

        except Exception as e:
            logger.exception(f"Pattern-based analysis failed: {e}")
            result["error"] = str(e)
            return result

    @classmethod
    def _ai_based_analysis(cls, firmware_file_path: str) -> dict[str, Any]:
        """
        AI-powered metadata extraction using LLM.
        Fallback when pattern-based analysis has low confidence.
        """
        try:
            from apps.ai.services import test_completion

            # Read sample for AI analysis
            with open(firmware_file_path, "rb") as f:
                header = f.read(1024 * 1024)  # 1MB sample

            # Convert to hex (first 1000 bytes)
            hex_sample = header[:1000].hex()

            # Extract readable strings
            strings = re.findall(rb"[\x20-\x7E]{10,}", header[:10000])
            readable_strings = "\n".join(
                [s.decode("utf-8", errors="ignore") for s in strings[:50]]
            )

            prompt = f"""
Analyze this firmware file header and extract metadata.

Hex sample (first 1000 bytes):
{hex_sample[:500]}...

Readable strings found:
{readable_strings}

Extract and return ONLY a JSON object with these fields:
- chipset: Detected chipset (e.g., "MediaTek MT6765", "Qualcomm SM6225", or null)
- android_version: Android version (e.g., "Android 13" or null)
- security_patch: Security patch level in YYYY-MM-DD format (or null)
- build_date: Build date in YYYY-MM-DD format (or null)
- firmware_type: Type - one of: official, engineering, modified, unknown
- partitions: List of detected partition names (e.g., ["boot", "system", "vendor"])
- confidence: Your confidence score from 0.0 to 1.0

Return ONLY valid JSON, no markdown formatting.
"""

            response = test_completion(prompt)
            content = response.get("text", "{}")

            # Clean JSON from markdown
            if content.startswith("```json"):
                content = content.replace("```json", "").replace("```", "")
            elif content.startswith("```"):
                content = content.replace("```", "")

            content = content.strip()

            data = json.loads(content)
            data["method"] = "ai"

            return data

        except Exception as e:
            logger.error(f"AI-based analysis failed: {e}", exc_info=True)
            return {"error": str(e), "confidence": 0.0, "method": "ai_error"}

    @classmethod
    def update_pending_firmware(cls, pending_firmware) -> bool:
        """
        Analyze and update PendingFirmware instance with extracted metadata.

        Args:
            pending_firmware: PendingFirmware model instance

        Returns:
            True if analysis successful, False otherwise
        """
        try:
            file_path = pending_firmware.stored_file_path

            if not Path(file_path).exists():
                logger.warning(f"Firmware file not found: {file_path}")
                return False

            # Run analysis
            result = cls.analyze_firmware(file_path)

            # Update model fields
            if result.get("chipset"):
                pending_firmware.ai_chipset = result["chipset"]

            if result.get("android_version"):
                pending_firmware.ai_android_version = result["android_version"]

            if result.get("partitions"):
                pending_firmware.partitions = result["partitions"]

            if result.get("firmware_type") and result["firmware_type"] != "unknown":
                pending_firmware.ai_firmware_type = result["firmware_type"]

            # Store full analysis in metadata
            pending_firmware.metadata = pending_firmware.metadata or {}
            pending_firmware.metadata["ai_analysis"] = result
            pending_firmware.metadata["analysis_timestamp"] = str(timezone.now())  # noqa: F821  # type: ignore[name-defined]

            # Update AI status based on confidence
            confidence = result.get("confidence", 0.0)
            if confidence >= 0.8:
                pending_firmware.ai_status = "completed"
            elif confidence >= 0.5:
                pending_firmware.ai_status = "partial"
            else:
                pending_firmware.ai_status = "failed"

            pending_firmware.save()

            logger.info(
                f"Firmware analysis completed for {pending_firmware.id}: "
                f"confidence={confidence:.2f}, method={result.get('method')}"
            )

            return True

        except Exception as e:
            logger.error(f"Failed to update pending firmware: {e}", exc_info=True)
            return False


# Convenience function for use in tasks/views
def analyze_and_update_firmware(pending_firmware_id: int) -> dict[str, Any]:
    """
    Analyze firmware and update database record.
    Can be called from Celery task or view.

    Args:
        pending_firmware_id: ID of PendingFirmware instance

    Returns:
        Analysis result dictionary
    """

    from apps.firmwares.models import PendingFirmware

    try:
        pf = PendingFirmware.objects.get(id=pending_firmware_id)
        success = FirmwareAIAnalyzer.update_pending_firmware(pf)

        return {
            "success": success,
            "firmware_id": str(pf.id),
            "metadata": pf.metadata.get("ai_analysis", {}),
        }

    except PendingFirmware.DoesNotExist:
        return {"success": False, "error": "Firmware not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}

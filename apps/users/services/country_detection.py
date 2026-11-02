"""
apps/users/services/country_detection.py

User-related services including:
- IP-based country detection
- Phone number validation
- Profile completion checks
"""

from __future__ import annotations

import logging
import re

from django.utils import timezone

logger = logging.getLogger(__name__)


# Country code to phone prefix mapping
COUNTRY_PHONE_CODES = {
    "AF": "+93",
    "AL": "+355",
    "DZ": "+213",
    "AS": "+1684",
    "AD": "+376",
    "AO": "+244",
    "AI": "+1264",
    "AG": "+1268",
    "AR": "+54",
    "AM": "+374",
    "AW": "+297",
    "AU": "+61",
    "AT": "+43",
    "AZ": "+994",
    "BS": "+1242",
    "BH": "+973",
    "BD": "+880",
    "BB": "+1246",
    "BY": "+375",
    "BE": "+32",
    "BZ": "+501",
    "BJ": "+229",
    "BM": "+1441",
    "BT": "+975",
    "BO": "+591",
    "BA": "+387",
    "BW": "+267",
    "BR": "+55",
    "BN": "+673",
    "BG": "+359",
    "BF": "+226",
    "BI": "+257",
    "KH": "+855",
    "CM": "+237",
    "CA": "+1",
    "CV": "+238",
    "KY": "+1345",
    "CF": "+236",
    "TD": "+235",
    "CL": "+56",
    "CN": "+86",
    "CO": "+57",
    "KM": "+269",
    "CG": "+242",
    "CD": "+243",
    "CR": "+506",
    "CI": "+225",
    "HR": "+385",
    "CU": "+53",
    "CY": "+357",
    "CZ": "+420",
    "DK": "+45",
    "DJ": "+253",
    "DM": "+1767",
    "DO": "+1809",
    "EC": "+593",
    "EG": "+20",
    "SV": "+503",
    "GQ": "+240",
    "ER": "+291",
    "EE": "+372",
    "ET": "+251",
    "FJ": "+679",
    "FI": "+358",
    "FR": "+33",
    "GA": "+241",
    "GM": "+220",
    "GE": "+995",
    "DE": "+49",
    "GH": "+233",
    "GR": "+30",
    "GD": "+1473",
    "GT": "+502",
    "GN": "+224",
    "GW": "+245",
    "GY": "+592",
    "HT": "+509",
    "HN": "+504",
    "HK": "+852",
    "HU": "+36",
    "IS": "+354",
    "IN": "+91",
    "ID": "+62",
    "IR": "+98",
    "IQ": "+964",
    "IE": "+353",
    "IL": "+972",
    "IT": "+39",
    "JM": "+1876",
    "JP": "+81",
    "JO": "+962",
    "KZ": "+7",
    "KE": "+254",
    "KI": "+686",
    "KP": "+850",
    "KR": "+82",
    "KW": "+965",
    "KG": "+996",
    "LA": "+856",
    "LV": "+371",
    "LB": "+961",
    "LS": "+266",
    "LR": "+231",
    "LY": "+218",
    "LI": "+423",
    "LT": "+370",
    "LU": "+352",
    "MO": "+853",
    "MK": "+389",
    "MG": "+261",
    "MW": "+265",
    "MY": "+60",
    "MV": "+960",
    "ML": "+223",
    "MT": "+356",
    "MH": "+692",
    "MR": "+222",
    "MU": "+230",
    "MX": "+52",
    "FM": "+691",
    "MD": "+373",
    "MC": "+377",
    "MN": "+976",
    "ME": "+382",
    "MA": "+212",
    "MZ": "+258",
    "MM": "+95",
    "NA": "+264",
    "NR": "+674",
    "NP": "+977",
    "NL": "+31",
    "NZ": "+64",
    "NI": "+505",
    "NE": "+227",
    "NG": "+234",
    "NO": "+47",
    "OM": "+968",
    "PK": "+92",
    "PW": "+680",
    "PA": "+507",
    "PG": "+675",
    "PY": "+595",
    "PE": "+51",
    "PH": "+63",
    "PL": "+48",
    "PT": "+351",
    "PR": "+1787",
    "QA": "+974",
    "RO": "+40",
    "RU": "+7",
    "RW": "+250",
    "KN": "+1869",
    "LC": "+1758",
    "VC": "+1784",
    "WS": "+685",
    "SM": "+378",
    "ST": "+239",
    "SA": "+966",
    "SN": "+221",
    "RS": "+381",
    "SC": "+248",
    "SL": "+232",
    "SG": "+65",
    "SK": "+421",
    "SI": "+386",
    "SB": "+677",
    "SO": "+252",
    "ZA": "+27",
    "ES": "+34",
    "LK": "+94",
    "SD": "+249",
    "SR": "+597",
    "SZ": "+268",
    "SE": "+46",
    "CH": "+41",
    "SY": "+963",
    "TW": "+886",
    "TJ": "+992",
    "TZ": "+255",
    "TH": "+66",
    "TL": "+670",
    "TG": "+228",
    "TO": "+676",
    "TT": "+1868",
    "TN": "+216",
    "TR": "+90",
    "TM": "+993",
    "TV": "+688",
    "UG": "+256",
    "UA": "+380",
    "AE": "+971",
    "GB": "+44",
    "US": "+1",
    "UY": "+598",
    "UZ": "+998",
    "VU": "+678",
    "VA": "+379",
    "VE": "+58",
    "VN": "+84",
    "YE": "+967",
    "ZM": "+260",
    "ZW": "+263",
    "PS": "+970",
}


def get_phone_code_for_country(country_code: str) -> str:
    """Get phone country code for a given ISO country code."""
    return COUNTRY_PHONE_CODES.get(country_code.upper(), "")


def detect_country_from_ip(ip_address: str) -> str | None:
    """
    Detect country from IP address using free IP geolocation.

    Returns ISO 3166-1 alpha-2 country code or None.
    """
    if not ip_address or ip_address in ("127.0.0.1", "localhost", "::1"):
        return None

    try:
        import requests

        # Use ip-api.com (free, no API key required, 45 requests/minute)
        response = requests.get(
            f"http://ip-api.com/json/{ip_address}",
            timeout=3,
            params={"fields": "countryCode,status"},
        )

        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                return data.get("countryCode")

    except Exception as e:
        logger.warning(f"IP geolocation failed for {ip_address}: {e}")

    return None


def get_client_ip(request) -> str:
    """Extract client IP from request, handling proxies."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        # Take the first IP in the chain
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "")
    return ip


def auto_detect_user_country(user, request) -> bool:
    """
    Auto-detect and set user's country from their IP.

    Returns True if country was detected and set.
    """
    if user.country and user.country_detected_at:
        # Already detected
        return False

    ip = get_client_ip(request)
    country_code = detect_country_from_ip(ip)

    if country_code:
        user.country = country_code
        user.country_detected_at = timezone.now()

        # Also set phone country code if not set
        if not user.phone_country_code:
            user.phone_country_code = get_phone_code_for_country(country_code)

        user.save(
            update_fields=["country", "country_detected_at", "phone_country_code"]
        )
        logger.info(
            f"Auto-detected country {country_code} for user {user.pk} from IP {ip}"
        )
        return True

    return False


def is_email_verified(user) -> bool:
    """Check if user's email is verified."""
    # Social login users are considered verified
    if user.signup_method == "social":
        return True

    # Manual signup users need explicit verification
    return user.email_verified_at is not None


def requires_email_verification(user) -> bool:
    """
    Check if user needs to verify email before sensitive actions.

    Social login users skip this (already verified by provider).
    """
    if user.signup_method == "social":
        return False

    return user.email_verified_at is None


def is_profile_complete(user) -> bool:
    """Check if user has completed their profile setup."""
    required_fields = [
        user.username,
        user.full_name,
    ]
    return all(required_fields) and user.profile_completed


def validate_phone_number(phone: str, country_code: str = "") -> tuple[bool, str]:
    """
    Validate phone number format.

    Returns (is_valid, error_message)
    """
    if not phone:
        return True, ""

    # Remove all non-digit characters except +
    cleaned = re.sub(r"[^\d+]", "", phone)

    # Must start with + or be digits only
    if not re.match(r"^\+?\d{7,15}$", cleaned):
        return False, "Invalid phone number format"

    return True, ""

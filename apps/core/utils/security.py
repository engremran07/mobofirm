"""
Security Utilities
==================

Cryptographic and security-related utilities with no business logic.
"""

import hashlib
import secrets
import string


def generate_token(length: int = 32) -> str:
    """
    Generate a cryptographically secure random token.

    Args:
        length: Token length in bytes (will be longer when base64-encoded)

    Returns:
        URL-safe random token

    Example:
        >>> token = generate_token(32)
        >>> len(token) > 32  # Base64 encoding makes it longer
        True
    """
    return secrets.token_urlsafe(length)


def generate_numeric_code(length: int = 6) -> str:
    """
    Generate a numeric code (for OTP, verification, etc.).

    Args:
        length: Number of digits

    Returns:
        Numeric code as string

    Example:
        >>> code = generate_numeric_code(6)
        >>> len(code)
        6
        >>> code.isdigit()
        True
    """
    return "".join(secrets.choice(string.digits) for _ in range(length))


def generate_alphanumeric_code(length: int = 8) -> str:
    """
    Generate an alphanumeric code (for invite codes, etc.).

    Args:
        length: Code length

    Returns:
        Alphanumeric code

    Example:
        >>> code = generate_alphanumeric_code(8)
        >>> len(code)
        8
    """
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_value(value: str, algorithm: str = "sha256") -> str:
    """
    Hash a value using specified algorithm.

    Args:
        value: Value to hash
        algorithm: Hash algorithm (sha256, sha512, md5, etc.)

    Returns:
        Hex-encoded hash

    Example:
        >>> hash_value("test")
        '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'
    """
    hasher = hashlib.new(algorithm)
    hasher.update(value.encode("utf-8"))
    return hasher.hexdigest()


def hash_password_simple(password: str, salt: str | None = None) -> tuple[str, str]:
    """
    Simple password hashing with salt (use Django's auth for production).

    Args:
        password: Password to hash
        salt: Optional salt (generated if not provided)

    Returns:
        Tuple of (hashed_password, salt)

    Note:
        This is for educational purposes. Use Django's make_password() in production.
    """
    if salt is None:
        salt = generate_token(16)

    combined = salt + password
    hashed = hash_value(combined, "sha256")
    return hashed, salt


def constant_time_compare(val1: str, val2: str) -> bool:
    """
    Compare two strings in constant time (prevent timing attacks).

    Args:
        val1: First string
        val2: Second string

    Returns:
        True if strings are equal

    Example:
        >>> constant_time_compare("secret", "secret")
        True
        >>> constant_time_compare("secret", "public")
        False
    """
    return secrets.compare_digest(val1, val2)


def mask_email(email: str) -> str:
    """
    Mask email address for privacy.

    Args:
        email: Email address

    Returns:
        Masked email

    Example:
        >>> mask_email("john.doe@example.com")
        'j***e@example.com'
    """
    if "@" not in email:
        return email

    username, domain = email.split("@", 1)

    if len(username) <= 2:
        masked_username = username[0] + "*"
    else:
        masked_username = username[0] + "***" + username[-1]

    return f"{masked_username}@{domain}"


def mask_phone(phone: str) -> str:
    """
    Mask phone number for privacy.

    Args:
        phone: Phone number

    Returns:
        Masked phone number

    Example:
        >>> mask_phone("+1234567890")
        '***-***-7890'
    """
    # Remove non-digits
    digits = "".join(c for c in phone if c.isdigit())

    if len(digits) < 4:
        return phone

    return "***-***-" + digits[-4:]


def mask_string(value: str, show_chars: int = 3, mask_char: str = "*") -> str:
    """
    Mask a string, showing only first/last characters.

    Args:
        value: String to mask
        show_chars: Number of characters to show at start/end
        mask_char: Character to use for masking

    Returns:
        Masked string

    Example:
        >>> mask_string("sensitive_data", 3)
        'sen*******ata'
    """
    if len(value) <= show_chars * 2:
        return mask_char * len(value)

    start = value[:show_chars]
    end = value[-show_chars:]
    middle_length = len(value) - (show_chars * 2)

    return start + (mask_char * middle_length) + end


def validate_strength_password(password: str) -> dict:
    """
    Check password strength.

    Args:
        password: Password to check

    Returns:
        Dictionary with strength info

    Example:
        >>> validate_strength_password("Weak1")
        {'is_strong': False, 'score': 2, 'issues': ['Too short']}
    """
    issues = []
    score = 0

    # Length check
    if len(password) < 8:
        issues.append("Too short (minimum 8 characters)")
    elif len(password) >= 12:
        score += 2
    else:
        score += 1

    # Character variety
    has_lower = any(c.islower() for c in password)
    has_upper = any(c.isupper() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in string.punctuation for c in password)

    if not has_lower:
        issues.append("Missing lowercase letter")
    else:
        score += 1

    if not has_upper:
        issues.append("Missing uppercase letter")
    else:
        score += 1

    if not has_digit:
        issues.append("Missing number")
    else:
        score += 1

    if not has_special:
        issues.append("Missing special character")
    else:
        score += 2

    # Common patterns
    common_passwords = ["password", "123456", "qwerty", "abc123"]
    if password.lower() in common_passwords:
        issues.append("Too common")
        score = 0

    return {
        "is_strong": score >= 5 and len(issues) == 0,
        "score": score,
        "max_score": 7,
        "issues": issues,
    }


def generate_uuid() -> str:
    """
    Generate a UUID4 string.

    Returns:
        UUID string

    Example:
        >>> uuid = generate_uuid()
        >>> len(uuid)
        36
    """
    import uuid

    return str(uuid.uuid4())


__all__ = [
    "constant_time_compare",
    "generate_alphanumeric_code",
    "generate_numeric_code",
    "generate_token",
    "generate_uuid",
    "hash_password_simple",
    "hash_value",
    "mask_email",
    "mask_phone",
    "mask_string",
    "validate_strength_password",
]

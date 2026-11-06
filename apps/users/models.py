# apps/users/models.py
"""
apps/users/models.py

Unified foundation layer — absorbs core abstract base models and consent models.

Contains:
- Abstract base models (TimestampedModel, SoftDeleteModel, AuditFieldsModel)
- CustomUser, MFA, SecurityQuestion, NotificationPreferences
- Consent models (ConsentPolicy, ConsentDecision, ConsentEvent)
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import TYPE_CHECKING, Any, ClassVar

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import DatabaseError, IntegrityError, models, transaction
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.text import slugify
from solo.models import SingletonModel

# ===========================================================================
# Abstract Base Models (dissolved from apps.core)
# ===========================================================================


class TimestampedModel(models.Model):
    """Minimal timestamp mixin — created_at + updated_at only."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Opt-in soft-delete. Call obj.soft_delete(user) instead of obj.delete()."""

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_deleted",
    )

    class Meta:
        abstract = True

    def soft_delete(self, user=None, commit: bool = True) -> None:
        from django.utils import timezone as tz

        self.is_deleted = True
        self.deleted_at = tz.now()
        if user and not self.deleted_by:
            self.deleted_by = user
        if commit:
            self.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])


class AuditFieldsModel(models.Model):
    """Adds created_by / updated_by FKs to AUTH_USER_MODEL."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_updated",
    )

    class Meta:
        abstract = True


logger = logging.getLogger(__name__)

_PHONE_NORMALIZE_RE = re.compile(r"[^\d+]")  # keep digits and leading +


# --------------------------------------------------------------------------
# User manager
# --------------------------------------------------------------------------
if TYPE_CHECKING:
    _UserManagerBase = BaseUserManager["CustomUser"]
else:
    _UserManagerBase = BaseUserManager


class CustomUserManager(_UserManagerBase):
    """Custom manager with unified user/superuser creation."""

    use_in_migrations = True

    def _create_user(
        self,
        email: str,
        username: str | None,
        password: str | None,
        **extra_fields: Any,
    ) -> CustomUser:
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).strip().lower()
        username = (username or email.split("@")[0]).strip()[:150]

        # FINDING-0015: Guard against duplicate username — generic message to prevent
        # account enumeration (ACCOUNT_PREVENT_ENUMERATION = True must be consistent).
        existing_username = self.filter(username=username).only("id", "email").first()
        if existing_username and str(existing_username.email).strip().lower() != email:
            raise ValueError(
                "Registration failed. Please check your details and try again."
            )

        # FINDING-0015: Guard against duplicate email — suppress detail to prevent enumeration.
        existing_email = self.filter(email__iexact=email).only("id", "username").first()
        if existing_email and (existing_email.username or "") != username:
            raise ValueError(
                "Registration failed. Please check your details and try again."
            )

        with transaction.atomic():
            try:
                user = self.model(email=email, username=username, **extra_fields)
                if password:
                    user.set_password(password)
                else:
                    user.set_unusable_password()
                user.save(using=self._db)
            except IntegrityError as exc:
                raise ValueError(
                    "User creation failed due to a uniqueness conflict on username or email. "
                    "Check existing accounts and retry with unique credentials."
                ) from exc
        return user

    def create_user(
        self,
        email: str,
        username: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> CustomUser:
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("is_active", True)
        return self._create_user(email, username, password, **extra_fields)

    def create_superuser(
        self,
        email: str,
        username: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> CustomUser:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if not (extra_fields.get("is_staff") and extra_fields.get("is_superuser")):
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(email, username, password, **extra_fields)


# --------------------------------------------------------------------------
# CustomUser
# --------------------------------------------------------------------------
class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Core authentication model with verification & tracking.
    Email is the primary unique identifier.
    """

    # Identity
    email = models.EmailField(unique=True, db_index=True)
    username = models.CharField(
        max_length=150, unique=True, null=True, blank=True, db_index=True
    )
    username_changes_this_year = models.PositiveIntegerField(default=0)
    username_last_changed_at = models.DateTimeField(null=True, blank=True)
    full_name = models.CharField(max_length=150, blank=True, default="")

    # Profile with enhanced location/phone
    bio = models.TextField(
        max_length=500, blank=True, default="", help_text="Short biography"
    )
    country = models.CharField(
        max_length=2,
        blank=True,
        default="",
        help_text="ISO 3166-1 alpha-2 country code (auto-detected from IP)",
    )
    country_detected_at = models.DateTimeField(
        null=True, blank=True, help_text="When the country was auto-detected from IP"
    )
    phone_country_code = models.CharField(
        max_length=5,
        blank=True,
        default="",
        help_text="Phone country code (e.g., +1, +44, +966)",
    )
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    currency = models.CharField(max_length=10, null=True, blank=True)

    class Roles(models.TextChoices):
        ADMIN = "admin", "Admin"
        EDITOR = "editor", "Editor"
        AUTHOR = "author", "Author"
        MODERATOR = "moderator", "Moderator"
        READER = "reader", "Reader"

    role = models.CharField(max_length=50, null=True, blank=True, choices=Roles.choices)

    # Permissions
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    can_create_blog_posts = models.BooleanField(
        default=False,
        help_text="Allow this user to create blog posts (individual permission override)",
    )

    # Credits (deprecated - kept for legacy compatibility)
    credits = models.PositiveIntegerField(default=0)

    # Security & verification
    unlock_count = models.PositiveIntegerField(default=0)
    last_unlock = models.DateTimeField(null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)

    # Signup metadata
    signup_method = models.CharField(
        max_length=20,
        choices=[("manual", "Manual"), ("social", "Social")],
        default="manual",
    )
    manual_signup = models.BooleanField(default=False)
    profile_completed = models.BooleanField(
        default=False,
        help_text="Indicates whether the user has completed their onboarding/profile setup.",
    )
    date_joined = models.DateTimeField(auto_now_add=True)

    # Manager / ID
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[
        list[str]
    ] = []  # keep empty to simplify superuser creation prompts
    objects = CustomUserManager()

    class Meta:
        ordering: ClassVar[list[str]] = ["-date_joined"]
        verbose_name = "User"
        verbose_name_plural = "Users"
        indexes = (
            models.Index(fields=["email"], name="user_email_idx"),
            models.Index(fields=["username"], name="user_username_idx"),
        )

    def __str__(self) -> str:
        return self.email or (self.username or f"user-{self.pk}")

    # ============================================================
    # Minimal model clean / normalization
    # ============================================================
    def clean(self) -> None:
        # Normalize email and phone before validations
        if self.email:
            self.email = str(self.email).strip().lower()
        if self.phone:
            normalized = _PHONE_NORMALIZE_RE.sub("", str(self.phone))
            self.phone = normalized

    def save(self, *args, **kwargs) -> None:
        """Persist user with normalization.

        FINDING-0011 fix: the original implementation performed a double-save
        for new objects (INSERT then unconditional UPDATE), risking duplicate
        post_save signal fires and unnecessary DB writes.  Now uses a single
        super().save() call.
        """
        self.clean()

        if not self.pk and not self.username and self.email:
            base = self.email.split("@")[0][:120]
            slug = slugify(base) or f"user{secrets.token_hex(3)}"
            if CustomUser.objects.filter(username=slug).exists():
                slug = f"{slug[:10]}{secrets.token_hex(2)}"
            self.username = slug

        super().save(*args, **kwargs)

    # ============================================================
    # Utilities
    # ============================================================
    def get_full_name(self) -> str:
        """Return the full name or username as fallback."""
        return self.full_name or self.username or self.email.split("@")[0]

    @property
    def public_display_name(self) -> str:
        """Name for public pages (authors, commenters); never the email address."""
        return (
            self.full_name.strip()
            or (self.username or "").strip()
            or f"Member {self.pk or ''}".strip()
        )

    @property
    def is_verified(self) -> bool:
        return bool(self.email_verified_at)

    def has_role(self, *roles: object) -> bool:
        """Return True if user's role matches any of the provided roles.

        Django's `TextChoices` members are tuples at class definition time
        which static type checkers can't resolve easily; accept object and
        cast to `str` for robust runtime behavior and static compatibility.
        """
        if not self.role:
            return False
        roles_str = [str(r) for r in roles]
        return self.role in roles_str

    def is_admin(self) -> bool:
        return self.is_superuser or self.has_role(self.Roles.ADMIN)

    def is_editor(self) -> bool:
        return self.has_role(self.Roles.EDITOR)

    def is_author(self) -> bool:
        return self.has_role(self.Roles.AUTHOR)

    def is_moderator(self) -> bool:
        return self.has_role(self.Roles.MODERATOR)

    def can_create_posts(self) -> bool:
        """Check if user can create blog posts based on role, staff status, or explicit permission."""
        return (
            self.is_staff
            or self.is_superuser
            or self.can_create_blog_posts
            or self.has_role(self.Roles.AUTHOR, self.Roles.EDITOR, self.Roles.ADMIN)
        )

    def mark_email_verified(self) -> None:
        if not self.email_verified_at:
            self.email_verified_at = timezone.now()
            self.save(update_fields=["email_verified_at"])

    def increment_unlock(self) -> None:
        self.unlock_count = (self.unlock_count or 0) + 1
        self.last_unlock = timezone.now()
        self.save(update_fields=["unlock_count", "last_unlock"])

    def add_credits(self, amount: int) -> None:
        if amount > 0:
            self.credits = (self.credits or 0) + int(amount)
            self.save(update_fields=["credits"])


# --------------------------------------------------------------------------
# Notification
# --------------------------------------------------------------------------
class Notification(models.Model):
    """Multi-channel user notifications with audit timestamps."""

    PRIORITY_CHOICES = (
        ("info", "Info"),
        ("warning", "Warning"),
        ("critical", "Critical"),
    )
    CHANNEL_CHOICES = (
        ("web", "Web"),
        ("email", "Email"),
        ("sms", "SMS"),
        ("push", "Push"),
    )
    ACTION_TYPE_CHOICES = (
        ("comment", "Comment"),
        ("reply", "Reply"),
        ("reaction", "Reaction"),
        ("mention", "Mention"),
        ("vote", "Vote"),
        ("award", "Award"),
        ("post", "Post"),
        ("like", "Like"),
        ("follow", "Follow"),
        ("security", "Security"),
        ("moderation", "Moderation"),
        ("system", "System"),
    )

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    url = models.URLField(max_length=500, blank=True, null=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="info")
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default="web")
    action_type = models.CharField(
        max_length=20,
        choices=ACTION_TYPE_CHOICES,
        default="system",
        help_text="Type of action that triggered notification",
    )
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Icon name for UI display (e.g., comment, heart, bell)",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_notifications",
        help_text="User who triggered this notification",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        indexes = (
            models.Index(
                fields=["recipient", "is_read"], name="notif_recipient_read_idx"
            ),
        )

    def __str__(self) -> str:
        return f"{self.title} → {getattr(self.recipient, 'email', 'unknown')}"

    def mark_as_read(self) -> None:
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            try:
                self.save(update_fields=["is_read", "read_at"])
            except DatabaseError:
                logger.exception("Failed to mark notification read")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.pk,
            "title": self.title,
            "message": self.message,
            "priority": self.priority,
            "channel": self.channel,
            "url": getattr(self, "url", None),
            "is_read": bool(self.is_read),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
        }

    def to_json(self) -> dict[str, Any]:
        return self.to_dict()

    def author_schema(self) -> dict[str, Any]:
        """Basic author schema info for SEO."""
        return {
            "@type": "Person",
            "name": self.full_name or self.username or self.email,  # type: ignore[attr-defined]
            "url": getattr(self, "profile_url", None) or "",
        }


# --------------------------------------------------------------------------
# Announcement
# --------------------------------------------------------------------------
class Announcement(models.Model):
    """Global or segmented announcements for users or staff."""

    AUDIENCE_CHOICES = (
        ("all", "All"),
        ("user", "Users"),
        ("staff", "Staff"),
    )

    title = models.CharField(max_length=255)
    message = models.TextField()
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default="all")
    is_active = models.BooleanField(default=False)
    is_global = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_announcements",
    )
    start_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        verbose_name = "Announcement"
        verbose_name_plural = "Announcements"
        indexes = (
            models.Index(
                fields=["is_global", "start_at"], name="announce_global_start_idx"
            ),
        )

    def __str__(self) -> str:
        return self.title

    def active_now(self) -> bool:
        now = timezone.now()
        if not self.is_active:
            return False
        if self.start_at and self.start_at > now:
            return False
        return not (self.expires_at and self.expires_at <= now)

    def deactivate_if_expired(self) -> None:
        if self.expires_at and self.expires_at <= timezone.now() and self.is_active:
            self.is_active = False
            try:
                self.save(update_fields=["is_active"])
            except DatabaseError:
                logger.exception("Failed to deactivate expired announcement")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.pk,
            "title": self.title,
            "message": self.message,
            "audience": self.audience,
            "is_active": self.is_active,
            "is_global": bool(self.is_global),
            "start_at": self.start_at.isoformat() if self.start_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_by": getattr(self.created_by, "email", None)
            or getattr(self.created_by, "username", None)
            or None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_json(self) -> dict[str, Any]:
        return self.to_dict()


# --------------------------------------------------------------------------
# UsersSettings (app-local, singleton)
# --------------------------------------------------------------------------
class UsersSettings(SingletonModel):
    """
    Per-project settings for the Users app.

    Kept independent so the app can be dropped into other projects
    without relying on a project-wide database settings model.
    """

    enable_signup = models.BooleanField(default=True)
    enable_password_reset = models.BooleanField(default=True)
    enable_notifications = models.BooleanField(default=True)

    require_mfa = models.BooleanField(default=False)
    mfa_totp_issuer = models.CharField(max_length=50, default="Site")

    max_login_attempts = models.PositiveIntegerField(default=5)
    rate_limit_window_seconds = models.PositiveIntegerField(default=300)

    enable_payments = models.BooleanField(default=True)
    required_profile_fields = models.JSONField(
        default=list,
        blank=True,
        help_text="List of user model fields that should trigger a profile-completion prompt.",
    )

    class Meta:
        verbose_name = "Users Settings"

    def __str__(self) -> str:
        return "Users Settings"


# --------------------------------------------------------------------------
# Security Questions (admin-grade recovery)
# --------------------------------------------------------------------------
class SecurityQuestion(models.Model):
    """
    Stores a single security question/answer pair per user for admin-grade recovery.
    Answers are hashed with Django's password hasher.
    """

    QUESTION_CHOICES = (
        ("first_pet", "Name of your first pet?"),
        ("mother_maiden", "What is your mother's maiden name?"),
        ("first_school", "Name of your first school?"),
        ("city_born", "City where you were born?"),
        ("custom", "Custom question"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="security_question",
    )
    question_key = models.CharField(
        max_length=50, choices=QUESTION_CHOICES, default="first_pet"
    )
    custom_question = models.CharField(max_length=255, blank=True, default="")
    answer_hash = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Security Question"
        verbose_name_plural = "Security Questions"

    def __str__(self) -> str:
        return f"Security Question for {getattr(self.user, 'email', self.user_id)}"

    @property
    def question_text(self) -> str:
        if self.question_key == "custom" and self.custom_question:
            return self.custom_question
        return dict(self.QUESTION_CHOICES).get(self.question_key, "Security question")

    def set_answer(self, raw_answer: str) -> None:
        self.answer_hash = make_password((raw_answer or "").strip())

    def check_answer(self, raw_answer: str) -> bool:
        return check_password((raw_answer or "").strip(), self.answer_hash)


# --------------------------------------------------------------------------
# MFA Device (TOTP Authenticator)
# --------------------------------------------------------------------------
class MFADevice(models.Model):
    """
    Stores user MFA device enrollment for TOTP-based 2FA.
    Secret is encrypted at rest using Fernet.
    """

    DEVICE_TYPE_CHOICES = (
        ("totp", "TOTP Authenticator App"),
        ("backup", "Backup Codes"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa_devices"
    )
    device_type = models.CharField(
        max_length=16, choices=DEVICE_TYPE_CHOICES, default="totp"
    )
    name = models.CharField(max_length=64, default="Authenticator App")

    # Encrypted TOTP secret (base32 encoded)
    secret_encrypted = models.CharField(max_length=512, blank=True, default="")

    # Backup codes (JSON list of hashed codes) - only for device_type='backup'
    backup_codes_hash = models.JSONField(default=list, blank=True)

    is_primary = models.BooleanField(default=False, help_text="Primary MFA device")
    is_active = models.BooleanField(default=True)

    # Tracking
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_counter = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "MFA Device"
        verbose_name_plural = "MFA Devices"
        indexes = (
            models.Index(fields=["user", "is_active"], name="mfa_user_active_idx"),
        )

    def __str__(self) -> str:
        return f"MFA {self.get_device_type_display()} for {self.user.email}"

    @classmethod
    def get_fernet(cls) -> Fernet:
        """
        Get Fernet instance for MFA secret encryption/decryption.

        Uses dedicated MFA_ENCRYPTION_KEY if available, falls back to SECRET_KEY.
        Having a separate key allows SECRET_KEY rotation without invalidating MFA.

        FINDING-0022: Key derivation upgraded from raw SHA-256 to HKDF-SHA256
        for proper cryptographic key derivation with domain separation.
        """
        import base64

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured

        # A dedicated key lets SECRET_KEY rotate without invalidating MFA secrets.
        mfa_key = getattr(settings, "MFA_ENCRYPTION_KEY", None)
        if not mfa_key:
            if not settings.DEBUG:
                raise ImproperlyConfigured(
                    "MFA_ENCRYPTION_KEY must be configured outside DEBUG."
                )
            mfa_key = settings.SECRET_KEY

        # FINDING-0022: Use HKDF-SHA256 instead of raw SHA-256 for key derivation.
        # HKDF provides proper domain separation and is safe for use as a KDF.
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"gsmfws-mfa-secret-v1",
        )
        key = hkdf.derive(force_bytes(mfa_key))
        fernet_key = base64.urlsafe_b64encode(key)
        return Fernet(fernet_key)

    def set_secret(self, raw_secret: str) -> None:
        """Encrypt and store the TOTP secret."""
        if not raw_secret:
            self.secret_encrypted = ""
            return
        self.secret_encrypted = self.get_fernet().encrypt(raw_secret.encode()).decode()

    def get_secret(self) -> str | None:
        """Decrypt and return the TOTP secret; None if the key no longer matches."""
        if not self.secret_encrypted:
            return None
        try:
            return self.get_fernet().decrypt(self.secret_encrypted.encode()).decode()
        except InvalidToken:
            logger.error(
                "MFA secret for device %s cannot be decrypted with the configured "
                "MFA_ENCRYPTION_KEY",
                self.pk,
            )
            return None

    def verify_code(self, code: str) -> bool:
        """Verify a TOTP code against this device."""
        from apps.users.mfa import TOTPService

        secret = self.get_secret()
        if not secret:
            return False

        matched_counter = TOTPService.matching_counter(secret, code)
        if matched_counter is None or (
            self.last_used_counter is not None
            and matched_counter <= self.last_used_counter
        ):
            return False

        self.last_used_at = timezone.now()
        self.last_used_counter = matched_counter
        self.save(update_fields=["last_used_at", "last_used_counter"])
        return True

    def generate_backup_codes(self, count: int = 10) -> list:
        """Generate and store backup codes. Returns unhashed codes for user."""
        codes = []
        hashed_codes = []

        for _ in range(count):
            code = secrets.token_hex(4).upper()  # 8-char hex code
            codes.append(code)
            hashed_codes.append(make_password(code))

        self.backup_codes_hash = hashed_codes
        self.device_type = "backup"
        self.save(update_fields=["backup_codes_hash", "device_type"])
        return codes

    def verify_backup_code(self, code: str) -> bool:
        """Verify and consume a backup code."""
        code = (code or "").strip().upper()

        for i, hashed in enumerate(self.backup_codes_hash):
            if check_password(code, hashed):
                # Remove used code
                self.backup_codes_hash.pop(i)
                self.last_used_at = timezone.now()
                self.save(update_fields=["backup_codes_hash", "last_used_at"])
                return True
        return False


# --------------------------------------------------------------------------
# NotificationPreferences
# --------------------------------------------------------------------------
class NotificationPreferences(models.Model):
    """User notification preferences for granular control over notification channels and types."""

    FREQUENCY_CHOICES = (
        ("instant", "Instant"),
        ("hourly", "Hourly Digest"),
        ("daily", "Daily Digest"),
        ("weekly", "Weekly Digest"),
        ("never", "Never"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )

    # Email notification toggles
    email_comments = models.BooleanField(
        default=True, help_text="Email when someone comments on your posts"
    )
    email_replies = models.BooleanField(
        default=True, help_text="Email when someone replies to your comments"
    )
    email_mentions = models.BooleanField(
        default=True, help_text="Email when someone mentions you"
    )
    email_new_posts = models.BooleanField(
        default=False, help_text="Email about new blog posts"
    )
    email_security = models.BooleanField(
        default=True, help_text="Email for security alerts (always enabled)"
    )
    email_frequency = models.CharField(
        max_length=10, choices=FREQUENCY_CHOICES, default="instant"
    )

    # Web notification toggles
    web_comments = models.BooleanField(
        default=True, help_text="Web notifications for comment interactions"
    )
    web_awards = models.BooleanField(
        default=True, help_text="Web notifications for awards and achievements"
    )
    web_moderation = models.BooleanField(
        default=True, help_text="Web notifications for moderation updates"
    )
    web_system = models.BooleanField(
        default=True, help_text="Web notifications for system announcements"
    )

    # Push notification toggle
    push_enabled = models.BooleanField(
        default=False, help_text="Enable browser push notifications"
    )

    # Quiet hours
    quiet_hours_enabled = models.BooleanField(
        default=False, help_text="Mute notifications during quiet hours"
    )
    quiet_hours_start = models.TimeField(
        default="22:00", help_text="Start of quiet hours"
    )
    quiet_hours_end = models.TimeField(default="08:00", help_text="End of quiet hours")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Notification Preferences"
        verbose_name_plural = "Notification Preferences"

    def __str__(self) -> str:
        return f"Preferences for {self.user.email}"

    def is_quiet_hours_now(self) -> bool:
        """Check if current time is within quiet hours."""
        if not self.quiet_hours_enabled:
            return False

        current_time = timezone.now().time()

        if self.quiet_hours_start < self.quiet_hours_end:
            # Normal range (e.g., 22:00 - 23:59)
            return self.quiet_hours_start <= current_time <= self.quiet_hours_end
        else:
            # Overnight range (e.g., 22:00 - 08:00)
            return (
                current_time >= self.quiet_hours_start
                or current_time <= self.quiet_hours_end
            )

    def should_send_email(self, notification_type: str) -> bool:
        """Check if email should be sent for this notification type."""
        if notification_type == "security":
            return True

        if self.email_frequency == "never":
            return False

        # Check specific type preferences
        type_map = {
            "comment": self.email_comments,
            "reply": self.email_replies,
            "mention": self.email_mentions,
            "post": self.email_new_posts,
        }

        return type_map.get(notification_type, False)

    def should_send_web(self, notification_type: str) -> bool:
        """Check if web notification should be sent for this notification type."""
        type_map = {
            "comment": self.web_comments,
            "reply": self.web_comments,
            "reaction": self.web_comments,
            "vote": self.web_comments,
            "award": self.web_awards,
            "moderation": self.web_moderation,
            "system": self.web_system,
        }

        return type_map.get(notification_type, True)


# --------------------------------------------------------------------------
# PushSubscription
# --------------------------------------------------------------------------
class PushSubscription(models.Model):
    """Store push notification subscriptions for web push."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )

    # Web Push subscription details
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=255, help_text="Encryption key")
    auth = models.CharField(max_length=255, help_text="Authentication secret")

    # Metadata
    user_agent = models.CharField(max_length=500, blank=True)
    device_name = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Push Subscription"
        verbose_name_plural = "Push Subscriptions"
        indexes = (
            models.Index(fields=["user", "is_active"], name="push_user_active_idx"),
        )

    def __str__(self) -> str:
        return f"Push subscription for {self.user.email} ({self.device_name or 'Unknown device'})"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for pywebpush library."""
        return {
            "endpoint": self.endpoint,
            "keys": {
                "p256dh": self.p256dh,
                "auth": self.auth,
            },
        }


# ===========================================================================
# Consent Models
# ===========================================================================


class ConsentPolicy(models.Model):
    """GDPR/CCPA consent policy version with banner text and categories snapshot."""

    version = models.CharField(max_length=50, unique=True)
    site_domain = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=False)
    banner_text = models.TextField(blank=True, default="")
    manage_text = models.TextField(blank=True, default="")
    cache_ttl_seconds = models.IntegerField(default=86400)
    text = models.TextField(blank=True, default="")
    categories_snapshot = models.JSONField(default=dict, blank=True)
    public_slug = models.SlugField(max_length=100, blank=True, default="")
    public_url = models.URLField(blank=True, default="")
    effective_from = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        verbose_name = "Consent Policy"
        verbose_name_plural = "Consent Policies"

    def __str__(self) -> str:
        return f"Policy {self.version} ({'active' if self.is_active else 'inactive'})"

    @classmethod
    def get_active(cls, domain: str = "") -> ConsentPolicy | None:
        qs = cls.objects.filter(is_active=True)
        if domain:
            qs = qs.filter(site_domain__iexact=domain)
        return qs.order_by("-effective_from").first()


class ConsentDecision(models.Model):
    """Records a user or session consent decision against a policy version."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    session_id = models.CharField(max_length=64, blank=True, default="")
    policy = models.ForeignKey(
        ConsentPolicy, null=True, blank=True, on_delete=models.SET_NULL
    )
    categories = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    ip_hash = models.CharField(max_length=64, blank=True, default="")
    user_agent_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        verbose_name = "Consent Decision"

    def set_hashes(self, ip: str = "", ua: str = "") -> None:
        from apps.users.services.consent import hash_ip, hash_ua

        self.ip_hash = hash_ip(ip)
        self.user_agent_hash = hash_ua(ua)


class ConsentEvent(models.Model):
    """Audit event tied to a ConsentDecision."""

    decision = models.ForeignKey(
        ConsentDecision,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    policy = models.ForeignKey(
        ConsentPolicy, null=True, blank=True, on_delete=models.SET_NULL
    )
    categories = models.JSONField(default=dict, blank=True)
    event_type = models.CharField(max_length=32, default="accepted")
    created_at = models.DateTimeField(auto_now_add=True)
    ip_hash = models.CharField(max_length=64, blank=True, default="")
    user_agent_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        verbose_name = "Consent Event"

    def set_hashes(self, ip: str = "", ua: str = "") -> None:
        from apps.users.services.consent import hash_ip, hash_ua

        self.ip_hash = hash_ip(ip)
        self.user_agent_hash = hash_ua(ua)


class ConsentRecord(ConsentDecision):
    """Proxy for admin/import backwards compatibility."""

    class Meta:
        proxy = True
        verbose_name = "Consent Record"
        verbose_name_plural = "Consent Records"


class ConsentLog(ConsentEvent):
    """Proxy for admin/import backwards compatibility."""

    class Meta:
        proxy = True
        verbose_name = "Consent Log"
        verbose_name_plural = "Consent Logs"


from apps.users.models_social import SocialPostingAccount, SocialProviderConfig  # noqa: F401
from apps.users.profile_models import ActivityFeed, Following, Profile, Reputation  # noqa: F401

from __future__ import annotations

from typing import Any, ClassVar

from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.consent.constants import (
    ACTION_ACCEPT_ALL,
    ACTION_CUSTOM,
    ACTION_REJECT_ALL,
    BUILTIN_CATEGORIES,
    NECESSARY,
    SOURCE_ADMIN,
    SOURCE_BANNER,
    SOURCE_PRIVACY_CENTRE,
)

slug_validator = RegexValidator(
    r"^[a-z]{3,32}$", _("Use 3-32 lowercase letters, no digits or separators.")
)
privacy_url_validator = RegexValidator(
    r"^(/[^\s]*|https://[^\s]+)$",
    _("Use a site path such as /pages/privacy/ or an https:// address."),
)


class ConsentCategory(models.Model):
    """A group of optional trackers the visitor can allow or refuse together."""

    slug = models.SlugField(
        max_length=32,
        unique=True,
        validators=[slug_validator],
        help_text=_("Stable identifier used by code. The built-in slugs are fixed."),
    )
    name = models.CharField(max_length=80)
    description = models.TextField(
        help_text=_("Plain-language explanation shown to the visitor.")
    )
    is_required = models.BooleanField(
        default=False,
        help_text=_("Strictly necessary: always on and not offered as a choice."),
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering: ClassVar[list[str]] = ["sort_order", "slug"]
        verbose_name = _("consent category")
        verbose_name_plural = _("consent categories")

    def __str__(self) -> str:
        return self.name

    @property
    def is_builtin(self) -> bool:
        return self.slug in BUILTIN_CATEGORIES

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.slug == NECESSARY:
            self.is_required = True
            self.is_active = True
        super().save(*args, **kwargs)


class ConsentPolicy(models.Model):
    """A versioned consent text. Publishing a version asks every visitor again."""

    version = models.PositiveIntegerField(unique=True)
    title = models.CharField(max_length=120, default="Cookie and tracking consent")
    banner_text = models.TextField(
        help_text=_("Short explanation shown in the consent banner.")
    )
    details_text = models.TextField(
        blank=True, help_text=_("Longer introduction shown in the privacy centre.")
    )
    privacy_url = models.CharField(
        max_length=200,
        blank=True,
        default="/pages/privacy/",
        validators=[privacy_url_validator],
        help_text=_("Link to the full privacy policy."),
    )
    categories_snapshot = models.JSONField(
        default=list,
        editable=False,
        help_text=_("Categories as offered when this version was published."),
    )
    is_active = models.BooleanField(default=False, editable=False)
    published_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-version"]
        verbose_name = _("consent policy version")
        verbose_name_plural = _("consent policy versions")
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="consent_single_active_policy",
            )
        ]

    def __str__(self) -> str:
        return f"v{self.version} — {self.title}"

    @property
    def is_published(self) -> bool:
        return self.published_at is not None


class ConsentDecision(models.Model):
    """Append-only audit record of one choice, kept anonymised.

    The subject is a random identifier stored in the visitor's own cookie. IP
    address and user agent are stored only as keyed hashes. Withdrawing consent
    adds a newer decision; nothing here is ever updated.
    """

    ACTIONS: ClassVar[list[tuple[str, str]]] = [
        (ACTION_ACCEPT_ALL, "Accepted all"),
        (ACTION_REJECT_ALL, "Rejected optional"),
        (ACTION_CUSTOM, "Custom choice"),
    ]
    SOURCES: ClassVar[list[tuple[str, str]]] = [
        (SOURCE_BANNER, "Banner"),
        (SOURCE_PRIVACY_CENTRE, "Privacy centre"),
        (SOURCE_ADMIN, "Admin"),
    ]

    subject = models.UUIDField(db_index=True)
    policy = models.ForeignKey(
        ConsentPolicy, on_delete=models.PROTECT, related_name="decisions"
    )
    granted = models.JSONField(
        default=list, help_text=_("Optional category slugs the visitor allowed.")
    )
    action = models.CharField(max_length=16, choices=ACTIONS)
    source = models.CharField(max_length=16, choices=SOURCES)
    ip_hash = models.CharField(max_length=64, blank=True)
    user_agent_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        verbose_name = _("consent record")
        verbose_name_plural = _("consent records")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["subject", "-created_at"], name="consent_subject_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_action_display()} (v{self.policy_id})"

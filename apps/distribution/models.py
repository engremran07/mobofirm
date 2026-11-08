from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from solo.models import SingletonModel

from apps.distribution.constants import (
    APPROVAL_AUTO,
    APPROVAL_REQUIRED,
    APPROVAL_SCHEDULE_ONLY,
    DEFAULT_BLOG_TEMPLATE,
    DEFAULT_CLICK_RETENTION_DAYS,
    DEFAULT_FIRMWARE_TEMPLATE,
    DEFAULT_HOOKS,
    DEFAULT_LOG_RETENTION_DAYS,
    DEFAULT_MEDIA_RETENTION_DAYS,
    EMOJI_EXPRESSIVE,
    EMOJI_MINIMAL,
    EMOJI_NONE,
    HOOK_BENEFIT,
    HOOK_HOWTO,
    HOOK_QUESTION,
    HOOK_URGENCY,
    KIND_BLUESKY,
    KIND_DISCORD,
    KIND_FACEBOOK,
    KIND_INSTAGRAM,
    KIND_LINKEDIN,
    KIND_MASTODON,
    KIND_PINTEREST,
    KIND_REDDIT,
    KIND_SLACK,
    KIND_TELEGRAM,
    KIND_THREADS,
    KIND_TIKTOK,
    KIND_WEBHOOK,
    KIND_WHATSAPP,
    KIND_X,
    KIND_YOUTUBE,
    TEMPLATE_FIELDS,
)
from apps.distribution.scheduling import parse_windows, valid_timezone

if TYPE_CHECKING:
    from apps.distribution.channels import Adapter

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
HOOK_CHOICES = (
    (HOOK_QUESTION, "Question"),
    (HOOK_BENEFIT, "Benefit"),
    (HOOK_HOWTO, "How to flash"),
    (HOOK_URGENCY, "New / just added"),
)


def validate_template(value: str) -> None:
    """Only known ``{placeholders}``, and the link must be included."""
    unknown = sorted(set(_PLACEHOLDER.findall(value)) - set(TEMPLATE_FIELDS))
    if unknown:
        raise ValidationError(
            _("Unknown placeholder(s): %(names)s. Use: %(allowed)s."),
            params={
                "names": ", ".join(f"{{{name}}}" for name in unknown),
                "allowed": ", ".join(f"{{{name}}}" for name in TEMPLATE_FIELDS),
            },
        )
    if "{url}" not in value:
        raise ValidationError(_("The template must contain {url}."))


def validate_windows(value: str) -> None:
    try:
        parse_windows(value)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def validate_timezone(value: str) -> None:
    if not valid_timezone(value):
        raise ValidationError(_("Unknown time zone. Use a name like Europe/Istanbul."))


def validate_hooks(value: str) -> None:
    allowed = {key for key, _label in HOOK_CHOICES}
    chosen = [part.strip() for part in value.split(",") if part.strip()]
    if not chosen or set(chosen) - allowed:
        raise ValidationError(
            _("Choose from: %(allowed)s (comma separated).")
            % {"allowed": ", ".join(sorted(allowed))}
        )


class DistributionSettings(SingletonModel):
    """Retention and housekeeping policy (one row, staff-editable)."""

    click_retention_days = models.PositiveSmallIntegerField(
        default=DEFAULT_CLICK_RETENTION_DAYS,
        help_text=_(
            "Individual click records older than this are deleted (the per-link "
            "totals stay). 0 keeps them forever."
        ),
    )
    log_retention_days = models.PositiveSmallIntegerField(
        default=DEFAULT_LOG_RETENTION_DAYS,
        help_text=_(
            "Audit log lines older than this are deleted. 0 keeps them forever."
        ),
    )
    media_retention_days = models.PositiveSmallIntegerField(
        default=DEFAULT_MEDIA_RETENTION_DAYS,
        help_text=_(
            "Generated share cards and videos are deleted after this many days. "
            "0 keeps them forever."
        ),
    )
    last_housekeeping_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = _("distribution settings")
        verbose_name_plural = _("distribution settings")

    def __str__(self) -> str:
        return "Distribution settings"


class Campaign(models.Model):
    """A reusable copy/schedule preset a channel can follow."""

    class Emoji(models.TextChoices):
        NONE = EMOJI_NONE, "None"
        MINIMAL = EMOJI_MINIMAL, "Minimal (one per post)"
        EXPRESSIVE = EMOJI_EXPRESSIVE, "Expressive"

    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=300, blank=True)
    hook_styles = models.CharField(
        max_length=120,
        default=",".join(DEFAULT_HOOKS),
        validators=[validate_hooks],
        help_text=_(
            "Opening styles to rotate, comma separated: question, benefit, howto, urgency."
        ),
    )
    emoji_policy = models.CharField(
        max_length=12, choices=Emoji.choices, default=EMOJI_MINIMAL
    )
    hashtag_cap = models.PositiveSmallIntegerField(
        default=0, help_text=_("Most hashtags per post; 0 = the platform default.")
    )
    use_short_links = models.BooleanField(
        default=True,
        help_text=_("Post a first-party /go/ link so clicks per channel are counted."),
    )
    utm_campaign = models.SlugField(
        max_length=60,
        blank=True,
        help_text=_("utm_campaign value; empty = the content type."),
    )
    use_referral = models.BooleanField(
        default=False,
        help_text=_(
            "Add the channel's referral code so sign-ups from the post credit that account."
        ),
    )
    ab_test = models.BooleanField(
        default=True,
        help_text=_(
            "Rotate the opening styles and keep the winner after enough clicks."
        ),
    )
    ab_min_clicks = models.PositiveIntegerField(
        default=50, help_text=_("Clicks (all styles) before a winner is chosen.")
    )
    evergreen = models.BooleanField(
        default=False,
        help_text=_("Re-share the best-performing firmware and guides, spaced out."),
    )
    evergreen_gap_days = models.PositiveSmallIntegerField(
        default=45,
        help_text=_("Never repeat an item on a channel within this many days."),
    )
    evergreen_per_week = models.PositiveSmallIntegerField(
        default=2, help_text=_("Most re-shares per channel per week.")
    )
    trending_boost = models.BooleanField(
        default=True,
        help_text=_(
            "New models and chipsets get extra hashtags and the next free slot."
        ),
    )
    generate_media = models.BooleanField(
        default=True, help_text=_("Create a share card (and a video when possible).")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]
        verbose_name = _("campaign preset")

    def __str__(self) -> str:
        return self.name

    @property
    def hooks(self) -> tuple[str, ...]:
        chosen = tuple(p.strip() for p in self.hook_styles.split(",") if p.strip())
        return chosen or DEFAULT_HOOKS

    @classmethod
    def default(cls) -> Campaign:
        """The in-memory preset used by channels that have none selected."""
        return cls(name="Default")


class Channel(models.Model):
    """One outbound destination. Off until an operator enables it."""

    class Kind(models.TextChoices):
        WEBHOOK = KIND_WEBHOOK, "Generic webhook (signed JSON)"
        TELEGRAM = KIND_TELEGRAM, "Telegram"
        DISCORD = KIND_DISCORD, "Discord webhook"
        X = KIND_X, "X (Twitter)"
        YOUTUBE = KIND_YOUTUBE, "YouTube Shorts"
        FACEBOOK = KIND_FACEBOOK, "Facebook Page"
        INSTAGRAM = KIND_INSTAGRAM, "Instagram"
        THREADS = KIND_THREADS, "Threads"
        LINKEDIN = KIND_LINKEDIN, "LinkedIn page"
        REDDIT = KIND_REDDIT, "Reddit"
        PINTEREST = KIND_PINTEREST, "Pinterest"
        TIKTOK = KIND_TIKTOK, "TikTok"
        MASTODON = KIND_MASTODON, "Mastodon"
        BLUESKY = KIND_BLUESKY, "Bluesky"
        WHATSAPP = KIND_WHATSAPP, "WhatsApp (Cloud API)"
        SLACK = KIND_SLACK, "Slack webhook"

    class Approval(models.TextChoices):
        AUTO = APPROVAL_AUTO, "Automatic"
        REQUIRED = APPROVAL_REQUIRED, "Staff approval required"
        SCHEDULE_ONLY = APPROVAL_SCHEDULE_ONLY, "Schedule only (best-time windows)"

    name = models.CharField(max_length=80, unique=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    enabled = models.BooleanField(default=False)
    endpoint_url = models.URLField(
        blank=True, help_text=_("Webhook only: the public https address to POST to.")
    )
    target = models.CharField(
        max_length=120,
        blank=True,
        help_text=_("Telegram only: chat id or @channelname."),
    )
    options = models.JSONField(
        default=dict,
        blank=True,
        editable=False,
        help_text=_("Non-secret platform settings (account ids, server URL...)."),
    )
    secret_encrypted = models.TextField(blank=True, editable=False)
    share_firmware = models.BooleanField(default=True)
    share_blog_posts = models.BooleanField(default=True)
    use_composer = models.BooleanField(
        default=True,
        help_text=_(
            "Write platform-native posts (hook, hashtags, tracked link). Switch off "
            "to use the two templates below instead."
        ),
    )
    firmware_template = models.TextField(
        default=DEFAULT_FIRMWARE_TEMPLATE, validators=[validate_template]
    )
    blog_template = models.TextField(
        default=DEFAULT_BLOG_TEMPLATE, validators=[validate_template]
    )
    campaign = models.ForeignKey(
        Campaign,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="channels",
        help_text=_("Copy and schedule preset; empty = the default preset."),
    )
    referral_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=_(
            "Account whose referral code is added to links when the preset allows it."
        ),
    )
    approval_mode = models.CharField(
        max_length=16, choices=Approval.choices, default=APPROVAL_AUTO
    )
    timezone = models.CharField(
        max_length=64,
        default="UTC",
        validators=[validate_timezone],
        help_text=_("Time zone for windows and quiet hours, e.g. Europe/Istanbul."),
    )
    send_windows = models.CharField(
        max_length=200,
        blank=True,
        validators=[validate_windows],
        help_text=_(
            "Best times to post, local time, e.g. 09:00-11:00,18:00-21:00. "
            "Empty = any time (schedule-only channels use the platform default)."
        ),
    )
    quiet_start = models.TimeField(null=True, blank=True)
    quiet_end = models.TimeField(
        null=True,
        blank=True,
        help_text=_(
            "Quiet hours: nothing is posted between start and end (local time)."
        ),
    )
    delay_minutes = models.PositiveSmallIntegerField(
        default=0, help_text=_("Wait this long before posting (0 = right away).")
    )
    daily_limit = models.PositiveSmallIntegerField(
        default=20, help_text=_("Most posts per 24 hours; extra items are skipped.")
    )
    kind_daily_cap = models.PositiveSmallIntegerField(
        default=0,
        help_text=_(
            "Most posts of one content type (firmware / blog) per local day; the rest "
            "move to the next day. 0 = no cap."
        ),
    )
    max_attempts = models.PositiveSmallIntegerField(default=5)
    consecutive_failures = models.PositiveIntegerField(default=0, editable=False)
    last_success_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_error = models.CharField(max_length=300, blank=True, editable=False)
    disabled_reason = models.CharField(max_length=300, blank=True, editable=False)
    connection_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    connection_ok = models.BooleanField(null=True, blank=True, editable=False)
    connection_message = models.CharField(max_length=300, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]
        verbose_name = _("channel")

    def __str__(self) -> str:
        return self.name

    @property
    def adapter(self) -> Adapter | None:
        from apps.distribution import channels

        return channels.get_adapter(self.kind)

    @property
    def has_secret(self) -> bool:
        return bool(self.secret_encrypted)

    @property
    def configuration_problem(self) -> str:
        """Why this channel cannot send yet (empty when it is complete)."""
        from apps.distribution import channels
        from apps.site_settings.crypto import SecretUnavailableError, decrypt_secret

        try:
            plain = decrypt_secret(self.secret_encrypted)
        except SecretUnavailableError:
            return "The stored credential cannot be decrypted; enter it again."
        return channels.configuration_problem(self, plain)

    @property
    def state(self) -> str:
        if not self.enabled:
            return "off"
        if self.configuration_problem:
            return "misconfigured"
        if self.consecutive_failures >= 3:
            return "failing"
        return "ready"

    def clean(self) -> None:
        if (
            self.kind == KIND_WEBHOOK
            and self.endpoint_url
            and not self.endpoint_url.startswith("https://")
        ):
            raise ValidationError({"endpoint_url": _("Use an https:// address.")})
        if bool(self.quiet_start) != bool(self.quiet_end):
            raise ValidationError(_("Set both quiet hours times, or neither."))
        if self.enabled and self.configuration_problem:
            raise ValidationError(self.configuration_problem)


class ShareJob(models.Model):
    """One item to post to one channel. ``idempotency_key`` is unique per
    (channel, object, round), so a replayed event never posts twice."""

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting approval"
        QUEUED = "queued", "Queued"
        NEEDS_MEDIA = "needs_media", "Needs media"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed (gave up)"
        SKIPPED = "skipped", "Skipped"
        CANCELLED = "cancelled", "Cancelled"
        REJECTED = "rejected", "Rejected"

    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="jobs")
    content_kind = models.CharField(max_length=12, db_index=True)
    object_ref = models.CharField(max_length=120)
    idempotency_key = models.CharField(max_length=200, unique=True)
    title = models.CharField(max_length=300)
    url = models.URLField(max_length=500, blank=True)
    target_url = models.URLField(
        max_length=900,
        blank=True,
        help_text=_("Where the tracked link lands (page + UTM + optional referral)."),
    )
    text = models.TextField()
    hook = models.CharField(max_length=12, blank=True)
    reshare_round = models.PositiveSmallIntegerField(default=0)
    trending = models.BooleanField(default=False)
    remote_state = models.JSONField(default=dict, blank=True, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error = models.CharField(max_length=300, blank=True)
    response_code = models.CharField(max_length=20, blank=True)
    external_id = models.CharField(max_length=200, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["status", "next_attempt_at"], name="distribution_due_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel}: {self.title}"


class ShareLog(models.Model):
    """Append-only audit trail: every attempt and decision, with the HTTP result."""

    job = models.ForeignKey(
        ShareJob, null=True, blank=True, on_delete=models.CASCADE, related_name="log"
    )
    channel = models.ForeignKey(
        Channel, null=True, blank=True, on_delete=models.CASCADE, related_name="log"
    )
    level = models.CharField(max_length=8, default="info")
    message = models.CharField(max_length=400)
    response_code = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.level}: {self.message}"


class ShortLink(models.Model):
    """First-party redirect ``/go/<code>/`` created for one share."""

    code = models.CharField(max_length=12, unique=True)
    job = models.OneToOneField(
        ShareJob, on_delete=models.CASCADE, related_name="short_link"
    )
    channel = models.ForeignKey(
        Channel, on_delete=models.CASCADE, related_name="short_links"
    )
    content_kind = models.CharField(max_length=12, db_index=True)
    object_ref = models.CharField(max_length=120, db_index=True)
    hook = models.CharField(max_length=12, blank=True)
    target_url = models.URLField(max_length=900)
    clicks = models.PositiveIntegerField(default=0)
    bot_clicks = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_click_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"/go/{self.code}/"


class ClickEvent(models.Model):
    """One counted human click: only the hour it happened (no IP, agent or cookie)."""

    link = models.ForeignKey(ShortLink, on_delete=models.CASCADE, related_name="events")
    bucket = models.DateTimeField(db_index=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-bucket"]


class MediaAsset(models.Model):
    """A generated share card or video, shared by every channel posting the content."""

    class AssetKind(models.TextChoices):
        CARD = "card", "Share card 1200×630"
        VERTICAL = "vertical", "Vertical card 1080×1920"
        VIDEO = "video", "Vertical video"

    content_kind = models.CharField(max_length=12)
    object_ref = models.CharField(max_length=120)
    asset_kind = models.CharField(max_length=10, choices=AssetKind.choices)
    storage_name = models.CharField(max_length=300, blank=True)
    reason = models.CharField(
        max_length=300,
        blank=True,
        help_text=_("Why no file exists (tooling missing...)."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["content_kind", "object_ref", "asset_kind"],
                name="distribution_asset_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.asset_kind}: {self.object_ref}"

    @property
    def ready(self) -> bool:
        return bool(self.storage_name)

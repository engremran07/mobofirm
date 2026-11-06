from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .rendering import RenderedPage, plain_text_excerpt, render_page


class PageQuerySet(models.QuerySet["Page"]):
    def published(self) -> PageQuerySet:
        """Pages whose status and publication window make them live right now."""
        now = timezone.now()
        return self.filter(status=Page.Status.PUBLISHED).filter(
            Q(publish_at__isnull=True) | Q(publish_at__lte=now),
            Q(unpublish_at__isnull=True) | Q(unpublish_at__gt=now),
        )

    def visible_to(self, user) -> PageQuerySet:
        """Published pages the given user is allowed to read."""
        levels = [Page.Access.PUBLIC]
        if getattr(user, "is_authenticated", False):
            levels.append(Page.Access.AUTHENTICATED)
        if getattr(user, "is_staff", False):
            levels.append(Page.Access.STAFF)
        return self.published().filter(access_level__in=levels)


class Page(models.Model):
    """CMS page with scheduling, access control and sitemap controls."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    class Access(models.TextChoices):
        PUBLIC = "public", "Public"
        AUTHENTICATED = "auth", "Authenticated"
        STAFF = "staff", "Staff"

    class ContentFormat(models.TextChoices):
        MARKDOWN = "md", "Markdown"
        HTML = "html", "HTML"

    class ChangeFreq(models.TextChoices):
        ALWAYS = "always", "always"
        HOURLY = "hourly", "hourly"
        DAILY = "daily", "daily"
        WEEKLY = "weekly", "weekly"
        MONTHLY = "monthly", "monthly"
        YEARLY = "yearly", "yearly"
        NEVER = "never", "never"

    slug = models.SlugField(unique=True, max_length=200)
    title = models.CharField(max_length=200)
    content = models.TextField()
    content_format = models.CharField(
        max_length=8, choices=ContentFormat.choices, default=ContentFormat.MARKDOWN
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    publish_at = models.DateTimeField(null=True, blank=True)
    unpublish_at = models.DateTimeField(null=True, blank=True)

    seo_title = models.CharField(max_length=200, blank=True)
    seo_description = models.CharField(max_length=300, blank=True)
    og_image = models.ImageField(upload_to="pages/og/", null=True, blank=True)
    canonical_url = models.URLField(blank=True, default="")
    access_level = models.CharField(
        max_length=12, choices=Access.choices, default=Access.PUBLIC
    )

    include_in_sitemap = models.BooleanField(default=True)
    changefreq = models.CharField(
        max_length=12, choices=ChangeFreq.choices, default=ChangeFreq.WEEKLY
    )
    priority = models.DecimalField(
        max_digits=2,
        decimal_places=1,
        default=0.5,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pages_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pages_updated",
    )

    objects = PageQuerySet.as_manager()

    class Meta:
        ordering: ClassVar[list[str]] = ["slug"]
        verbose_name = "Page"
        verbose_name_plural = "Pages"
        constraints = (
            models.CheckConstraint(
                condition=Q(unpublish_at__isnull=True)
                | Q(publish_at__isnull=True)
                | Q(unpublish_at__gt=models.F("publish_at")),
                name="pages_page_unpublish_after_publish",
            ),
        )

    def __str__(self) -> str:
        return self.title or self.slug

    def get_absolute_url(self) -> str:
        return reverse("pages:page", kwargs={"slug": self.slug})

    @property
    def is_published(self) -> bool:
        now = timezone.now()
        if self.status != self.Status.PUBLISHED:
            return False
        if self.publish_at and self.publish_at > now:
            return False
        return not (self.unpublish_at and self.unpublish_at <= now)

    @property
    def last_modified(self):
        return self.updated_at

    @property
    def rendered(self) -> RenderedPage:
        return render_page(self.content, self.content_format)

    @property
    def rendered_content(self) -> str:
        return self.rendered.html

    @property
    def effective_seo_title(self) -> str:
        return self.seo_title or self.title

    @property
    def effective_seo_description(self) -> str:
        return self.seo_description or plain_text_excerpt(
            self.content, self.content_format
        )

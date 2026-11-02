from django.conf import settings
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        abstract = True

    def soft_delete(self, actor=None, *, using=None) -> None:
        self.is_deleted = True
        self.deleted_at = timezone.now()
        if actor is not None:
            self.deleted_by = actor
        self.save(
            using=using,
            update_fields=["is_deleted", "deleted_at", "deleted_by"],
        )

    def restore(self, *, using=None) -> None:
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.save(
            using=using,
            update_fields=["is_deleted", "deleted_at", "deleted_by"],
        )

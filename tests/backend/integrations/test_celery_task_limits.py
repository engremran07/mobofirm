"""Every Celery task is bounded in time and every scheduled task exists (F-011)."""

from __future__ import annotations

import pytest
from django.conf import settings

from app.celery import app as celery_app

pytestmark = pytest.mark.django_db  # importing task modules runs Celery's model fixup


@pytest.fixture(name="registered")
def registered_tasks():
    celery_app.loader.import_default_modules()
    return {
        name: task
        for name, task in celery_app.tasks.items()
        if name.startswith("apps.")
    }


def test_the_global_backstop_is_defined_and_consistent():
    assert 0 < settings.CELERY_TASK_SOFT_TIME_LIMIT < settings.CELERY_TASK_TIME_LIMIT


def test_every_task_has_a_hard_limit_of_its_own_or_the_global_one(registered):
    unbounded = sorted(
        name
        for name, task in registered.items()
        if not (task.time_limit or settings.CELERY_TASK_TIME_LIMIT)
    )

    assert registered
    assert unbounded == []


def test_a_task_soft_limit_is_below_its_hard_limit(registered):
    inverted = sorted(
        name
        for name, task in registered.items()
        if task.soft_time_limit
        and task.time_limit
        and task.soft_time_limit >= task.time_limit
    )

    assert inverted == []


def test_every_scheduled_task_is_registered(registered):
    missing = sorted(
        str(entry["task"])
        for entry in settings.CELERY_BEAT_SCHEDULE.values()
        if entry["task"] not in registered
    )

    assert missing == []


def test_core_tasks_declare_limits_and_are_safe_to_redeliver(registered):
    task = registered["apps.core.tasks.cleanup_old_data"]

    assert task.soft_time_limit and task.time_limit
    assert task.soft_time_limit < task.time_limit <= settings.CELERY_TASK_TIME_LIMIT
    assert task.acks_late is True

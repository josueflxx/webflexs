"""Celery app bootstrap for optional background jobs."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flexs_project.settings.production")

app = Celery("flexs_project")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


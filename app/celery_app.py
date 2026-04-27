from celery import Celery
from celery.schedules import crontab
from .config import settings

celery_app = Celery(
    "memoryweb",
    broker=settings.MW_CELERY_BROKER_URL,
    backend=settings.MW_CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    result_expires=86400,        # 24h
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    beat_schedule={
        "requeue-stalled-every-10min": {
            "task": "memoryweb.requeue_stalled",
            "schedule": 600,    # every 10 minutes
        },
        "sweep-unprocessed-every-15min": {
            "task": "memoryweb.sweep_unprocessed",
            "schedule": 900,    # every 15 minutes
        },
        "memory-decay-sweep-daily": {
            "task": "memoryweb.memory_decay_sweep",
            "schedule": crontab(hour=3, minute=0),  # daily at 03:00 UTC
        },
    },
)

celery_app.conf.imports = [
    "app.tasks.ingest_tasks",
    "app.tasks.pipeline_tasks",
]

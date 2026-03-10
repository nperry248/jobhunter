"""
workers/schedule.py — Celery Beat periodic task schedule.

CONCEPT — What is Celery Beat?
  Beat is a separate process that acts like a cron daemon for Celery.
  It runs alongside your workers and sends tasks to the queue on a schedule.

  Think of it this way:
    - Workers are the kitchen (they cook the food when orders arrive)
    - Beat is the waiter with a timer (every hour it places a new order)
    - Redis is the ticket rail between them

  You run Beat as a separate process:
    celery -A workers.celery_app beat --loglevel=info

  IMPORTANT: Only ever run ONE Beat process. Running two Beats would cause
  duplicate tasks (imagine two waiters both placing the same order every hour).

CONCEPT — crontab() vs timedelta():
  Celery Beat supports two schedule formats:

  1. `crontab(minute=0)` — Unix cron style: "at minute 0 of every hour"
     This is clock-based: it fires at 9:00, 10:00, 11:00, etc.

  2. `timedelta(hours=1)` — Interval style: "every 60 minutes from when Beat started"
     This is elapsed-time-based: fire 60 minutes after the last run.

  We use `crontab` because it gives predictable, clock-aligned runs.
  With timedelta, if Beat starts at 9:37, tasks fire at 10:37, 11:37, etc.
  With crontab(minute=0), they always fire on the hour.

CONFIGURATION:
  The schedule is attached directly to `celery_app.conf.beat_schedule`.
  Each entry maps a unique name → task name + schedule + optional kwargs.

  To change the frequency, edit the `schedule` value:
    - Every 30 minutes:  crontab(minute="*/30")
    - Every day at 8am:  crontab(hour=8, minute=0)
    - Every weekday:     crontab(hour=8, minute=0, day_of_week="mon-fri")
    - Every 5 minutes:   timedelta(minutes=5)  (good for dev/testing)
"""

from datetime import timedelta

from celery.schedules import crontab

from workers.celery_app import celery_app
from core.config import settings

# ── Periodic Task Schedule ────────────────────────────────────────────────────
#
# Each key in beat_schedule is a unique human-readable name for the job.
# The value is a dict with:
#   "task":     The registered task name (must match the `name=` in @celery_app.task)
#   "schedule": When to run (crontab or timedelta)
#   "kwargs":   Optional keyword arguments passed to the task each time it runs
#
celery_app.conf.beat_schedule = {
    # Run the full scrape + score pipeline at the top of every hour.
    # kwargs passes resume_path=None so the agent reads from UserProfile in DB.
    "scrape-and-score-hourly": {
        "task": "scrape_and_score_task",
        "schedule": timedelta(minutes=1),  # fires at :00 every hour or 1 min for dev testing
        "kwargs": {"resume_path": None},
        "options": {
            # CONCEPT — task routing:
            # If you later want to run scraping tasks on dedicated worker machines,
            # you can route them to a specific queue by setting "queue" here.
            # For now we use the default queue.
        },
    },
}

# CONCEPT — Beat persistence:
#   Beat needs to track the last time each task ran so it doesn't fire the same
#   task twice after a restart. By default it stores this in a local file
#   `celerybeat-schedule` (a shelve database). In production you'd use the
#   Django database scheduler or a Redis-backed scheduler, but the file-based
#   default works fine for a single-server setup.

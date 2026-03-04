"""
workers/celery_app.py — Creates and configures the Celery application instance.

CONCEPT — What is Celery?
  Celery is a distributed task queue. Your code puts "tasks" onto a queue
  (backed by Redis), and separate worker processes pick them up and run them.
  This lets you:
    1. Run work in the background without blocking the web server
    2. Schedule recurring tasks (like cron jobs, but in Python)
    3. Spread work across multiple machines by running more workers

CONCEPT — Broker vs Backend:
  - Broker (redis_url, DB 0): The message queue. Tasks are PUT here by the
    scheduler or your app, and READ here by workers. Think of it as the
    order ticket rail in a restaurant kitchen.
  - Backend (redis_url, DB 1): Where task RESULTS are stored after completion.
    We use a separate Redis DB (index 1) so results don't mix with task messages.
    Workers can store "task X returned Y" here; callers can retrieve it later.

WHY SEPARATE DB INDEXES?
  Redis supports 16 logical databases (0–15) on the same server.
  Using DB 0 for the broker and DB 1 for results keeps them cleanly separated
  without needing two Redis instances.

USAGE:
  Import `celery_app` wherever you define tasks or need to inspect the app.
  The worker process also imports this to discover registered tasks.

  Start a worker:
    celery -A workers.celery_app worker --loglevel=info

  Start the Beat scheduler (triggers periodic tasks):
    celery -A workers.celery_app beat --loglevel=info
"""

from celery import Celery

from core.config import settings

# ── Create the Celery application ─────────────────────────────────────────────
# The first argument ("jobhunter") is the app name — it's used as a prefix
# in log messages and task names, so pick something descriptive.
#
# broker: Where tasks are queued (Redis DB 0)
# backend: Where results are stored (Redis DB 1)
celery_app = Celery(
    "jobhunter",
    broker=settings.redis_url,
    backend=settings.celery_result_backend,
    # Tell Celery where to find task definitions.
    # When the worker starts, it imports these modules and discovers all @task
    # decorators. Without this, tasks won't be found.
    include=["workers.tasks"],
)

# ── Serialization settings ────────────────────────────────────────────────────
# CONCEPT — Serialization:
#   When a task is placed on the queue, its arguments must be converted to
#   bytes (serialized) so they can travel over the network to Redis.
#   JSON is the safest choice: human-readable, language-agnostic, and avoids
#   the security vulnerabilities of Python's pickle format.
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Always use UTC internally. Avoids daylight-saving-time bugs.
    timezone="UTC",
    enable_utc=True,

    # CONCEPT — Task acknowledgement:
    #   By default, Celery acknowledges (removes from queue) a task the moment
    #   a worker receives it — even before it finishes. If the worker crashes
    #   mid-task, the task is lost.
    #
    #   `acks_late=True` delays acknowledgement until AFTER the task completes
    #   successfully. If the worker crashes, the task stays on the queue and
    #   another worker will pick it up. Much safer for long-running agent tasks.
    task_acks_late=True,

    # CONCEPT — Worker prefetch:
    #   By default workers grab multiple tasks at once (prefetch). For short
    #   tasks this is a performance win. For our long-running agent tasks
    #   (scraping + scoring can take 60+ seconds), prefetching means one worker
    #   hogs tasks while other workers sit idle.
    #   Setting prefetch to 1 means each worker takes exactly one task at a time.
    worker_prefetch_multiplier=1,
)

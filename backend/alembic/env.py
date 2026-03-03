"""
alembic/env.py — Alembic environment configuration.

This file tells Alembic:
  1. Where to find the database (via DATABASE_URL from settings)
  2. Which models to inspect for autogenerate (via Base.metadata)
  3. How to run migrations (we use async because our driver is asyncpg)

CONCEPT — Why we need asyncio here:
  asyncpg is an async-only PostgreSQL driver. Alembic was originally designed for
  synchronous drivers. To bridge the gap, we use asyncio.run() to run the async
  migration code from the sync Alembic context.

CONCEPT — Offline vs Online migrations:
  Offline mode: Alembic doesn't connect to the DB — just outputs raw SQL to a file.
    Use this to generate SQL scripts to review before running (useful in production CI).
  Online mode: Alembic connects to the DB directly and runs the migration.
    This is what we use locally.
"""

import asyncio
import sys
import os
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import pool

from alembic import context

# ── Path fix ─────────────────────────────────────────────────────────────────
# Alembic is run from the `backend/` directory, but Python needs to find our
# modules (core.config, models, etc.). Add `backend/` to sys.path.
# NOTE: This is a common "gotcha" when setting up Alembic in a structured project.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Import our app's config and models ───────────────────────────────────────
# IMPORTANT: Import all models so SQLAlchemy's metadata knows about them.
# If a model isn't imported here, Alembic won't see it in --autogenerate.
from core.config import settings
from core.database import Base

# Importing models registers them with Base.metadata (SQLAlchemy's registry).
# Even though we don't use these names directly, the import side-effect is required.
import models  # noqa: F401 — imports Job, Application, UserProfile via models/__init__.py

# ── Alembic config object ────────────────────────────────────────────────────
config = context.config

# Interpret alembic.ini's [loggers] section for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Target metadata ───────────────────────────────────────────────────────────
# This tells Alembic what the "desired" schema looks like (from our Python models).
# It compares this against the actual DB schema to generate diffs (autogenerate).
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — outputs SQL to stdout instead of running it.
    Useful for: reviewing SQL before applying, generating migration scripts for DBAs.

    Usage: alembic upgrade head --sql > migration.sql
    """
    # In offline mode, we use a synchronous URL (swap asyncpg for psycopg2 syntax)
    # because we're not actually connecting — just generating SQL text.
    url = settings.database_url.replace("+asyncpg", "")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Compare server defaults so Alembic notices changes to column defaults.
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """
    Configure Alembic context with an active DB connection and run migrations.
    Called from both online modes (sync wrapper and async wrapper).
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect changes to column types, not just added/dropped columns.
        compare_type=True,
        # Detect changes to server-side defaults.
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations asynchronously using our async engine.

    NOTE: We use NullPool here (not our regular pool_size=10 pool).
    WHY: Alembic runs migrations as a one-shot process — it connects, migrates, disconnects.
    A persistent connection pool (like our production one) would hold open connections
    even after Alembic exits, which is wasteful for a CLI tool.
    NullPool creates connections on-demand and immediately closes them after use.
    """
    # Create a fresh engine just for migrations (not reusing the production pool)
    connectable = create_async_engine(
        settings.database_url,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # NOTE: Alembic's context.configure() and context.run_migrations() are sync.
        # We use `run_sync` to run them inside an async connection context.
        # This is the standard pattern for async Alembic as of SQLAlchemy 2.0.
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    Entry point for online migrations. Bridges sync Alembic → async SQLAlchemy.
    asyncio.run() creates a new event loop, runs our async migration, then exits.
    """
    asyncio.run(run_async_migrations())


# ── Dispatch ─────────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

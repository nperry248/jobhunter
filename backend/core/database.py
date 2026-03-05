"""
core/database.py — SQLAlchemy async engine, session factory, and base model.

THREE THINGS THIS FILE PROVIDES:
  1. `engine`       — The async connection pool to PostgreSQL (one per app instance)
  2. `AsyncSession` — A session factory: each request gets one session (one DB transaction)
  3. `Base`         — All SQLAlchemy models inherit from this to register with Alembic

CONCEPT — Why async?
  FastAPI can handle thousands of concurrent requests. If DB operations were synchronous
  (blocking), a slow query would freeze ALL other requests. Async DB calls release control
  back to FastAPI while waiting for PostgreSQL, so other requests can proceed.

CONCEPT — Connection Pooling:
  Opening a raw TCP connection to PostgreSQL takes ~50ms and hits a max connection limit.
  A pool keeps N connections warm and reuses them. Under light load, 10 connections serve
  hundreds of requests per second. Under heavy load, it grows up to max_overflow before
  queuing requests — no crashes, just a brief wait.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from core.config import settings


# ── Engine ────────────────────────────────────────────────────────────────────
# The engine is the core connection pool. Create it ONCE at module load time
# (not per-request!) because it manages the pool of persistent connections.
#
# NOTE: `echo=False` in production — set to True temporarily when debugging SQL.
# With echo=True, every SQL statement is printed to the console.
engine = create_async_engine(
    settings.database_url,
    # pool_size: number of connections to keep open permanently.
    # Each Celery worker and the API server share this pool.
    pool_size=10,
    # max_overflow: how many *extra* connections can be created under peak load.
    # Total ceiling = pool_size + max_overflow = 30 connections per process.
    max_overflow=20,
    # pool_pre_ping: before reusing a connection, test it with "SELECT 1".
    # This prevents "connection reset" errors if PostgreSQL restarted.
    pool_pre_ping=True,
    # echo: log every SQL statement. Keep False in production.
    echo=False,
)


# ── Session Factory ───────────────────────────────────────────────────────────
# A "session" represents one unit of work with the database (one transaction).
# `async_sessionmaker` creates new AsyncSession objects on demand.
#
# expire_on_commit=False: by default, SQLAlchemy expires all model attributes
# after a commit, meaning accessing them fires another SELECT. Since we're async,
# that would cause issues. Setting this to False keeps attribute values accessible
# after commit without re-querying.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Declarative Base ──────────────────────────────────────────────────────────
# All SQLAlchemy model classes (Job, Application, UserProfile) inherit from Base.
# This registers them with SQLAlchemy's metadata so Alembic can discover them
# and generate migrations automatically.
class Base(DeclarativeBase):
    """Base class for all database models. Inherit from this, not directly from SQLAlchemy."""
    pass


# ── Dependency: get_db ────────────────────────────────────────────────────────
# CONCEPT — Dependency Injection:
#   FastAPI's `Depends(get_db)` pattern means: "before calling this route handler,
#   call get_db() and pass its result as the `db` argument."
#
#   This is the standard pattern for database sessions in FastAPI:
#   - A new session is created at the start of each request
#   - The session is automatically closed (and connection returned to pool) when done
#   - If an exception is raised, the session is rolled back, preventing partial writes
#
# Usage in a route:
#   @router.get("/jobs")
#   async def list_jobs(db: AsyncSession = Depends(get_db)):
#       ...
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager that yields a database session for one request.
    Always closes the session when the request finishes (success or error).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            # If we reach here without an exception, commit any pending changes.
            await session.commit()
        except Exception:
            # Roll back any partial changes if something went wrong.
            await session.rollback()
            raise
        # The `async with` block automatically closes the session here.


# ── Context Manager: get_db_context ───────────────────────────────────────────
# A version of get_db() for use OUTSIDE of FastAPI's dependency injection system.
# Use this in agents/scripts that need a DB session but aren't route handlers.
#
# WHY A SEPARATE FUNCTION?
#   `get_db()` is an async generator designed for FastAPI's Depends() mechanism.
#   Outside FastAPI routes (e.g. background agents, CLI scripts), you can't use Depends().
#   `get_db_context()` gives you the same session + commit/rollback behaviour as an
#   async context manager you can use with `async with get_db_context() as session:`.
#
# Usage:
#   async with get_db_context() as session:
#       result = await session.execute(select(Job))
@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager yielding a database session for use outside FastAPI routes.
    Commits on clean exit, rolls back on exception, always closes the session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

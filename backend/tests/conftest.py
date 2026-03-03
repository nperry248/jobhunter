"""
tests/conftest.py — Shared test fixtures for the entire test suite.

CONCEPT — pytest fixtures:
  A "fixture" is a function that sets up (and tears down) test dependencies.
  Instead of repeating setup code in every test, you declare what you need
  as a function argument and pytest injects it automatically.

  Example:
    def test_something(db_session):   # pytest sees `db_session`, calls the fixture
        ...

WHAT THIS FILE PROVIDES:
  - `event_loop`: required for async tests (pytest-asyncio needs an event loop)
  - `test_engine`: a SQLAlchemy engine pointing at the test DB
  - `setup_test_db`: creates all tables before tests, drops them after
  - `db_session`: yields a clean DB session for each test (rolled back after)
  - `client`: an httpx AsyncClient that talks to the FastAPI test app

ISOLATION STRATEGY:
  Each test runs inside a transaction that is ROLLED BACK at the end.
  This means:
  1. Tests see a fresh, empty DB every time
  2. Tests don't affect each other (no shared state)
  3. No need to manually delete test data
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.main import app
from core.config import settings
from core.database import Base, get_db


# ── Test Database Engine ───────────────────────────────────────────────────────
# Session-scoped = created ONCE at the start of the test run, shared by all tests.
# We point it at TEST_DATABASE_URL (the `jobhunter_test` database) — never the dev DB.
@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """
    Create an async SQLAlchemy engine connected to the TEST database.
    NullPool: each connection is opened and closed immediately (no pool persistence).
    This prevents "too many connections" errors when running many tests.
    """
    engine = create_async_engine(
        settings.test_database_url,
        poolclass=NullPool,  # No persistent pool in tests — keep it clean
    )
    yield engine
    await engine.dispose()


# ── Create / Drop Tables ───────────────────────────────────────────────────────
# Session-scoped = runs ONCE before any test in the session.
# Creates all tables from our models at test startup, drops them at teardown.
@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db(test_engine):
    """
    Create all database tables before any tests run.
    Drop all tables after the entire test session completes.

    WHY NOT USE ALEMBIC HERE?
    Running alembic upgrade head in tests is slow and can fail if migrations
    have bugs. Using SQLAlchemy's create_all() is faster and tests the models
    directly — which is what we actually care about in unit/integration tests.
    E2E tests (Phase 3+) should use Alembic.
    """
    async with test_engine.begin() as conn:
        # Import models so their metadata is registered with Base
        import models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)

    yield  # ← Tests run here

    # Teardown: drop all tables after the session completes
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── DB Session Fixture ─────────────────────────────────────────────────────────
# Function-scoped (default) = a new session for EACH test function.
# The transaction is rolled back after each test, leaving the DB clean.
@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a database session for one test. Rolls back all changes after the test.

    ISOLATION TECHNIQUE — Nested transactions:
      We begin a transaction, then use a "savepoint" (nested transaction) for the test.
      After the test, we roll back to the savepoint. The outer transaction is also
      rolled back, so the DB is left in a pristine state for the next test.
    """
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        # Begin a transaction manually so we can ALWAYS roll it back in `finally`.
        #
        # WHY NOT `async with session.begin()`?
        #   That context manager commits the transaction when the `with` block exits
        #   cleanly (i.e. when the test passes without raising). That means every
        #   passing test permanently writes to the DB, and the next test sees those
        #   rows — breaking isolation.
        #
        # WHY `try/finally`?
        #   `finally` runs whether the test passes or fails, so `rollback()` is
        #   guaranteed. No data from one test can ever leak into the next.
        await session.begin()
        try:
            yield session
        finally:
            await session.rollback()


# ── FastAPI Test Client ────────────────────────────────────────────────────────
# This fixture creates an HTTP client that talks directly to our FastAPI app
# (no real network — it's all in-process). This is how we test API routes.
@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    Return an httpx AsyncClient wired to the FastAPI test app.
    Overrides the `get_db` dependency so routes use our test DB session.

    CONCEPT — Dependency Override:
      FastAPI lets you replace ("override") any dependency with a different function
      during testing. Here we replace `get_db` (which normally opens a real prod DB
      session) with a function that returns our test session.
      This means API routes automatically use the test DB — no code changes needed.
    """
    # Override `get_db` with a function that returns our test session
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    # Clean up: remove the override so it doesn't affect other test modules
    app.dependency_overrides.clear()

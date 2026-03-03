"""
core/config.py — Centralized configuration via Pydantic Settings.

WHY THIS EXISTS:
  All settings (DB URLs, API keys, thresholds) live here in one place.
  The app reads from environment variables (loaded from .env by python-dotenv).
  If a required setting is missing, the app fails loudly at startup rather than
  crashing mysteriously later — the "fail fast" principle.

HOW IT WORKS:
  1. Pydantic reads your .env file
  2. Each field has a type annotation (str, int, float, list[str])
  3. Pydantic validates every value and raises a clear error if types don't match
  4. You get a fully type-safe `settings` object to import anywhere
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the .env file path relative to THIS file, not the working directory.
# config.py lives at backend/core/config.py, so .parent.parent is backend/,
# and one more .parent is the project root where .env lives.
# WHY: if you run `uvicorn api.main:app` from backend/, the cwd is backend/.
# A plain `env_file=".env"` would look for backend/.env, which doesn't exist.
# Using an absolute path means it always finds project_root/.env correctly.
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    """
    All application settings, loaded from environment variables / .env file.

    IMPORTANT: Add any new config values here — never use os.getenv() directly elsewhere.
    """

    # ── Pydantic Settings config ───────────────────────────────────────────────
    # model_config tells Pydantic where to find the .env file.
    # `extra="ignore"` means unknown env vars won't cause errors.
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── AI ────────────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""

    # ── Database ──────────────────────────────────────────────────────────────
    # NOTE: The URL must use `postgresql+asyncpg://` (not `postgresql://`) so
    # SQLAlchemy knows to use the async asyncpg driver instead of psycopg2.
    database_url: str = "postgresql+asyncpg://jobhunter:jobhunter@localhost:5432/jobhunter"
    test_database_url: str = "postgresql+asyncpg://jobhunter:jobhunter@localhost:5432/jobhunter_test"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── API Server ────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # CORS origins: which frontend URLs are allowed to make API requests.
    # In .env, set as a comma-separated string: "http://localhost:5173,http://localhost:3000"
    # The @field_validator below converts that string into a Python list.
    #
    # NOTE on pydantic-settings v2: if the type is purely List[str], pydantic-settings
    # tries to JSON-decode the raw env value before our validator runs, and fails on
    # comma-separated strings like "http://a,http://b". Declaring it as `list | str`
    # tells pydantic-settings to pass the raw string through unchanged, letting our
    # validator handle the conversion.
    backend_cors_origins: list | str = ["http://localhost:5173", "http://localhost:3000"]

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list) -> list:
        """
        Convert a comma-separated CORS string from .env into a Python list.
        If it's already a list (e.g. from a test override), pass through unchanged.
        """
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",")]
        return value

    # ── Agent Behavior ────────────────────────────────────────────────────────
    scraper_max_jobs_per_run: int = 100
    match_score_threshold: int = 70
    max_retry_attempts: int = 3
    retry_base_delay: float = 1.0
    scraper_interval_seconds: int = 3600
    scraper_request_timeout: int = 30  # seconds before an HTTP request gives up

    # Seconds to wait between Claude API calls in the Resume Match agent.
    # WHY: Claude's API has rate limits (requests-per-minute). If we score 100 jobs
    # with zero delay we'll hit the limiter and get 429 errors. A 0.5s pause keeps
    # us well under the limit without meaningfully slowing down the agent.
    # Set to 0.0 in tests (via override) so tests don't sleep.
    claude_request_delay_seconds: float = 0.5

    # ── Scraper — Company Slugs ───────────────────────────────────────────────
    # Which companies to scrape, stored as JSON dicts: {"slug": "Human Name"}.
    # The slug is used in the API URL; the name is stored in the DB.
    #
    # How to find a company's slug:
    #   Greenhouse: go to their jobs page, e.g. https://boards.greenhouse.io/airbnb
    #               The slug is "airbnb"
    #   Lever:      go to https://jobs.lever.co/notion
    #               The slug is "notion"
    #
    # In .env, set as a JSON string (use single quotes around the whole thing):
    #   GREENHOUSE_SLUGS={"airbnb": "Airbnb", "figma": "Figma", "ramp": "Ramp"}
    greenhouse_slugs: dict = {
        "airbnb": "Airbnb",
        "figma": "Figma",
        "ramp": "Ramp",
        "coinbase": "Coinbase",
        "plaid": "Plaid",
    }
    lever_slugs: dict = {
        "notion": "Notion",
    }

    @field_validator("greenhouse_slugs", "lever_slugs", mode="before")
    @classmethod
    def parse_slug_dict(cls, value: str | dict) -> dict:
        """
        Allow slug dicts to be set as JSON strings in .env.
        If the value is already a dict (e.g. from code), pass through unchanged.
        """
        if isinstance(value, str):
            return json.loads(value)
        return value

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"


# ── Singleton pattern via lru_cache ───────────────────────────────────────────
# @lru_cache means this function is only ever called ONCE, no matter how many
# times you call get_settings(). The result is cached.
# WHY: Reading and validating the .env file on every request would be slow and wasteful.
# This way, settings are parsed at startup and reused everywhere.
@lru_cache()
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()


# Convenience: a module-level `settings` object for simple imports.
# Usage: `from core.config import settings`
settings = get_settings()

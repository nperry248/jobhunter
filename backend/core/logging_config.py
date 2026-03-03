"""
core/logging_config.py — Shared structured JSON logging for all agents.

WHY THIS EXISTS:
  Every agent needs logging. Instead of each agent configuring its own logger
  (copy-paste = drift and inconsistency), they all call get_logger() here.
  One place to change the format, one place to adjust the level.

WHY JSON LOGS:
  Plain text logs ("Scraped job at Airbnb") are for humans to read in a terminal.
  JSON logs are for machines to parse. When you have 10 agents running in parallel,
  you need to be able to filter: "show me all errors from the scraper agent for
  job_id=abc-123". JSON makes that possible.

  Each log line will look like:
  {
    "timestamp": "2024-01-01T12:00:00",
    "level": "INFO",
    "agent": "scraper",
    "message": "job upserted",
    "company": "Airbnb",
    "job_id": "abc-123",
    "was_new": true
  }

USAGE IN AN AGENT:
  from core.logging_config import get_logger
  logger = get_logger("scraper")
  logger.info("job upserted", extra={"company": "Airbnb", "job_id": str(job.id)})
"""

import logging
import sys

from pythonjsonlogger import jsonlogger

from core.config import settings


def get_logger(agent_name: str) -> logging.Logger:
    """
    Return a JSON-formatted logger for the given agent name.

    The logger name is prefixed with "agents." so log levels can be tuned
    per-agent in config if needed: e.g. logging.getLogger("agents.scraper").

    Args:
        agent_name: Short name for the agent, e.g. "scraper", "apply"

    Returns:
        A configured Logger instance with JSON output handler attached.
    """
    logger = logging.getLogger(f"agents.{agent_name}")

    # Guard: only add the handler once. Without this check, calling get_logger()
    # multiple times (e.g. in tests) would add duplicate handlers and print
    # every log line twice.
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)

    # JsonFormatter converts the LogRecord into a JSON string.
    # The `fmt` argument lists which standard LogRecord fields to include.
    # Extra fields (passed via logger.info("msg", extra={...})) are included automatically.
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "agent"},
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Set level from settings so LOG_LEVEL=DEBUG in .env gives verbose output.
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    # Prevent log records from bubbling up to the root logger (which might
    # have its own handler that would print a duplicate plain-text line).
    logger.propagate = False

    return logger

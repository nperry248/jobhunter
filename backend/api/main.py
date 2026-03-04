"""
api/main.py — The FastAPI application entry point.

WHAT THIS FILE DOES:
  1. Creates the FastAPI app instance
  2. Attaches CORS middleware (so the React frontend can talk to this API)
  3. Registers all API routers (jobs, applications, config)
  4. Defines the /health endpoint
  5. Manages app startup/shutdown lifecycle (DB connection pool, etc.)

CONCEPT — FastAPI vs Flask:
  Flask is synchronous: one request at a time per thread.
  FastAPI is async: one worker can handle thousands of concurrent requests by
  yielding control while waiting for DB/HTTP I/O. This matters because our agents
  make many outbound HTTP calls and DB writes simultaneously.

HOW TO RUN:
  cd backend
  uvicorn api.main:app --reload --port 8000

  --reload: restarts the server on file changes (dev only)
  Access auto-generated API docs at: http://localhost:8000/docs
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.database import engine

from api.routes import jobs, profile
# TODO: Uncomment as routes are implemented in later steps:
# from api.routes import applications

# ── Structured Logging Setup ──────────────────────────────────────────────────
# We configure logging once at app startup so all modules share the same format.
# CONCEPT — Structured logging:
#   Plain text logs like "Job scraped: Software Engineer at Google" are human-readable
#   but hard to search or parse programmatically. JSON logs like
#   {"level": "INFO", "agent": "scraper", "job_id": "...", "msg": "scraped"}
#   can be queried by log aggregation tools (Datadog, Loki, etc.).
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan Context Manager ──────────────────────────────────────────────────
# CONCEPT — Lifespan events:
#   FastAPI's `lifespan` is a newer pattern that replaces the older @app.on_event("startup").
#   Everything BEFORE `yield` runs once when the server starts.
#   Everything AFTER `yield` runs once when the server shuts down.
#   This is where we initialize/close the DB connection pool, check service health, etc.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage app startup and shutdown. Runs once — not per-request.
    """
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info("JobHunter AI API starting up...")
    logger.info(f"Log level: {settings.log_level}")
    logger.info(f"CORS origins: {settings.backend_cors_origins}")
    # The engine (connection pool) is created lazily on first use.
    # We can do a "ping" here to fail fast if DB is unreachable at startup.
    # (Skipping the ping for now so the server starts even without Docker running)

    yield  # ← The app is running here, handling requests

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("JobHunter AI API shutting down...")
    # Dispose the connection pool so all connections are cleanly closed.
    # Without this, you may see "connection still open" warnings.
    await engine.dispose()
    logger.info("Database connection pool closed.")


# ── FastAPI App Instance ──────────────────────────────────────────────────────
app = FastAPI(
    title="JobHunter AI",
    description="Multi-agent system for autonomous job hunting. Built with FastAPI + SQLAlchemy.",
    version="0.1.0",
    # lifespan tells FastAPI to use our startup/shutdown context manager.
    lifespan=lifespan,
    # docs_url: where Swagger UI lives. Visit http://localhost:8000/docs
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── CORS Middleware ───────────────────────────────────────────────────────────
# CONCEPT — Middleware:
#   Middleware is code that runs for EVERY request/response, before your route handler.
#   Think of it as a pipeline: Request → Middleware → Route Handler → Middleware → Response.
#
# CORSMiddleware adds the necessary response headers that tell browsers
# "this backend trusts requests from these origins."
#
# allow_credentials=True: required if React sends cookies or auth headers.
# allow_methods=["*"]:    allow GET, POST, PUT, DELETE, PATCH, OPTIONS.
# allow_headers=["*"]:    allow any request headers (Authorization, Content-Type, etc.).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API Routers ───────────────────────────────────────────────────────────────
# CONCEPT — Routers:
#   Instead of defining all routes in this one file (which would get massive),
#   we use FastAPI's APIRouter to define routes in separate files and "include" them here.
#   The `prefix` means every route in jobs.py is automatically under /api/v1/jobs.
#
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(profile.router, prefix="/api/v1/profile", tags=["profile"])
# TODO: Uncomment as route files are implemented:
# app.include_router(applications.router, prefix="/api/v1/applications", tags=["applications"])


# ── Health Check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health_check() -> dict:
    """
    Simple health check endpoint. Returns 200 OK when the server is running.

    Used by:
    - Docker health checks to know if the container is healthy
    - Load balancers to decide if traffic should be routed here
    - Your own sanity check: `curl localhost:8000/health`
    """
    return {
        "status": "ok",
        "service": "jobhunter-api",
        "version": "0.1.0",
    }


# ── Root Redirect ─────────────────────────────────────────────────────────────
@app.get("/", tags=["system"])
async def root() -> dict:
    """Root endpoint — useful for confirming the server is up and finding the docs."""
    return {
        "message": "JobHunter AI API is running.",
        "docs": "/docs",
        "health": "/health",
    }

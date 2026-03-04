# CLAUDE.md — JobHunter AI

> This file is read by Claude Code at the start of every session.
> Keep it updated as the project evolves.

---

## Project Summary

JobHunter AI is a pipeline-based job hunting system that scrapes SWE listings, scores them against a resume using Claude AI, and surfaces matches in a React dashboard. The eventual goal is a fully autonomous multi-agent system that can apply to jobs without human intervention. Each "agent" today is a well-structured script with a single responsibility — they will become the tools of a real LLM-driven orchestrator in Phase 4.

---

## Important Concept: "Agents" vs Real Agents

The scripts in `backend/agents/` are called agents because they have single responsibilities and clean `run()` entry points — but they are NOT yet AI agents in the LangChain/agentic sense. They follow fixed, deterministic sequences:

- `scraper.py` — HTTP calls → filter → DB write. No AI, no decisions.
- `resume_match.py` — fetch jobs → call Claude → store score. Claude scores text; it doesn't make decisions about what to do next.

A real AI agent would receive a goal, decide which tools to call, observe the results, and adapt. That's Phase 4 (Orchestrator). For now, these are scripts with good architecture that will become the tools a real agent uses.

---

## Current Phase

**Phase 3C — Apply Agent** ← UP NEXT

---

## Phase History

### Phase 1 — Foundation ✅ COMPLETE

- Docker Compose (PostgreSQL + Redis)
- `backend/core/config.py` — Pydantic Settings, reads `.env`
- `backend/core/database.py` — async SQLAlchemy, pool_size=10, max_overflow=20
- `backend/models/` — Job, Application, UserProfile (UUIDs, indexes, soft deletes, timestamps)
- Alembic migrations — initialized + initial migration applied
- `backend/api/main.py` — FastAPI app, CORS, `/health` endpoint
- `backend/core/logging_config.py` — JSON structured logging
- `backend/agents/scraper.py` — Greenhouse + Lever scraper, retry logic, upsert deduplication
- `backend/agents/scraper_parsers.py` — ParsedJob, ScraperFilters, pure parse + filter functions
- `frontend/` — Vite + React + Tailwind, sidebar layout
- 18 passing tests

---

### Phase 2 — Resume Match Agent + Jobs API ✅ COMPLETE

- `backend/services/resume_parser.py` — `parse_pdf()` (pdfminer.six) + `strip_html()` (regex)
- `backend/agents/resume_match_logic.py` — pure functions: `MatchConfig`, `build_scoring_prompt`, `parse_claude_response`, `clamp_score`
- `backend/agents/resume_match.py` — orchestration: `run()`, `load_resume_text()`, `fetch_new_jobs()`, `score_job()` via `asyncio.to_thread`, `update_job_score()`
- `backend/api/routes/jobs.py` — `GET /api/v1/jobs` (paginated, filterable) + `PATCH /api/v1/jobs/{id}`
- `frontend/src/pages/JobsPage.jsx` — full dashboard: score badges, status filters, pagination, undo reviewed
- `frontend/src/api/client.js` — `getJobs()`, `updateJobStatus()`
- 169 passing tests

**Key design decisions:**
- Resume Match uses `claude-haiku-4-5-20251001` (~25× cheaper than Sonnet, fast enough for JSON scoring)
- `score_job()` is sync, called via `asyncio.to_thread()` — Anthropic SDK is sync-only
- `parse_claude_response()` has two fallback strategies: direct JSON parse → regex extract → (0.0, error)
- `GET /jobs` returns `{jobs, total, limit, offset}` envelope, ordered by `match_score DESC NULLS LAST`
- `PATCH /jobs/{id}` allows `"reviewed"`, `"ignored"`, or `"scored"` (undo reviewed → scored)
- Scoring prompt calibrated for early-career: ignores years-of-experience requirements, scores "worth applying?" not "will you get hired?"

---

### Phase 3A — User Profile API + Settings UI ✅ COMPLETE

- `backend/api/routes/profile.py` — `GET /api/v1/profile` (auto-create on first call) + `PUT /api/v1/profile`
- `frontend/src/pages/SettingsPage.jsx` — full settings form: personal info, online presence, resume path, job preferences (toggles for remote/open-to-relocate/exclude-senior)
- JSON list fields (`target_locations`, `company_blocklist`) stored as JSON strings in DB, exposed as `list[str]` via Pydantic `@field_validator`
- 9 integration tests in `tests/integration/test_api_profile.py`
- 209 passing tests total

---

### Phase 3B — Celery Scheduling + Run Now Button ✅ COMPLETE

- `backend/workers/celery_app.py` — Celery app wired to Redis, `task_acks_late=True`, `prefetch_multiplier=1`
- `backend/workers/tasks.py` — `scrape_task`, `score_task`, `scrape_and_score_task` (all use `asyncio.run()` to bridge sync Celery → async agents)
- `backend/workers/schedule.py` — Celery Beat fires `scrape_and_score_task` at top of every hour
- `backend/api/routes/pipeline.py` — `POST /api/v1/pipeline/run` (FastAPI BackgroundTasks, no Celery worker needed in dev) + `GET /api/v1/pipeline/status`
- "Run Now" button in dashboard: polls status every 3s, shows pulsing indicator while running, displays result banner (new jobs / scored / duplicates) on completion
- 13 unit tests for Celery tasks, 9 integration tests for pipeline endpoints

**To run Celery in production:**
```bash
celery -A workers.celery_app worker --loglevel=info   # Terminal 1
celery -A workers.celery_app beat --loglevel=info -S workers.schedule  # Terminal 2
```

---

### Phase 2 — Scraper Improvements ✅ COMPLETE (same session)

- Tightened `swe_title_keywords` — removed standalone `"engineer"`/`"developer"` (too broad), replaced with specific phrases like `"software engineer"`, `"backend engineer"`, `"ml engineer"` etc.
- Added `max_jobs_per_company` (default: 5) to `ScraperFilters` + `config.py`
- Restructured fetch loop to filter inline per-company — prevents first company filling entire quota
- Fixed CLI `--dry-run` bug: was using empty keywords (no filter), now falls back to `swe_title_keywords`
- 30+ companies across Greenhouse + Lever

---

## What's Next — Phase 3C: Apply Agent

The last remaining piece of Phase 3. Playwright browser automation that reads a user's "reviewed" jobs from the DB, loads each application URL, fills the form using data from `UserProfile`, and submits.

**Scope: Greenhouse applications only** (most standardized forms — Lever and others later)

**What to build:**
1. `backend/agents/apply.py`
   - `run(job_ids: list[UUID] | None)` — apply to specified jobs, or all "reviewed" jobs above `APPLY_MIN_SCORE`
   - `apply_greenhouse(job: Job, profile: UserProfile, page: Page)` — Playwright automation
   - Screenshots saved to `data/screenshots/` for audit trail
   - Job status updated to `"applied"` or `"failed"` after each attempt
2. New config vars in `core/config.py` + `.env.example`:
   - `APPLY_HEADLESS=true` — run browser headless in prod, false for debugging
   - `APPLY_MIN_SCORE=70` — only apply to jobs scoring above this threshold
   - `SCREENSHOTS_DIR=data/screenshots`
3. Tests: `tests/unit/test_apply.py` + `tests/integration/test_apply_pipeline.py`

**Key design notes:**
- Load profile from DB at start of run — fail fast if no profile or no resume
- Apply to each job in a try/except — one failure must not stop the others
- Screenshot the final page (success or error) before moving on
- Greenhouse forms vary by company — need to handle optional fields gracefully

---

## Tech Stack

**Backend**
- Python 3.11+, FastAPI, SQLAlchemy (async), Alembic
- PostgreSQL + Redis (Docker)
- Anthropic Claude API — `claude-haiku-4-5-20251001` for scoring
- httpx (async HTTP), pdfminer.six (PDF parsing)
- Celery (task queue — wired for Phase 3 scheduling)
- Playwright (browser automation — Phase 3 Apply Agent)

**Frontend**
- React 18, Vite, Tailwind CSS

**Future (Phase 4)**
- LangChain or direct Anthropic tool-use for real agent orchestration

---

## Key Commands

```bash
# Start local services (postgres + redis)
docker-compose up -d

# Backend
cd backend
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000

# Run agents manually
python -m agents.scraper --dry-run
python -m agents.scraper
python -m agents.resume_match --resume /absolute/path/to/data/resumes/NickPerryResume.pdf
# NOTE: resume path must be absolute — relative paths resolve from backend/, not project root

# Celery (optional — Run Now button uses BackgroundTasks in dev, no worker needed)
celery -A workers.celery_app worker --loglevel=info        # executes tasks
celery -A workers.celery_app beat --loglevel=info -S workers.schedule  # triggers schedule

# Database migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Frontend
cd frontend
npm install
npm run dev   # runs on localhost:5173

# Tests
cd backend
pytest tests/ -v
pytest tests/ --cov=. --cov-report=term-missing
```

---

## File Structure

```
job-agent/
├── backend/
│   ├── agents/
│   │   ├── scraper.py              # Greenhouse + Lever scraper
│   │   ├── scraper_parsers.py      # Pure parse + filter logic
│   │   ├── resume_match.py         # Resume scoring orchestration
│   │   └── resume_match_logic.py   # Pure scoring functions
│   ├── api/
│   │   ├── main.py                 # FastAPI app, CORS, routers
│   │   └── routes/
│   │       ├── jobs.py             # GET /jobs, PATCH /jobs/{id}, DELETE /jobs
│   │       ├── profile.py          # GET /profile, PUT /profile
│   │       └── pipeline.py         # POST /pipeline/run, GET /pipeline/status
│   ├── workers/
│   │   ├── celery_app.py           # Celery app instance + config
│   │   ├── tasks.py                # scrape_task, score_task, scrape_and_score_task
│   │   └── schedule.py             # Celery Beat hourly schedule
│   ├── core/
│   │   ├── config.py               # Pydantic Settings (reads .env)
│   │   ├── database.py             # Async SQLAlchemy engine + session
│   │   └── logging_config.py       # JSON structured logging
│   ├── models/
│   │   ├── job.py                  # Job (title, company, score, status)
│   │   ├── application.py          # Application (job_id, status, screenshot)
│   │   └── user_profile.py         # Resume path, personal info for Apply Agent
│   ├── services/
│   │   └── resume_parser.py        # PDF → text, HTML stripping
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── unit/
│   │   └── integration/
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── JobsPage.jsx        # Main dashboard (filter, score badges, Run Now, Clear All)
│       │   └── SettingsPage.jsx    # User profile form (personal info, preferences, toggles)
│       └── api/client.js           # getJobs, updateJobStatus, clearAllJobs, runPipeline, getPipelineStatus, getProfile, updateProfile
├── data/
│   └── resumes/                    # Resume PDFs (gitignored)
├── assets/                         # Static assets (screenshots for README etc.)
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Coding Conventions

- **Python:** PEP 8, type hints everywhere, docstrings on all public functions
- **Async:** `async/await` throughout backend
- **Agents:** each lives in `backend/agents/` with a clean `run()` entry point
- **API routes:** one file per resource in `backend/api/routes/`
- **No hardcoding:** all secrets, URLs, thresholds in `.env` via `core/config.py`
- **Error handling:** catch exceptions, log them, continue — never crash silently
- **Commits:** `type(scope): description` — e.g. `feat(scraper): add LinkedIn parser`
- **Tests:** write alongside code, not after. Run full suite before every commit.

---

## Agent Responsibilities

| Agent | File | Type | Does |
|---|---|---|---|
| Scraper | `agents/scraper.py` | Script | Fetches listings from Greenhouse + Lever |
| Resume Match | `agents/resume_match.py` | Script + Claude API | Scores jobs against resume |
| Apply | `agents/apply.py` | Script + Playwright | Auto-fills + submits applications *(Phase 3)* |
| Orchestrator | `agents/orchestrator.py` | Real AI Agent | Decides what to run, handles failures *(Phase 4)* |
| Outreach | `agents/outreach.py` | TBD | Finds contacts, drafts referral emails *(Phase 4+)* |

---

## Database Models

- `Job` — scraped listing (title, company, url, source, status, match_score, match_reasoning)
- `Application` — submitted app (job_id, status, applied_at, screenshot_path)
- `UserProfile` — personal info for auto-apply (name, email, phone, linkedin_url, github_url, resume_path)

---

## Decisions Made

- [x] Scraper source → Greenhouse + Lever (public APIs, no auth needed)
- [x] User profile stored → PostgreSQL (accessible to all Celery workers)
- [x] AI model for scoring → Claude Haiku (cheap, fast, good enough for structured JSON)
- [x] Branch strategy → `dev` for all work, merge to `main` when phase is complete
- [ ] Apply Agent scope — start with Greenhouse only, or attempt Lever too? (Phase 3 decision)
- [ ] Orchestration approach — simple Celery schedule, or real LLM agent with tool use? (Phase 4 decision)

---

## Git Branch Strategy

```
main  ← stable, always works — merge from dev when a phase is complete
dev   ← active development — all day-to-day work happens here
```

Never commit directly to `main`.

---

## Learning & Explanation Requirements

> The developer is early in their career and wants to deeply understand every decision.

**Claude Code must always:**

- **Explain before building** — plain-English explanation of what's being built, why this approach, what alternatives exist
- **Annotate generously** — comments explain *why*, not just *what*
- **Call out new concepts** — if a pattern appears for the first time, explain it in 2–3 sentences inline
- **Explain tradeoffs** — when making a design decision, briefly say what was chosen and why the alternative wasn't
- **Flag complexity** — non-obvious sections get a `# NOTE:` comment
- **No assumed knowledge** — treat as early-career; don't skip over concepts

---

## Scalability Requirements

- **Stateless agents** — all state lives in PostgreSQL or Redis, never in memory
- **Horizontal scaling ready** — Celery workers can scale to N instances without code changes
- **Database indexing** — every FK and every WHERE column has an index
- **Connection pooling** — SQLAlchemy async pool (pool_size=10, max_overflow=20)
- **Pagination everywhere** — all list endpoints support `limit` + `offset`
- **Config-driven** — all thresholds, limits, intervals in `.env`
- **Retry logic** — all external calls use exponential backoff
- **Structured logging** — JSON format, always includes `agent_name`, `timestamp`, `level`
- **No N+1 queries** — use `.joinedload()` / `.selectinload()`

---

## Testing Requirements

- `pytest` + `pytest-asyncio` for async tests
- `httpx` for FastAPI endpoint tests
- `pytest-mock` for mocking external calls (Claude API, HTTP, browser)
- Separate test database (`jobhunter_test`) — never run tests against dev DB
- Coverage targets: agents 90%+, API routes 100%, models 80%+
- Run `pytest tests/ -v` before every commit

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

**Phase 4 — Orchestrator** — mostly complete, one UI item pending

### Where we left off (end of session)
- Phase 4 Orchestrator is built and live-tested (281 passing tests)
- Real Anthropic tool-use agent loop with 6 tools: check_db_state, scrape_jobs, score_jobs, auto_review_jobs, get_reviewed_jobs, request_apply_approval
- Human-in-the-loop approval gate: agent pauses at `request_apply_approval`, waits for POST /approve/{id}
- Session state stored in `orchestrator_sessions` DB table (survives server restarts)
- API: POST /run, GET /status/{id} (poll every 2s), POST /approve/{id}, GET /history
- Frontend: OrchestratorPage.jsx with 4 states (idle → running → waiting → done)
- Mode selector: "Fresh Scan" (full pipeline) vs "Use Reviewed" (existing reviewed jobs only)
- Handoff mode: fills forms in visible browser, pauses 5min for user to submit manually
- Job dismissal in approval panel: X button removes individual jobs before approving
- max_apply cap: limits how many jobs get auto-reviewed and sent to approval (default 5)
- Dry run mode: all tools return mock data, safe for testing the full loop

### Pending — finish next session
1. **Add `max_apply` number input to OrchestratorPage.jsx** (the last in-progress task)
   - `orchMaxApply` state was added to `App.jsx` and the prop is passed to `OrchestratorPage`
   - Still need to: add `maxApply`/`setMaxApply` to `OrchestratorPage` props destructuring
   - Add a number input (1–10) in the idle state UI, between mode selector and goal textarea
   - Pass `maxApply` in the `startOrchestrator(goal, dryRun, mode, handoff, maxApply)` call in `handleStart`
   - Reset to 5 in `handleNewSession`
   - Run `pytest tests/ -v` to confirm 281 tests still pass, then commit

---

## Phase History

### Phase 4 — Orchestrator ✅ COMPLETE (one UI item still pending — see above)

- `backend/agents/orchestrator_logic.py` — Pure functions: `OrchestratorConfig`, `OrchestratorResult`, `build_tool_definitions` (6 tools), `build_system_prompt`, `parse_tool_calls`, `build_tool_result_message`
- `backend/agents/orchestrator.py` — Agent loop: `run()`, `resume()`, `_run_loop()`, `_execute_tool()`, `ApprovalGateTriggered` exception, `_get_db_state()`, `_get_reviewed_jobs()`, `_auto_review_jobs()`
- `backend/models/orchestrator_session.py` — `OrchestratorSession` model, `SessionStatus` enum (running/waiting_for_approval/complete/failed)
- `backend/api/routes/orchestrator.py` — POST /run, GET /status/{id}, POST /approve/{id}, GET /history
- `backend/core/database.py` — Added `get_db_context()` async context manager for use outside FastAPI routes
- `backend/core/config.py` — Settings: `orchestrator_model`, `orchestrator_max_turns`, `orchestrator_max_tokens`, `orchestrator_dry_run`, `apply_handoff`, `apply_handoff_wait_seconds`
- `frontend/src/pages/OrchestratorPage.jsx` — 4-state UI: idle/running/waiting/done, live reasoning log, approval panel with job dismissal, mode selector, handoff + dry run toggles
- `frontend/src/App.jsx` — Orchestrator state lifted here (orchSessionId, orchSessionData, orchDryRun, orchMode, orchHandoff, orchMaxApply) so it survives tab navigation
- **281 total passing tests**

**Key design decisions:**
- Functional core / imperative shell pattern (same as all other agents) — `orchestrator_logic.py` zero I/O
- `ApprovalGateTriggered` exception exits the loop cleanly on approval gate — no threading flags through layers
- Two-phase loop: `run()` stops at approval gate, `resume()` runs apply after human approves
- Session persisted to DB immediately — approval gate survives server restarts
- `auto_review_jobs` tool uses subquery pattern for ORDER BY + LIMIT (PostgreSQL doesn't support LIMIT in plain UPDATE)
- Dry run mode: all 6 tools return plausible mock data, safe for UI testing
- `asyncio.to_thread()` wraps synchronous Anthropic SDK calls (same pattern as resume_match.py)
- In-memory `_sessions` dict caches active state for fast polling; DB is source of truth
- Handoff mode: sets headless=False, fills form, sleeps `handoff_wait_seconds`, returns SUBMITTED (user submits manually during the sleep window)
- Mode "fresh_scan" follows adaptive PATH A/B/C workflow; "use_reviewed" skips scrape/score entirely
- System prompt uses "call each tool at most ONCE" + explicit PATH A/B/C to prevent the agent looping

---

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

### Phase 3C — Apply Agent ✅ COMPLETE

- `backend/agents/apply_logic.py` — Pure functions: `ApplyConfig`, `ApplyResult`, `split_full_name`, `get_screenshots_dir`, `screenshot_filename`, `build_optional_field_map`
- `backend/agents/apply.py` — Orchestration: `load_profile`, `fetch_reviewed_jobs`, `save_application`, `apply_greenhouse` (Playwright form filler), `run()` entry point
- `backend/core/config.py` — 4 new settings: `apply_headless`, `apply_dry_run`, `apply_min_score`, `screenshots_dir`
- `backend/.env.example` — Documented all 4 new Apply Agent env vars
- 39 new tests — 30 unit (pure function coverage) + 9 integration (mocked Playwright)
- **248 total passing tests**

**Key design decisions:**
- Functional core / imperative shell pattern (same as resume_match) — `apply_logic.py` has zero I/O, `apply.py` has all side effects
- `DRY_RUN` mode fills forms and screenshots them but never clicks submit — safe on live job boards
- min_score filtered in Python (not SQL) so `total_skipped` is observable in `ApplyResult`
- One browser, one context, one page per job — reuse browser across jobs for speed
- Each job in its own `try/except` — one broken form never stops the rest of the queue
- `applied_at` timestamp set only on `SUBMITTED` (not dry runs or failures)
- Dry run leaves `Job.status = REVIEWED` — can run for real later

**To use:**
```bash
# Dry run — fill and screenshot, never submit (safe)
python -m agents.apply --dry-run

# Apply to all reviewed jobs above APPLY_MIN_SCORE
python -m agents.apply

# Target specific jobs by UUID
python -m agents.apply --job-ids <uuid1> <uuid2>
```

---

## What's Next — Phase 5: Apply Agent improvements

Phase 4 Orchestrator is complete. Remaining work:

**Apply Agent improvements (deferred from Phase 3C):**
- DOM extraction → Claude → execute: read all form fields from the page, pass to Claude with profile, Claude returns fill instructions for every field including custom questions and dropdowns
- EEOC/demographic fields: add to UserProfile, auto-fill
- Multi-page form handling

**Orchestrator improvements:**
- Better error recovery: if scraper fails, try again with a subset of companies
- LLM-graded summaries: have Claude synthesize a session outcome narrative
- Session history UI on OrchestratorPage (list of past sessions with outcomes)

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
APPLY_HEADLESS=false python -m agents.apply --dry-run   # fill forms visually, never submit
python -m agents.apply --dry-run                         # same, headless
python -m agents.apply                                   # real submissions (reviewed jobs above min_score)
python -m agents.apply --job-ids <uuid1> <uuid2>         # target specific jobs

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
│   │   ├── resume_match_logic.py   # Pure scoring functions
│   │   ├── apply.py                # Playwright form-filler orchestration
│   │   ├── apply_logic.py          # Pure apply functions (name split, screenshots, etc.)
│   │   ├── orchestrator.py         # Orchestrator agent loop (tool-use, approval gate)
│   │   └── orchestrator_logic.py   # Pure functions: tool defs, prompt building, response parsing
│   ├── api/
│   │   ├── main.py                 # FastAPI app, CORS, routers
│   │   └── routes/
│   │       ├── jobs.py             # GET /jobs, PATCH /jobs/{id}, DELETE /jobs
│   │       ├── profile.py          # GET /profile, PUT /profile
│   │       ├── pipeline.py         # POST /pipeline/run, GET /pipeline/status
│   │       └── orchestrator.py     # POST /run, GET /status/{id}, POST /approve/{id}, GET /history
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
│   │   ├── user_profile.py         # Resume path, personal info for Apply Agent
│   │   └── orchestrator_session.py # Orchestrator session (goal, steps, status, pending_job_ids)
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
│       └── api/client.js           # getJobs, updateJobStatus, clearAllJobs, runPipeline, getPipelineStatus, getProfile, updateProfile, startOrchestrator, getOrchestratorStatus, approveOrchestrator, getOrchestratorHistory
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
- [x] Apply Agent scope → Greenhouse only for v1; iframe-embedded forms handled
- [x] Apply Agent form-filling → hardcoded required fields for now; LLM form-filling deferred to post-Phase-4
- [x] Orchestration approach → raw Anthropic tool-use (not LangChain) — fewer dependencies, easier to understand, full control over the loop

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

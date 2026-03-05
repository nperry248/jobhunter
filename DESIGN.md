# JobHunter AI — System Design Document

> Living document. Update this when architecture changes, new phases ship, or design decisions are revisited.
> Last updated: Phase 4 (Orchestrator) complete — 281 passing tests.

---

## Table of Contents

1. [Purpose & Vision](#1-purpose--vision)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Tech Stack & Decisions](#3-tech-stack--decisions)
4. [Infrastructure](#4-infrastructure)
5. [Database Design](#5-database-design)
6. [Backend: Agents](#6-backend-agents)
7. [Backend: API](#7-backend-api)
8. [Backend: Workers (Celery)](#8-backend-workers-celery)
9. [Frontend](#9-frontend)
10. [Phase 4: Orchestrator (In Progress)](#10-phase-4-orchestrator-in-progress)
11. [Testing Strategy](#11-testing-strategy)
12. [Observability & Evals](#12-observability--evals)
13. [Security & Safety](#13-security--safety)
14. [Future Roadmap](#14-future-roadmap)

---

## 1. Purpose & Vision

JobHunter AI automates the SWE job search pipeline end-to-end:

```
Scrape listings → Score against resume → Surface matches → Apply to best fits
```

The near-term goal is a human-supervised autonomous system: it does the mechanical work (finding, scoring, filling applications), while the human retains final say on what gets submitted.

The long-term goal is a fully autonomous multi-agent system where an LLM orchestrator decides strategy, delegates to specialized agents, observes outcomes, and adapts — with human oversight as a configurable dial rather than a hard requirement.

### What it is NOT (yet)
- Not a real AI agent in the LangChain/agentic sense until Phase 4
- The current "agents" (scraper, resume_match, apply) are well-structured scripts with single responsibilities — they follow fixed sequences, make no decisions, and will become the *tools* a real agent uses in Phase 4

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        React Dashboard                          │
│         (Jobs, Settings, Orchestrator Panel)                    │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP (REST)
┌────────────────────────────▼────────────────────────────────────┐
│                       FastAPI Backend                           │
│    /jobs   /profile   /pipeline   /orchestrator                 │
└──────┬───────────────────────────────────┬───────────────────── ┘
       │                                   │
┌──────▼──────────┐              ┌─────────▼─────────┐
│   Agents        │              │   Celery Workers  │
│  scraper.py     │              │   (scheduled or   │
│  resume_match   │              │    on-demand)     │
│  apply.py       │              └─────────┬─────────┘
│  orchestrator   │                        │
└──────┬──────────┘                        │
       │                                   │
┌──────▼───────────────────────────────────▼─────────┐
│                   PostgreSQL                        │
│     jobs | applications | user_profile             │
│     orchestrator_sessions (Phase 4)                │
└─────────────────────────────────────────────────────┘
       │
┌──────▼──────────┐
│     Redis        │
│  (Celery broker  │
│   + backend)     │
└──────────────────┘
```

**Data flow for a typical run:**
1. Scraper fetches raw listings from Greenhouse/Lever APIs → writes to `jobs` table
2. Resume Match agent reads new jobs → calls Claude Haiku → writes `match_score` + `match_reasoning` back to each job
3. User reviews scored jobs in the dashboard → marks promising ones as `reviewed`
4. Apply Agent reads `reviewed` jobs → Playwright fills and submits applications → writes to `applications` table
5. (Phase 4) Orchestrator manages steps 1–4 autonomously via Claude tool-use, pausing at step 4 for human approval

---

## 3. Tech Stack & Decisions

### Backend

| Technology | Decision & Reasoning |
|---|---|
| **Python 3.11+** | Strong async support, dominant in AI/ML tooling, Anthropic SDK is Python-first |
| **FastAPI** | Async-native, automatic OpenAPI docs, Pydantic integration. Alternative was Flask — rejected because it's sync-first and lacks built-in validation |
| **SQLAlchemy (async)** | Industry standard ORM with mature async support. Raw SQL was considered but ORM wins for maintainability and migration support |
| **Alembic** | Pairs with SQLAlchemy for schema migrations. Essential once you have a production DB you can't just wipe |
| **PostgreSQL** | ACID-compliant, great JSON support, handles concurrent writes safely. SQLite was considered for simplicity but rejected — Celery workers need concurrent DB access |
| **Redis** | Celery broker + result backend. In-memory speed for queue operations, persisted to disk |
| **httpx** | Async HTTP client for scraping. requests is sync-only, which would block the event loop |
| **Celery** | Task queue for scheduled/background jobs. Alternative was APScheduler — rejected because Celery scales horizontally (multiple workers) without code changes |
| **Playwright** | Browser automation for form-filling. Selenium was the alternative — Playwright is faster, has better async support, and handles modern JS-heavy pages better |
| **pdfminer.six** | PDF text extraction for resume parsing. PyPDF2 was considered but pdfminer handles complex layouts better |

### AI

| Technology | Decision & Reasoning |
|---|---|
| **Anthropic Claude API** | Used for resume scoring (Phase 2) and orchestration (Phase 4). OpenAI GPT was the alternative — chose Claude for quality, tool-use capabilities, and alignment with the project's goals |
| **Claude Haiku** for scoring | ~25x cheaper than Sonnet, fast enough for structured JSON output. Scoring is high-volume (every new job) so cost matters |
| **Claude Sonnet** for orchestrator | Orchestration requires stronger multi-step reasoning than Haiku can reliably provide. The orchestrator runs infrequently (once per session) so cost is less of a concern |

### Frontend

| Technology | Decision & Reasoning |
|---|---|
| **React 18** | Component model is well-suited to a dashboard with multiple data views |
| **Vite** | Fast dev server, instant HMR. Create React App is deprecated; Vite is the modern standard |
| **Tailwind CSS** | Utility-first, fast to iterate without context-switching to CSS files. Alternative was styled-components — rejected for added complexity |

---

## 4. Infrastructure

### Docker Compose Services

```yaml
jobhunter_postgres:  port 5432, credentials: jobhunter/jobhunter/jobhunter
jobhunter_redis:     port 6379
```

Both services are defined in `docker-compose.yml` at the project root. The backend, Celery workers, and all agents connect to these via env vars from `.env`.

### Environment Configuration

All runtime config lives in `.env` (gitignored) and is read via `backend/core/config.py` using Pydantic `BaseSettings`. **Never use `os.getenv()` directly** — always go through `config.py`. This centralizes validation and makes it obvious what knobs exist.

Key config values:
- `DATABASE_URL` — PostgreSQL connection string
- `REDIS_URL` — Redis connection string
- `ANTHROPIC_API_KEY` — Claude API key
- `APPLY_HEADLESS`, `APPLY_DRY_RUN`, `APPLY_MIN_SCORE` — Apply Agent behavior
- `ORCHESTRATOR_MODEL`, `ORCHESTRATOR_MAX_TURNS`, `ORCHESTRATOR_DRY_RUN` — Phase 4

---

## 5. Database Design

### `jobs`
Core table — every scraped listing lives here.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `title` | str | Job title |
| `company` | str | Company name |
| `url` | str | Application URL (unique constraint) |
| `source` | enum | `greenhouse` or `lever` |
| `status` | enum | `new → scored → reviewed → applied / ignored` |
| `match_score` | float | 0.0–1.0, set by Resume Match agent |
| `match_reasoning` | text | Claude's explanation of the score |
| `created_at`, `updated_at` | timestamp | Auto-managed |

**Status flow:**
```
new → scored (after resume match) → reviewed (human marked) → applied / ignored / failed
                                  ↑
                            (undo: scored)
```

**Indexes:** `url` (unique), `status`, `match_score`, `source`

**Tradeoff:** `match_reasoning` is free text stored in the DB rather than returned fresh from Claude each time. This means reasoning might be slightly stale if the scoring prompt changes, but avoids re-calling Claude on every page load.

### `applications`
One row per submitted application.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `job_id` | UUID | FK → jobs |
| `status` | enum | `submitted`, `failed`, `dry_run` |
| `applied_at` | timestamp | Only set on `submitted` (not dry runs) |
| `screenshot_path` | str | Path to confirmation screenshot |
| `error_message` | text | Set on `failed` |

**Tradeoff:** Screenshots are stored as file paths, not blobs. Keeps the DB lean; files live in `data/screenshots/`. The risk is files getting out of sync with DB records — acceptable for now, solvable with a cleanup job later.

### `user_profile`
Single-row table — your personal info for auto-apply. Auto-created on first `GET /profile`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `full_name`, `email`, `phone` | str | Core personal info |
| `linkedin_url`, `github_url`, `website_url` | str | Online presence |
| `resume_path` | str | Absolute path to resume PDF |
| `target_locations` | JSON str | Stored as JSON array, exposed as `list[str]` |
| `company_blocklist` | JSON str | Companies to never apply to |
| `open_to_remote`, `open_to_relocate`, `exclude_senior` | bool | Preferences |

**Tradeoff:** List fields (`target_locations`, `company_blocklist`) are stored as JSON strings rather than a separate table. A proper relational model would use junction tables, but for a single-user app the JSON approach is far simpler and fast enough.

### `orchestrator_sessions` *(Phase 4)*
One row per orchestrator run.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `goal` | text | The goal you gave the orchestrator |
| `status` | enum | `running`, `waiting_for_approval`, `complete`, `failed` |
| `steps` | JSON | Array of tool calls + results (the reasoning log) |
| `pending_job_ids` | JSON | Jobs waiting for apply approval |
| `token_usage` | int | Total tokens consumed this session |
| `result_summary` | text | Final outcome message |
| `created_at`, `updated_at` | timestamp | Auto-managed |

**Why DB, not Redis?** Sessions need to persist across server restarts and you want a queryable history. Redis is ephemeral and better suited for ephemeral queue state.

---

## 6. Backend: Agents

All agents live in `backend/agents/` and follow the **functional core / imperative shell** pattern:

- `*_logic.py` — pure functions, zero I/O, fully unit-testable in isolation
- `*.py` — orchestration, all side effects (DB reads/writes, HTTP calls, browser)

Each agent has a clean `run()` entry point callable from CLI, Celery tasks, or the Orchestrator.

---

### Scraper (`scraper.py` + `scraper_parsers.py`)

**What it does:** Fetches job listings from Greenhouse and Lever's public JSON APIs, filters for SWE roles, deduplicates against the DB, and writes new jobs.

**How it works:**
1. For each configured company, fetch the job board JSON (no auth required — these are public)
2. Parse raw JSON into `ParsedJob` dataclass via pure functions in `scraper_parsers.py`
3. Filter: title keywords, max jobs per company, senior title exclusion (if configured)
4. Upsert into DB — if URL already exists, skip. If new, insert with `status=new`

**Key decisions:**
- **Greenhouse + Lever only** — both expose public JSON APIs without authentication. LinkedIn and Indeed require auth or scraping, which is fragile and against ToS
- **`max_jobs_per_company=5`** — prevents one company from flooding your feed. Configurable
- **Title keyword filter** — uses specific phrases (`"software engineer"`, `"backend engineer"`) not broad terms (`"engineer"`) to reduce noise
- **Upsert deduplication on URL** — the job URL is the natural unique key; using it avoids re-scraping logic

---

### Resume Match Agent (`resume_match.py` + `resume_match_logic.py`)

**What it does:** Reads all `status=new` jobs, sends each to Claude Haiku with your resume text, gets back a 0.0–1.0 match score + reasoning, updates the job record.

**How it works:**
1. Load resume text from PDF via `pdfminer.six`
2. Fetch all unscored jobs from DB
3. For each job: build a scoring prompt (pure function), call Claude Haiku, parse JSON response, write score + reasoning back to DB
4. Jobs move from `status=new` → `status=scored`

**Prompt design:**
- Calibrated for early-career: explicitly ignores years-of-experience requirements
- Scores "is this worth applying to?" not "will you definitely get hired?"
- Returns structured JSON: `{"score": 0.72, "reasoning": "..."}`
- Two fallback strategies: direct JSON parse → regex extract → `(0.0, error)`

**Key decisions:**
- **Claude Haiku, not Sonnet** — scoring is high-volume (every new job). Haiku is ~25x cheaper and sufficient for structured JSON output
- **`asyncio.to_thread()`** — the Anthropic SDK is sync-only. Wrapping in `to_thread` lets it run without blocking the async event loop
- **Score clamped to 0.0–1.0** — Claude occasionally returns values slightly outside range; clamp prevents downstream issues

---

### Apply Agent (`apply.py` + `apply_logic.py`)

**What it does:** Reads all `status=reviewed` jobs above a minimum score, uses Playwright to navigate to the Greenhouse application URL, fills the form with your profile data, and optionally submits.

**How it works:**
1. Load user profile from DB
2. Fetch reviewed jobs above `APPLY_MIN_SCORE`
3. For each job: launch Playwright, navigate to URL, fill required fields, take screenshot, click submit (unless dry run)
4. Write an `Application` record with status `submitted`, `dry_run`, or `failed`
5. On `submitted`: update `Job.status = applied`

**Form-filling approach (current):**
Hardcoded selectors for Greenhouse's standard required fields:
- First name, last name, email, phone
- LinkedIn URL, GitHub URL, website
- Resume upload (PDF from `resume_path`)

**Known limitations:**
- Custom questions (e.g., "Why do you want to work here?") — not handled
- Dropdown fields — not handled
- EEOC/demographic fields — not handled
- Multi-page forms — not handled
- Lever applications — not handled yet

**Deferred improvement (post-Phase 4):** DOM extraction → Claude → execute. Read all form fields from the page, pass to Claude with user profile, Claude returns fill instructions for every field including custom questions. Deferred because we need real failure data from the Orchestrator before knowing which patterns matter most.

**Key decisions:**
- **`DRY_RUN` mode** — fills forms and screenshots but never clicks submit. Safe to run against live job boards
- **One browser, reused across jobs** — launching a browser per job is slow. One browser instance, one page per job
- **Each job in its own `try/except`** — one broken form never stops the rest of the queue
- **`applied_at` only on `SUBMITTED`** — dry runs and failures don't count as applied

---

## 7. Backend: API

All routes follow REST conventions. Base path: `/api/v1/`

### Jobs (`/jobs`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/jobs` | Paginated list. Params: `status`, `limit`, `offset`. Ordered by `match_score DESC NULLS LAST` |
| `PATCH` | `/jobs/{id}` | Update status (`reviewed`, `ignored`, `scored` for undo) |
| `DELETE` | `/jobs` | Clear all jobs (dev utility) |

Response envelope for `GET /jobs`:
```json
{"jobs": [...], "total": 84, "limit": 20, "offset": 0}
```

### Profile (`/profile`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/profile` | Fetch profile. Auto-creates empty record on first call |
| `PUT` | `/profile` | Full update of profile |

### Pipeline (`/pipeline`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/pipeline/run` | Trigger scrape + score in a FastAPI `BackgroundTask` |
| `GET` | `/pipeline/status` | Poll status: `idle`, `running`, last run results |

**Why `BackgroundTasks` and not Celery?** In dev, you don't want to run a separate Celery worker process just to hit "Run Now." `BackgroundTasks` runs in the same process. Celery Beat handles the scheduled hourly run in production.

### Orchestrator (`/orchestrator`) *(Phase 4)*

| Method | Path | Description |
|---|---|---|
| `POST` | `/orchestrator/run` | Start a session. Returns `session_id` immediately |
| `GET` | `/orchestrator/status/{id}` | Poll status + steps log |
| `POST` | `/orchestrator/approve/{id}` | Approve pending job list, resume loop |
| `GET` | `/orchestrator/history` | All past sessions |

---

## 8. Backend: Workers (Celery)

**Purpose:** Run scrape + score jobs on a schedule (hourly by default) without human intervention.

**Components:**
- `workers/celery_app.py` — Celery app instance, wired to Redis broker
- `workers/tasks.py` — `scrape_task`, `score_task`, `scrape_and_score_task`
- `workers/schedule.py` — Celery Beat fires `scrape_and_score_task` at the top of every hour

**Key config:**
- `task_acks_late=True` — task isn't acknowledged (removed from queue) until it completes. If a worker crashes mid-task, the task re-queues automatically
- `prefetch_multiplier=1` — each worker takes one task at a time. Prevents a slow task from blocking others

**Bridging async → sync:**
Celery tasks are synchronous. The agents are async. Tasks use `asyncio.run()` to bridge: `asyncio.run(scraper.run())`. This is intentional and correct — each task gets its own event loop.

**Note:** The hourly Celery Beat schedule only runs if you explicitly start the beat and worker processes. It does NOT start automatically with Docker or the API server.

---

## 9. Frontend

Single-page React app at `http://localhost:5173`.

### Pages

**JobsPage** (`/`)
- Displays all scraped jobs sorted by match score
- Score badges: color-coded (green ≥85, blue ≥70, yellow ≥55, orange ≥40, red <40)
- Status filter tabs: All / New / Scored / Reviewed / Applied / Failed / Ignored
- Expandable cards: click any card to reveal full reasoning, source platform, dates, and full URL
- Pagination (20 per page)
- Mark reviewed / ignore / undo actions
- "Run Now" button → `POST /pipeline/run` → polls status every 3s → shows result banner
- "Clear All" button with confirmation

**SettingsPage** (`/settings`)
- Personal info: name, email, phone
- Online presence: LinkedIn, GitHub, website URLs
- Resume path (absolute path to PDF)
- Preferences: remote toggle, open to relocate toggle, exclude senior roles toggle
- Target locations (comma-separated)
- Company blocklist (comma-separated)

**OrchestratorPage** (`/orchestrator`)
- Goal input, mode selector (Fresh Scan / Use Reviewed), max_apply cap (1–10)
- Handoff mode toggle (pause for manual submit) + Dry run toggle
- Live reasoning log — step-by-step tool calls stream in as the agent runs
- Approval panel — job cards with dismiss (×) button; appears when `status=waiting_for_approval`
- Session summary on completion (jobs applied, failed, token usage)

### API Client (`src/api/client.js`)

Thin wrapper over `fetch`. All API calls go through here — no scattered `fetch()` calls in components. Functions:
`getJobs`, `updateJobStatus`, `clearAllJobs`, `runPipeline`, `getPipelineStatus`, `getProfile`, `updateProfile`, `startOrchestrator`, `getOrchestratorStatus`, `approveOrchestrator`, `getOrchestratorHistory`

---

## 10. Phase 4: Orchestrator

### What Changes

The Orchestrator is the first real AI agent in the system — it uses Anthropic tool-use (function calling) to make decisions, not just execute a fixed script.

**Before Phase 4:** You manually trigger scrape, score, and apply (or wait for Celery schedule).

**Phase 4:** You give a goal ("Find and apply to 5 good SWE jobs this week"). The Orchestrator checks DB state, decides which steps to run, executes them in sequence, and pauses for your approval before applying.

### The Agent Loop

```
Goal + tools → Claude
Claude: "call check_db_state"
Your code runs check_db_state → result sent back to Claude
Claude: "call scrape_jobs"        (if DB is empty or new jobs needed)
Your code runs scraper → result sent back to Claude
Claude: "call score_jobs"
Your code runs resume_match → result sent back to Claude
Claude: "call auto_review_jobs"   (marks top-N scored jobs as reviewed)
Your code updates DB → result sent back to Claude
Claude: "call get_reviewed_jobs"
Your code fetches reviewed jobs → result sent back to Claude
Claude: "call request_apply_approval"
→ Session status = waiting_for_approval, loop pauses
→ Frontend shows job cards + Approve / Dismiss buttons
You review, dismiss anything you don't want, click Approve
→ POST /orchestrator/approve/{id} resumes the loop
→ Apply Agent runs on approved job list
Claude: "Done. Applied to 3, 1 failed. Session complete."
```

### Tools Available to Claude

| Tool | Does | Returns |
|---|---|---|
| `check_db_state` | Count jobs by status, detect soft-deleted | `{new, scored, reviewed, applied, total}` |
| `scrape_jobs` | Run scraper agent | `{total_new, total_duplicate, total_errors}` |
| `score_jobs` | Run resume match agent | `{total_scored, total_errors}` |
| `auto_review_jobs` | Mark the top-N scored jobs as reviewed | `{reviewed, job_ids}` |
| `get_reviewed_jobs` | Fetch reviewed jobs above threshold | `[{id, title, company, score}]` |
| `request_apply_approval` | **GATED** — triggers approval pause | Sets session to `waiting_for_approval` |

### Human-in-the-Loop Design

Apply is intentionally gated. When Claude calls `request_apply_approval`, instead of running the Apply Agent immediately:
1. The pending job IDs are saved to the session record
2. Session status flips to `waiting_for_approval`
3. The loop is paused
4. Frontend polls `/status`, sees `waiting_for_approval`, renders approval panel
5. User reviews job list and clicks "Approve & Apply"
6. `POST /orchestrator/approve/{id}` resumes the loop
7. Apply Agent runs with the approved job list

**Why this design?** It separates the decision-making (Claude's job) from the authorization (your job). Claude figures out what's worth applying to; you retain final say before anything gets submitted.

### Token Safety & Cost Controls

| Safeguard | Mechanism |
|---|---|
| **Max turns** | `ORCHESTRATOR_MAX_TURNS` (default: 10) — hard cap on tool calls per session. If exceeded, session fails gracefully |
| **Token tracking** | `usage` returned on every Anthropic response → accumulated in `token_usage` on the session record |
| **Budget cap** | `ORCHESTRATOR_MAX_TOKENS` — if exceeded mid-loop, stop and save state |
| **Cheap testing** | `ORCHESTRATOR_MODEL=claude-haiku-4-5-20251001` in `.env` — swap without code changes |
| **Dry run mode** | `ORCHESTRATOR_DRY_RUN=true` — loop and reasoning run, but scraper/apply never fire |

### Model Strategy

| Phase | Model | Why |
|---|---|---|
| Development / testing | Haiku | Verify loop mechanics, tool dispatch, approval gate. ~20x cheaper |
| Production | Sonnet 4.6 | Orchestration needs stronger multi-step reasoning |

One config line to swap: `ORCHESTRATOR_MODEL=claude-sonnet-4-6`

---

## 11. Testing Strategy

### Structure

```
backend/tests/
├── conftest.py          # Shared fixtures: test DB, async session, HTTP client
├── unit/                # Pure function tests — no DB, no HTTP, no Claude
└── integration/         # Full stack tests — real test DB, mocked external calls
```

### Rules

- **Separate test DB** (`jobhunter_test`) — tests never touch the dev database
- **Mock external calls** — Claude API, HTTP scrapers, Playwright browser: always mocked in tests
- **Unit tests for logic files** — `scraper_parsers.py`, `resume_match_logic.py`, `apply_logic.py` have 90%+ coverage via pure function tests
- **Integration tests for routes** — every API endpoint has at least one happy-path and one error-path test
- **Run before every commit** — `pytest tests/ -v` must be green

### Current Coverage

281 passing tests across:
- Scraper parsing and filtering logic
- Resume match scoring and prompt building
- Apply form-filling logic (name splitting, screenshot paths, field mapping)
- All API routes (jobs, profile, pipeline, orchestrator)
- Orchestrator logic (tool definitions, prompt building, response parsing)
- Celery task unit tests

---

## 12. Observability & Evals

### Structured Logging

All agents log JSON to stdout with consistent fields:
```json
{"agent_name": "scraper", "level": "info", "timestamp": "...", "message": "...", "new_jobs": 14}
```

Configured in `core/logging_config.py`. Using JSON format makes logs parseable by log aggregators (Datadog, CloudWatch, etc.) without code changes.

### What Are Evals?

An eval is an AI-specific test: run your system against known inputs, check if the outputs are correct. Because LLM outputs are probabilistic (not deterministic), you run scenarios multiple times and measure pass rate.

**Why they matter for this project:**
- When you swap Haiku → Sonnet, evals show whether decision quality improved or regressed
- When you change the orchestrator's system prompt, evals catch regressions
- Over time, session history becomes a dataset of "correct" behaviors to test against

### Current Approach (Phase 4)

We won't use a full eval framework yet — that's premature. Instead:

1. **Session logging** — every orchestrator session records the full tool call sequence + token cost in the DB. This is the raw material for future evals
2. **Scenario tests** — pytest tests that mock Claude's responses and verify the loop handles them correctly:
   - "DB has unscored jobs → Claude calls scrape before score?"
   - "Claude always pauses at apply gate, never skips it?"
   - "Claude handles scraper returning 0 jobs gracefully?"
3. **Cost tracking** — `token_usage` on every session. You can query average cost per session and detect regressions

**Future:** Once you have 50+ real sessions, patterns emerge (which tool call sequences work, which prompts confuse Claude). That's when a proper eval suite (Braintrust, LangSmith, or custom) pays off.

---

## 13. Security & Safety

### API Security
- No authentication currently — single-user local app
- CORS configured to allow `localhost:5173` only in dev
- All inputs validated via Pydantic before touching the DB

### Apply Agent Safety
- `APPLY_DRY_RUN=true` — fills forms and screenshots, never submits. Always use this when testing
- `APPLY_MIN_SCORE` threshold — prevents applying to poor matches
- `APPLY_HEADLESS=false` — watch the browser in real time while testing

### Orchestrator Safety
- Apply gate — human approval required before any submissions
- `ORCHESTRATOR_DRY_RUN=true` — run the full loop logic without any real scraping or applying
- `ORCHESTRATOR_MAX_TURNS` — hard cap prevents runaway loops and unexpected API costs
- `ORCHESTRATOR_MAX_TOKENS` — budget cap per session

### Secrets
- All secrets (API keys, DB passwords) in `.env` (gitignored)
- `.env.example` documents every required var without real values
- Never commit `.env`

---

## 14. Future Roadmap

### Phase 5: Smarter Apply Agent Form-Filling
Deferred from Phase 3C until real failure data exists. Plan:
- Extract all form fields from the DOM (labels, input types, options)
- Pass DOM snapshot + user profile to Claude
- Claude returns fill instructions for every field, including custom questions and dropdowns
- EEOC/demographic fields: add to `UserProfile`, auto-fill

### Phase 6: Outreach Agent
- Given a job listing, find relevant contacts at the company (LinkedIn, Hunter.io)
- Draft a personalized referral request email
- Human approves before sending

### Multi-source Scraping
- Add LinkedIn (requires auth — session cookie approach)
- Add Wellfound / AngelList (startup-focused)
- Abstract `scraper_parsers.py` to support pluggable sources

### Evaluation Framework
Once 50+ real orchestrator sessions exist:
- Define a golden dataset of correct tool call sequences per scenario
- Run evals on every prompt change
- Track cost per session trend over time
- Consider Braintrust or LangSmith for eval infrastructure

### Multi-user Support
Currently single-user (one `UserProfile` row). To support multiple users:
- Add `user_id` FK to `Job`, `Application`, `OrchestratorSession`
- Add auth (JWT or session-based)
- Celery workers become per-user isolated

---

*This document should be updated whenever a phase ships, a significant design decision is made, or a known limitation is resolved.*

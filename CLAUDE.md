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

**Phase 3 — User Profile + Celery Scheduling + Apply Agent** ← UP NEXT

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

### Phase 2 — Scraper Improvements ✅ COMPLETE (same session)

- Tightened `swe_title_keywords` — removed standalone `"engineer"`/`"developer"` (too broad), replaced with specific phrases like `"software engineer"`, `"backend engineer"`, `"ml engineer"` etc.
- Added `max_jobs_per_company` (default: 5) to `ScraperFilters` + `config.py`
- Restructured fetch loop to filter inline per-company — prevents first company filling entire quota
- Fixed CLI `--dry-run` bug: was using empty keywords (no filter), now falls back to `swe_title_keywords`
- 30+ companies across Greenhouse + Lever

---

## What's Next — Phase 3

See `TODO_NEXT.md` for the full breakdown. High level:

1. **User Profile API + Settings UI** — The UserProfile model exists in the DB but has no API or UI. Before the Apply Agent can work, it needs the user's name, email, LinkedIn, GitHub, etc.
2. **Celery Scheduling** — Wire scraper + resume_match to run automatically on a schedule (e.g. every hour). Right now both are run manually from the CLI.
3. **Apply Agent** — Playwright browser automation to auto-fill and submit Greenhouse applications. Start with Greenhouse (most standardized forms).

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
python -m agents.resume_match --resume /path/to/data/resumes/YourResume.pdf

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
│   │   └── routes/jobs.py          # GET /jobs, PATCH /jobs/{id}
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
│       │   ├── JobsPage.jsx        # Main dashboard
│       │   └── SettingsPage.jsx    # User profile (stub — Phase 3)
│       └── api/client.js
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

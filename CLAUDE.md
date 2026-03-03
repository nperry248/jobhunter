# CLAUDE.md — JobHunter AI

> This file is read by Claude Code at the start of every session.
> Keep it updated as the project evolves.

---

## Project Summary

JobHunter AI is a multi-agent Python system that autonomously scrapes, filters, and applies to SWE jobs (internships + new grad). It has a React dashboard for tracking job statuses. Each agent has a single responsibility and communicates via Redis task queue and a shared PostgreSQL database.

---

## Current Phase

**Phase 1 — Foundation + Scraper Agent**

What's done: [ nothing yet — first session ]
What's in progress: [ project scaffold ]
What's next: Scraper Agent → LinkedIn/Indeed job pulling

---

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy, Alembic
- **Agents:** LangChain (tool use + orchestration)
- **Browser automation:** Playwright (async)
- **Queue:** Redis + Celery
- **Database:** PostgreSQL (local via Docker)
- **Frontend:** React 18 + Tailwind CSS + Vite
- **AI:** Anthropic Claude API (`claude-sonnet-4-20250514`)
- **Config:** Pydantic Settings, `.env` file

---

## Key Commands

```bash
# Start local services (postgres + redis)
docker-compose up -d

# Backend
cd backend
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000

# Run a specific agent manually
python -m agents.scraper --dry-run

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
```

---

## Coding Conventions

- **Python:** follow PEP 8, use type hints everywhere, docstrings on all public functions
- **Async:** use `async/await` throughout backend — FastAPI and Playwright are both async
- **Agents:** each agent lives in `backend/agents/` as its own file, with a clear `run()` entry point
- **API routes:** one file per resource in `backend/api/routes/`
- **No hardcoding:** all secrets, URLs, thresholds go in `.env` and are loaded via `core/config.py`
- **Error handling:** all agent actions must catch exceptions, log them, and continue — never crash silently
- **Commits:** format `type(scope): description` — e.g. `feat(scraper): add LinkedIn job parser`

---

## Environment Variables (see .env.example)

```
ANTHROPIC_API_KEY=
DATABASE_URL=postgresql://jobhunter:jobhunter@localhost:5432/jobhunter
REDIS_URL=redis://localhost:6379/0
```

---

## Agent Responsibilities (do not mix these)

| Agent | File | Does |
|---|---|---|
| Orchestrator | `agents/orchestrator.py` | Triggers agents, manages workflow state |
| Scraper | `agents/scraper.py` | Finds job listings from boards |
| Resume Match | `agents/resume_match.py` | Scores jobs against user resume |
| Apply | `agents/apply.py` | Auto-fills and submits applications |
| Outreach | `agents/outreach.py` | Finds contacts, drafts referral emails |

---

## Database Models (core)

- `Job` — scraped listing (title, company, url, source, status, match_score)
- `Application` — submitted app (job_id, status, applied_at, screenshot_path)
- `UserProfile` — your info used to fill applications (name, email, resume_path, etc.)

---

## Current Blockers / Decisions Needed

- [ ] Confirm: start scraping LinkedIn first or Indeed first?
- [ ] Confirm: use LangChain or CrewAI for agent orchestration?
- [ ] Confirm: user profile stored as JSON file or in DB?

---

## Git Branch Strategy

```
main          ← stable, always works
dev           ← integration branch
feat/...      ← feature branches (branch off dev)
fix/...       ← bug fixes
```

Never commit directly to `main`. PR from `dev` → `main` when a phase is complete.

---

## What Claude Code Should Always Do

1. Read this file before starting any task
2. Ask clarifying questions before writing code if the task is ambiguous
3. Write tests alongside new agent code, not after
4. Update the "Current Phase" section above when a milestone is hit
5. Use the folder structure in `PROJECT_BRIEF.md` — don't invent new locations
6. When adding a new dependency, add it to `requirements.txt` or `package.json` immediately

---

## Learning & Explanation Requirements

> The developer is early in their career and wants to deeply understand every decision.

**Claude Code must always:**

- **Explain before building** — before writing any non-trivial code, give a plain-English explanation of what you're about to build, why this approach was chosen, and what alternatives exist
- **Annotate generously** — every file should have comments explaining *why* the code is structured the way it is, not just *what* it does
- **Call out new concepts** — if a pattern, library, or architectural concept appears for the first time (e.g. async/await, dependency injection, message queues), explain it in 2–3 sentences inline
- **Explain tradeoffs** — when making a design decision (e.g. Redis vs in-memory queue), briefly explain what was chosen and why the alternative wasn't picked
- **Flag complexity** — if a section is non-obvious or would confuse a junior dev, add a `# NOTE:` comment explaining it
- **Summarize after each task** — end every response with a short "What we just built and why it matters" section

---

## Scalability Requirements

> This system must be designed to scale from day one — not refactored later.

**Architecture principles Claude Code must follow:**

- **Stateless agents** — agents must not store state in memory between runs; all state lives in PostgreSQL or Redis so agents can run on multiple workers
- **Horizontal scaling ready** — Celery workers must be able to scale to N instances without code changes; never assume single-process execution
- **Database indexing** — every foreign key and every column used in a WHERE clause must have an index; add these in migrations from the start
- **Connection pooling** — SQLAlchemy must use async connection pooling (AsyncEngine with pool_size, max_overflow settings); never open unbounded connections
- **Pagination everywhere** — all API list endpoints must support `limit` + `offset` pagination; never return unbounded result sets
- **Config-driven, not hardcoded** — rate limits, concurrency settings, retry counts, scrape intervals all live in `.env` and Pydantic Settings
- **Retry logic** — all external calls (scraping, API calls, browser automation) must use exponential backoff with a max retry count
- **Structured logging** — use Python's `logging` module with JSON-formatted output; every log entry must include `agent_name`, `job_id` (if applicable), `timestamp`, and `level`
- **No N+1 queries** — use SQLAlchemy `.joinedload()` or `.selectinload()` for related data; never query in a loop
- **Future-proof models** — DB models should include `created_at`, `updated_at` timestamps on every table, and soft deletes (`deleted_at`) where relevant

---

## Testing Requirements

> Tests are not optional. Every agent and API route must be tested before moving to the next feature.

**Testing stack:**
- `pytest` + `pytest-asyncio` for async tests
- `httpx` for testing FastAPI endpoints
- `pytest-mock` / `unittest.mock` for mocking external calls (scrapers, browser, Claude API)
- `factory-boy` for generating test fixtures
- `pytest-cov` for coverage reports

**Coverage targets:**
- Agents: 90%+ coverage
- API routes: 100% coverage
- Models/DB layer: 80%+ coverage

**Required test types per feature:**

| Feature | Required Tests |
|---|---|
| Scraper Agent | Unit: parser, deduplication, filter logic. Integration: mock HTTP → DB write |
| Resume Match Agent | Unit: scoring logic, edge cases (empty resume, no skills match) |
| Apply Agent | Unit: form-fill logic. Integration: Playwright mock browser flow |
| API Routes | Full happy path + error cases (404, 422, 500) for every endpoint |
| DB Models | Constraint tests: unique violations, null violations, cascade deletes |

**Test file structure:**
```
backend/tests/
├── conftest.py          # shared fixtures (test DB, mock clients)
├── unit/
│   ├── test_scraper.py
│   ├── test_resume_match.py
│   └── test_apply.py
├── integration/
│   ├── test_scraper_pipeline.py
│   └── test_api_jobs.py
└── e2e/
    └── test_full_workflow.py   # Phase 3+
```

**Rules:**
- Tests use a separate test database — never run tests against the dev DB
- All external HTTP calls must be mocked — tests must work offline
- Each test must have a docstring explaining what it's verifying and why
- Run `pytest --cov=. --cov-report=term-missing` before every commit

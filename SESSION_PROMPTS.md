# Session 1 — Project Scaffold

## Paste this as your first message to Claude Code

---

Read CLAUDE.md and PROJECT_BRIEF.md in full before doing anything.

We are starting Phase 1 of JobHunter AI. The goal of this session is to scaffold the entire project structure so we have a working skeleton before writing any agent logic.

**Important:** I am still learning, so before each step below, give me a plain-English explanation of what you're about to build and why we're making the technical choices we are. Don't just write code — teach me what's happening.

Please do the following in order:

1. **Create the full folder structure** from PROJECT_BRIEF.md — all directories and empty placeholder files (use `# TODO` stubs, not real code yet). Explain the purpose of each top-level folder.

2. **Set up docker-compose.yml** with PostgreSQL and Redis services. Explain what Docker Compose is doing and why we run these as containers instead of installing them directly.

3. **Create .env.example** with all required environment variables (no real values). Explain why we use .env files and why they must never be committed to git.

4. **Set up the FastAPI app** in `backend/api/main.py` — app instance, CORS config, and a `/health` endpoint. Explain what CORS is and why it matters for our React frontend.

5. **Set up SQLAlchemy** in `backend/core/database.py` with async engine, connection pooling (pool_size=10, max_overflow=20), and session factory. Explain what an ORM is, why we use async, and what connection pooling does.

6. **Create the three core models** (Job, Application, UserProfile) with proper types, relationships, indexes on all foreign keys and query columns, and `created_at`/`updated_at` timestamps. Explain each field choice.

7. **Set up Alembic** and generate the first migration. Explain what database migrations are and why we don't just recreate the DB each time.

8. **Set up the test infrastructure** in `backend/tests/`: conftest.py with a test database fixture, one example unit test, and one example API test using httpx. Explain the difference between unit and integration tests. Confirm tests pass with `pytest`.

9. **Create `backend/requirements.txt`** with all dependencies pinned to exact versions.

10. **Scaffold the React frontend** with Vite — basic layout with a sidebar nav (Jobs, Applications, Settings) and an empty main content area. Explain why we use Vite over Create React App.

11. **Verify everything works end-to-end**: docker-compose up, backend starts, frontend starts, /health returns 200, pytest passes with no errors.

After each step, tell me: what you built, why you made the key decisions you did, and what I should understand before we move on.

Do NOT start the Scraper Agent yet — that is Session 2.

---

## For Session 2 (Scraper Agent), use this prompt:

Read CLAUDE.md. We are building the Scraper Agent (Phase 1, step 2). I want to understand everything we build, so explain your decisions before writing code.

The scraper agent should:
- Accept filters: job_type (internship/new_grad), keywords (list), location, company_blocklist
- Use exponential backoff retry logic on all HTTP requests (explain what this is before implementing)
- Parse each listing into the Job model and upsert into PostgreSQL (no duplicates)
- Use structured JSON logging with agent_name, job_id, timestamp on every log line
- Have a `--dry-run` flag that prints results without saving

Before writing any code:
1. Recommend which job source to start with and why (explain the tradeoffs between LinkedIn scraping vs public APIs vs RSS feeds)
2. Explain how the Celery task queue will eventually run this agent, even though we're not wiring that up yet
3. Wait for my confirmation before proceeding

Tests to write alongside the code (not after):
- `tests/unit/test_scraper.py`: test the HTML/JSON parser, test deduplication logic, test each filter type
- `tests/integration/test_scraper_pipeline.py`: mock the HTTP response, run the full scraper, assert the Job row was written to the test DB correctly
- Each test must have a docstring. Run `pytest --cov=agents/scraper.py` and confirm 90%+ coverage before calling this step done.

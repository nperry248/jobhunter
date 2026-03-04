# TODO_NEXT.md — Phase 3 Plan

> What we're building next, why, and in what order.
> Update this as decisions are made or scope changes.

---

## Where We Are

Two phases done. We can:
- Scrape 50 SWE-relevant jobs from 30+ companies with per-company diversity
- Score each job against a resume using Claude Haiku (0–100 match score)
- View, filter, review, and ignore jobs in a React dashboard

What we cannot do yet:
- Run any of this automatically (still manual CLI commands)
- Store the user's personal info in a way the system can use it
- Actually apply to a job

---

## Phase 3 — Three Parts

### Part A: User Profile API + Settings UI

**Why this comes first:**
The Apply Agent needs your name, email, phone, LinkedIn, GitHub, and resume path to fill out application forms. The `UserProfile` DB model already exists — we just need the API and UI to populate it.

**Backend:**
- `backend/api/routes/profile.py`
  - `GET /api/v1/profile` — return the user's profile (create empty one if none exists)
  - `PUT /api/v1/profile` — upsert all profile fields
- Wire router into `api/main.py`
- Tests: `tests/integration/test_api_profile.py`

**Frontend:**
- `frontend/src/pages/SettingsPage.jsx` — currently a stub, build it out
- Form fields: name, email, phone, LinkedIn URL, GitHub URL, resume path
- Save button → `PUT /api/v1/profile`
- Show success/error state

---

### Part B: Celery Scheduling

**Why:**
Right now you have to `ssh` in and run `python -m agents.scraper` manually. A real system runs on a schedule — scrape every hour, score immediately after. Celery is already in `requirements.txt` and Redis is already running.

**What to build:**
- `backend/workers/celery_app.py` — Celery app wired to Redis
- `backend/workers/tasks.py`
  - `scrape_task()` — wraps `agents.scraper.run()` as a Celery task
  - `score_task()` — wraps `agents.resume_match.run()` as a Celery task
  - `scrape_and_score_task()` — runs both in sequence (the normal pipeline)
- `backend/workers/schedule.py` — Celery Beat schedule (e.g. scrape + score every hour)

**How to run:**
```bash
# Worker (executes tasks)
celery -A workers.celery_app worker --loglevel=info

# Scheduler (triggers tasks on schedule)
celery -A workers.celery_app beat --loglevel=info
```

**Tests:**
- Unit tests for task logic (mock the agent run() functions)

---

### Part C: Apply Agent

**Why this is last:**
Applying requires knowing who you are (Part A) and ideally having a populated job list (Part B feeds it continuously). Also the most complex part — browser automation is inherently fragile.

**Scope for Phase 3: Greenhouse applications only**
Greenhouse has the most standardized form structure across companies. Lever and others can be added later.

**What to build:**
- `backend/agents/apply.py`
  - `run(job_ids: list[UUID] | None)` — apply to specified jobs (or all "reviewed" jobs above threshold)
  - `apply_greenhouse(job: Job, profile: UserProfile, page: Page)` — Playwright automation
  - Screenshots saved to `data/screenshots/` for audit trail
  - Status updated to `"applied"` or `"failed"` in DB

**Playwright approach:**
```python
async with async_playwright() as p:
    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.goto(job.source_url)
    # Fill name, email, resume upload, LinkedIn, GitHub
    # Submit
    # Screenshot
```

**New config vars needed:**
```
APPLY_HEADLESS=true          # run browser headless in prod, false for debugging
APPLY_MIN_SCORE=70           # only apply to jobs scoring above this threshold
SCREENSHOTS_DIR=data/screenshots
```

**Tests:**
- Unit: form-fill logic with mock Playwright page
- Integration: mock browser flow end-to-end

---

## Phase 4 Preview — Real Orchestrator Agent

This is where the scripts become actual AI agents.

Instead of running `scraper → score → apply` as a fixed pipeline, the Orchestrator will be an LLM (Claude) with tools:

```python
tools = [
    scrape_tool,        # wraps scraper.run()
    score_tool,         # wraps resume_match.run()
    apply_tool,         # wraps apply.run()
    get_jobs_tool,      # query the DB
    report_tool,        # summarize what happened
]

# Claude decides what to do, in what order, and how to handle failures
orchestrator = claude_agent(tools=tools)
orchestrator.run("Find and apply to 5 backend jobs posted in the last 24 hours")
```

The LLM will be able to:
- Decide whether to scrape first or check existing unscored jobs
- Retry failed applications with a different strategy
- Skip a company if it's erroring and move on
- Report results in plain English

**Decision to make before Phase 4:**
- Use LangChain (more abstractions, bigger ecosystem) or direct Anthropic tool-use API (simpler, fewer dependencies)?

---

## Unresolved Questions

- [ ] Apply Agent: Greenhouse only for Phase 3, or attempt Lever too?
- [ ] Apply Agent: headless by default, or show the browser during testing?
- [ ] Celery: do we want a "run now" button in the UI to trigger a scrape manually (via Celery task)?
- [ ] Do we want email/Slack notifications when new high-scoring jobs are found?

---

## Current Test Count: 169 passing

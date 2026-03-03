# JobHunter AI — Project Brief

## What It Is

A multi-agent AI system that autonomously hunts, filters, applies to, and networks for software engineering jobs on your behalf. You set your preferences and filters once; the system does the rest — scraping listings, matching them against your resume, auto-applying, tracking statuses in a dashboard, and eventually finding referral connections at target companies.

## The Problem It Solves

Job hunting for SWE roles (internships / new grad) is repetitive, time-consuming, and unstrategic. Most people manually scroll job boards, copy-paste resumes, and spray-apply with no tracking or intelligence. This system makes the process autonomous, personalized, and measurable.

## Target User (v1)

You. One user. Local machine. This is not a SaaS product yet — it's a personal power tool.

---

## Agent Architecture (Multi-Agent System)

Each agent has one job. They communicate via a shared task queue (Redis) and shared database (PostgreSQL).

```
┌─────────────────────────────────────────────────────┐
│                   Orchestrator Agent                │
│  Reads user config, triggers agents, tracks state   │
└────────┬──────────────┬────────────────┬────────────┘
         │              │                │
         ▼              ▼                ▼
  ┌─────────────┐ ┌──────────────┐ ┌───────────────┐
  │ Scraper     │ │ Resume Match │ │ Outreach      │
  │ Agent       │ │ Agent        │ │ Agent         │
  │             │ │              │ │               │
  │ Finds jobs  │ │ Scores jobs  │ │ Finds people  │
  │ from boards │ │ against your │ │ at companies, │
  │ + filters   │ │ resume/prefs │ │ drafts emails │
  └──────┬──────┘ └──────┬───────┘ └───────┬───────┘
         │               │                 │
         └───────────────▼─────────────────┘
                  ┌──────────────┐
                  │ Apply Agent  │
                  │              │
                  │ Auto-fills + │
                  │ submits apps │
                  └──────┬───────┘
                         │
                  ┌──────▼───────┐
                  │  Dashboard   │
                  │  (React UI)  │
                  │              │
                  │ Track jobs,  │
                  │ statuses,    │
                  │ analytics    │
                  └──────────────┘
```

---

## Build Phases (Milestone Plan)

### Phase 1 — Foundation + Scraper Agent (MVP)
**Goal:** Scrape jobs, store them, view them in a basic UI.
- Project scaffold (FastAPI + React + PostgreSQL + Redis)
- Scraper Agent: pull from LinkedIn, Indeed, Greenhouse, Lever
- Filters: job type (internship / new grad), location, keywords, company blocklist
- Dashboard: view scraped jobs, mark as reviewed/ignored
- Git discipline: one feature per branch, PR to main

### Phase 2 — Resume Match Agent
**Goal:** Stop seeing irrelevant jobs.
- Upload your resume (PDF parser)
- Resume Match Agent scores each job 0–100 against your resume skills
- Auto-filter: only surface jobs above a threshold
- Dashboard: show match score per job, reasoning summary

### Phase 3 — Apply Agent
**Goal:** Auto-submit applications.
- Playwright browser automation
- Handle common ATS systems: Greenhouse, Lever, Workday, Ashby
- Fill forms using your profile data (stored locally, never sent anywhere)
- Screenshot + log every application attempt
- Dashboard: track applied / pending / failed statuses

### Phase 4 — Outreach Agent
**Goal:** Get referrals before applying cold.
- For each target company, find employees on LinkedIn
- Prioritize: same school, mutual connections, SWE roles
- Draft personalized outreach emails using Claude API
- Dashboard: track outreach status per company

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Agent logic | Python 3.11+ | Best AI/agent ecosystem |
| Agent framework | LangChain or CrewAI | Orchestration, tool use, memory |
| Browser automation | Playwright | Reliable, async, handles modern SPAs |
| Backend API | FastAPI | Async, fast, auto-docs at /docs |
| Task queue | Redis + Celery | Background agents, job scheduling |
| Database | PostgreSQL | Structured job tracking, queryable |
| ORM | SQLAlchemy + Alembic | Type-safe queries + migrations |
| Frontend | React + Tailwind CSS | Component-based dashboard |
| AI | Anthropic Claude API | Matching, drafting, reasoning |
| Config | Pydantic Settings (.env) | Type-safe config, secrets management |

---

## Folder Structure (Target)

```
jobhunter/
├── backend/
│   ├── agents/
│   │   ├── orchestrator.py
│   │   ├── scraper.py
│   │   ├── resume_match.py
│   │   ├── apply.py
│   │   └── outreach.py
│   ├── api/
│   │   ├── routes/
│   │   │   ├── jobs.py
│   │   │   ├── applications.py
│   │   │   └── config.py
│   │   └── main.py
│   ├── core/
│   │   ├── config.py          # Pydantic settings
│   │   ├── database.py        # SQLAlchemy setup
│   │   └── queue.py           # Redis/Celery setup
│   ├── models/
│   │   ├── job.py
│   │   ├── application.py
│   │   └── user_profile.py
│   ├── services/
│   │   └── resume_parser.py
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   └── api/               # API client
│   └── package.json
├── docker-compose.yml         # PostgreSQL + Redis local
├── .env.example
├── CLAUDE.md                  ← Claude Code reads this every session
├── PROJECT_BRIEF.md           ← This file
└── README.md
```

---

## Guiding Principles

1. **One agent, one job** — never mix concerns across agents
2. **Everything is logged** — every agent action writes to DB with timestamps, structured JSON format
3. **Fail gracefully** — if a site blocks scraping, skip and log, don't crash; use exponential backoff retries
4. **Config over code** — filters, thresholds, retry counts, concurrency limits go in `.env`, never hardcoded
5. **Test as you go** — every agent gets unit + integration tests before the next phase begins; 90%+ coverage
6. **Git discipline** — branch per feature, descriptive commits, never commit to main directly
7. **Explain everything** — every non-trivial decision gets a comment or explanation; code should teach as it runs
8. **Scale from day one** — stateless agents, connection pooling, pagination, indexed queries; no shortcuts that require rewrites later

---

## Scalability Design Decisions

These are locked-in decisions made upfront to avoid expensive refactors later:

**Why stateless agents?**
If agents store state in memory, you can only ever run one instance. Stateless agents (all state in DB/Redis) means you can run 10 scrapers in parallel just by spinning up more Celery workers — no code changes needed.

**Why Redis + Celery instead of just running agents directly?**
Direct execution is fine for one job. A queue means agents can run on a schedule, retry failed jobs automatically, run concurrently, and won't block your API server. It's the difference between a script and a system.

**Why PostgreSQL instead of SQLite?**
SQLite locks the whole file on writes — fine for one user, broken under concurrent agents. PostgreSQL handles concurrent reads/writes natively and will scale to a multi-user product if this ever becomes one.

**Why connection pooling?**
Each agent run opening a new DB connection is slow and hits PostgreSQL's connection limit fast. A pool keeps N connections warm and reuses them — critical when Celery is running many workers.

---

## Testing Philosophy

Testing isn't just about catching bugs — it's how you *know* a system this complex is actually working correctly end-to-end.

**The three layers:**

- **Unit tests** — test one function in isolation, mock everything external. Fast, run on every save.
- **Integration tests** — test that two components work together (e.g. scraper output → DB write). Run before every commit.
- **E2E tests** — test a full workflow (scrape → match → apply). Run before merging to main.

**Why mock external calls in tests?**
The internet is unreliable. LinkedIn might be down, rate-limit you, or change their HTML. Tests that depend on the real internet are flaky and untestable in CI. Mocking means tests are fast, deterministic, and work offline.

**Coverage targets by layer:**
- Agents: 90%+ (they're the core logic — must be airtight)
- API routes: 100% (every endpoint, every error case)
- DB models: 80%+ (constraints, relationships, cascade behavior)

---

## Success Metric for v1

> Run the system on a Monday morning, come back Tuesday, and have 20+ pre-screened, relevant jobs in your dashboard — some already applied to.

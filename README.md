# JobHunter AI

A multi-agent system that autonomously scrapes, filters, and scores software engineering job listings — then surfaces the best matches in a clean React dashboard.

Built for the modern SWE job hunt, where applying to 100+ roles is the norm and manually sorting through job boards is a waste of time.

---

## What It Does

1. **Scrapes** job listings from Greenhouse and Lever boards across 30+ top tech companies
2. **Filters** to SWE-relevant roles only (backend, frontend, ML, data, mobile, etc.) — no sales engineers, no AV engineers, no account executives
3. **Scores** each job against your resume using Claude AI (Haiku) — rates skill overlap, project relevance, and domain fit on a 0–100 scale
4. **Surfaces** the best matches in a React dashboard where you can review, ignore, or undo actions

---

## Dashboard

![Dashboard showing scored job listings across companies like Stripe, Airbnb, Coinbase](assets/earlyDashboard.png)

> Jobs ordered by match score. Color-coded badges: green (85+), blue (70+), yellow (55+), orange (40+), red (<40). Claude's one-sentence reasoning shown under each title.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      React Dashboard                     │
│         (Vite + Tailwind — localhost:5173)               │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP (REST)
┌────────────────────────▼────────────────────────────────┐
│                    FastAPI Backend                        │
│              GET /api/v1/jobs  PATCH /api/v1/jobs/{id}  │
└───────────┬────────────────────────────┬────────────────┘
            │                            │
┌───────────▼──────────┐   ┌────────────▼───────────────┐
│   Scraper Agent       │   │   Resume Match Agent        │
│                       │   │                             │
│  Greenhouse API ──┐   │   │  Reads resume PDF           │
│  Lever API ───────┼───┤   │  Sends to Claude Haiku      │
│                   │   │   │  Stores score + reasoning   │
│  Title filtering  │   │   │                             │
│  Per-company cap  │   │   └─────────────────────────────┘
└───────────────────┘   │
                        │
            ┌───────────▼──────────────┐
            │      PostgreSQL           │
            │  jobs / applications /    │
            │  user_profiles            │
            └──────────────────────────┘
```

### Agents

| Agent | File | Responsibility |
|---|---|---|
| Scraper | `backend/agents/scraper.py` | Fetches listings from Greenhouse + Lever, deduplicates, stores in DB |
| Resume Match | `backend/agents/resume_match.py` | Scores jobs against your resume via Claude API |
| Apply *(planned)* | `backend/agents/apply.py` | Auto-fills and submits applications via Playwright |
| Outreach *(planned)* | `backend/agents/outreach.py` | Finds referral contacts, drafts emails |
| Orchestrator *(planned)* | `backend/agents/orchestrator.py` | Schedules agents, manages workflow state via Celery |

---

## Tech Stack

**Backend**
- Python 3.11+, FastAPI, SQLAlchemy (async), Alembic
- PostgreSQL (via Docker), Redis (via Docker)
- Anthropic Claude API (`claude-haiku-4-5` for scoring — ~25× cheaper than Sonnet)
- httpx (async HTTP), pdfminer.six (PDF parsing)

**Frontend**
- React 18, Vite, Tailwind CSS

**Infrastructure**
- Docker Compose (local Postgres + Redis)
- Celery (task queue — wired for future scheduled runs)
- Pydantic Settings (type-safe config from `.env`)

---

## Scoring Logic

The Resume Match agent sends each job + your resume to Claude with a calibrated prompt:

- **Ignores years-of-experience requirements** — "5+ years required" is a wish list, not a hard gate for early-career candidates
- **Scores on skill overlap** — if you have 60%+ of the listed tech skills, score ≥ 65; 80%+ → score ≥ 80
- **Scores "worth applying?" not "will you get hired?"** — optimistic by design

Score guide:
```
85–100  Excellent match — strong skill overlap, clearly relevant background
70–84   Good match — most skills present, apply confidently
55–69   Decent match — some gaps but enough overlap to apply
40–54   Weak match — missing several core requirements
0–39    Poor match — different domain or skill set
```

---

## Setup

### Prerequisites
- Docker Desktop (for Postgres + Redis)
- Python 3.11+
- Node.js 18+
- An [Anthropic API key](https://console.anthropic.com)

### 1. Clone and configure

```bash
git clone https://github.com/your-username/job-agent.git
cd job-agent
cp .env.example .env
```

Edit `.env` and add your Anthropic API key:
```
ANTHROPIC_API_KEY=sk-ant-...
```

### 2. Start infrastructure

```bash
docker-compose up -d
```

This starts PostgreSQL (port 5432) and Redis (port 6379).

### 3. Backend

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head          # create DB tables
uvicorn api.main:app --reload --port 8000
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev                   # starts at localhost:5173
```

### 5. Add your resume

Place your resume PDF at:
```
data/resumes/YourResume.pdf
```

---

## Running the Agents

### Scrape jobs

```bash
cd backend

# Dry run — see what would be scraped without saving
python -m agents.scraper --dry-run

# Full run — fetch and store up to 50 SWE jobs across 30+ companies
python -m agents.scraper
```

### Score jobs against your resume

```bash
python -m agents.resume_match --resume /path/to/data/resumes/YourResume.pdf
```

### Both at once (typical workflow)

```bash
python -m agents.scraper
python -m agents.resume_match --resume ../data/resumes/YourResume.pdf
```

Then open `localhost:5173` to see scored results.

---

## Configuration

All settings live in `.env`. Key options:

| Variable | Default | Description |
|---|---|---|
| `SCRAPER_MAX_JOBS_PER_RUN` | `50` | Total SWE jobs to keep per scrape run |
| `SCRAPER_MAX_JOBS_PER_COMPANY` | `5` | Per-company cap (ensures diversity) |
| `MATCH_SCORE_THRESHOLD` | `70` | Minimum score for auto-apply (Phase 3) |
| `ANTHROPIC_API_KEY` | — | Required for resume scoring |

To target different companies, override `GREENHOUSE_SLUGS` or `LEVER_SLUGS` in `.env` as JSON:
```
GREENHOUSE_SLUGS={"stripe": "Stripe", "figma": "Figma", "notion": "Notion"}
```

Find a company's slug at `https://boards.greenhouse.io/{slug}` or `https://jobs.lever.co/{slug}`.

---

## Project Structure

```
job-agent/
├── backend/
│   ├── agents/
│   │   ├── scraper.py            # Greenhouse + Lever scraper
│   │   ├── scraper_parsers.py    # Pure parsing + filter logic
│   │   ├── resume_match.py       # Resume scoring orchestration
│   │   └── resume_match_logic.py # Pure scoring functions (prompt, parse, clamp)
│   ├── api/
│   │   ├── main.py               # FastAPI app, CORS, routers
│   │   └── routes/jobs.py        # GET /jobs, PATCH /jobs/{id}
│   ├── core/
│   │   ├── config.py             # Pydantic Settings (reads .env)
│   │   ├── database.py           # Async SQLAlchemy engine + session
│   │   └── logging_config.py     # JSON structured logging
│   ├── models/
│   │   ├── job.py                # Job model (title, company, score, status)
│   │   ├── application.py        # Application model
│   │   └── user_profile.py       # Resume path, personal info
│   ├── services/
│   │   └── resume_parser.py      # PDF → text, HTML stripping
│   ├── tests/
│   │   ├── unit/                 # Pure function tests (no DB, no network)
│   │   └── integration/          # API + DB tests (test database)
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/JobsPage.jsx    # Main dashboard
│       └── api/client.js         # HTTP client for FastAPI
├── data/
│   └── resumes/                  # Put your resume PDF here (gitignored)
├── docker-compose.yml
└── .env.example
```

---

## Tests

```bash
cd backend
pytest tests/ -v
pytest tests/ --cov=. --cov-report=term-missing
```

169 tests across unit + integration. Coverage targets: agents 90%+, API routes 100%.

---

## Roadmap

- [x] Phase 1 — Foundation (FastAPI, SQLAlchemy, Docker, Alembic)
- [x] Phase 2 — Scraper Agent (Greenhouse + Lever, keyword filtering, per-company diversity)
- [x] Phase 2 — Resume Match Agent (Claude scoring, PDF parsing, React dashboard)
- [ ] Phase 3 — Apply Agent (Playwright form-fill, screenshot capture)
- [ ] Phase 4 — Outreach Agent (LinkedIn contact finder, referral email drafter)
- [ ] Phase 5 — Orchestrator (Celery scheduling, full autonomous pipeline)

---

## Why This Exists

The SWE job market requires volume. Sending 5 carefully-crafted applications used to work. Now you need to send 50–100+, which means spending hours manually sorting through job boards, copy-pasting descriptions, and deciding what's worth applying to.

This system automates the sorting step — the part that doesn't require human judgment — so you can spend your time on the applications that actually matter.

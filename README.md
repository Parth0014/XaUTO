# X-Ai-Sys

Autonomous content scraping, generation, analytics, and optimization system.

This repository contains a Python backend API, a React frontend, scrapers, scheduled jobs, and several service modules that together collect, process, and generate posts and analytics for an AI-driven content system.

---

## Key Features

- Scraping of external sources (scraper module)
- Content generation and autonomous pipeline (generator, generator_service)
- NLP processing and analytics (nlp_processor, analytics)
- Scheduler for periodic jobs (scheduler/jobs.py)
- Fullstack dev setup: backend (FastAPI/Flask-style) and frontend (Vite + React)

---

## Repository Layout

- `app/` — Backend application code and modules
  - `app/main.py` — Application entrypoint
  - `app/database.py` — DB connection helpers
  - `app/routes/` — HTTP route handlers (analytics, generator, retrieval, trends, scraper)
  - `app/scheduler/jobs.py` — Scheduled background jobs
  - `app/scraper/` — Scraper implementations
  - `app/services/` — Business logic services (analytics_service, generator_service, nlp_processor)
- `frontend/` — Frontend app built with Vite + React
  - `frontend/src/` — React source files
- SQL files at repository root for schema / sample queries: `x_ai_system_*.sql`
- `requirements.txt` — Python dependencies

Refer to these files as you develop: [app/main.py](app/main.py#L1), [frontend/src/App.jsx](frontend/src/App.jsx#L1), and [app/scheduler/jobs.py](app/scheduler/jobs.py#L1).

---

## Prerequisites

- Python 3.10+ (recommended)
- Node 16+ / npm or yarn for frontend
- Optional: MongoDB (Atlas recommended)
- Optional: Redis (if scheduler or background tasks use it)

---

## Environment variables

Create a `.env` file or export environment variables for development. Common variables used by the project:

- `MONGO_URI` — MongoDB connection string
- `MONGO_DB` — Database name (default: `x_ai_system`)
- `GROQ_API_KEY` — API key for GROQ (or your chosen generation provider)
- `GROQ_API_URL` — Full inference endpoint URL for your GROQ model
- `PORT` — Backend server port (default: `8000`)
- `FRONTEND_PORT` — Frontend dev server port (default: `5173`)

Inspect [app/database.py](app/database.py#L1) and [app/services/generator_service.py](app/services/generator_service.py#L1) for the exact keys currently read by the app.

---

## Backend: Install & Run

1. Create and activate a virtual environment (recommended):

```bash
python -m venv .venv
.
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# or Command Prompt
.\.venv\Scripts\activate.bat
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the backend (example):

```bash
# If app/main.py exposes an ASGI/WSGI app, use your preferred runner, e.g. uvicorn:
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Check [app/main.py](app/main.py#L1) to confirm the callable to pass to `uvicorn` or the run command used by this project.

4. MongoDB does not require schema migrations. Indexes are created at startup.

---

## Frontend: Install & Run

1. Install dependencies:

```bash
cd frontend
npm install
```

2. Run dev server:

```bash
npm run dev
```

The frontend uses Vite; the dev server will hot-reload while you work.

### Frontend GUI flow

The current frontend is a single operations dashboard that can:

- Open Chrome in remote-debug mode first
- Start the X scraper from the browser
- Generate a batch for any topic from the browser
- Show top scraped posts, topic distribution, and sentiment distribution

The dashboard lives at [frontend/src/pages/ReviewDashboard.jsx](frontend/src/pages/ReviewDashboard.jsx#L1) and is mounted on `/`.

Set `VITE_API_BASE_URL` if your backend is not running on `http://127.0.0.1:8000`.

---

## Scheduler & Background Jobs

Scheduled jobs are defined in `app/scheduler/jobs.py`. Typical jobs include scraping runs, generation, and analytics aggregation. To run scheduler tasks locally, either:

- Start the scheduler module directly (if the project exposes a CLI) or
- Use a process manager / cron job to call the scheduler entrypoint periodically.

Inspect [app/scheduler/jobs.py](app/scheduler/jobs.py#L1) for details on job registration and triggers.

## Production Deployment (Render + Vercel)

This repo ships with a Render blueprint and a Vercel config so you can deploy immediately.

### Render

1. Create a new Render project and select the repository.
2. Render will detect [render.yaml](render.yaml) and provision two services:

- `x-ai-sys-api` (web)
- `x-ai-sys-worker` (background jobs)

3. Set the secret env vars in Render for both services (DB + GROQ + Qdrant + X API). The blueprint marks them as `sync: false`.
4. Configure `X_SEARCH_TOPICS`, `X_SEARCH_MAX_RESULTS`, and `X_SCRAPE_INTERVAL_MINUTES` to match your free-tier limits.
5. For the web service, keep `ENABLE_SCHEDULER=false`.
6. For the worker service, keep `ENABLE_SCHEDULER=true`.

Required secrets:

- `MONGO_URI`
- `GROQ_API_KEY`, `GROQ_API_URL`
- `QDRANT_URL`, `QDRANT_API_KEY`
- `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`

Optional knobs:

- `AUTOPOST_ENABLED=true` to allow autonomous posting
- `POST_MIN_SCORE`, `POST_MIN_INTERVAL_MINUTES`, `POST_DAILY_LIMIT`, `POST_MAX_PER_RUN`
- `CLEANUP_RETENTION_DAYS`, `CLEANUP_MAX_SCRAPED_POSTS`, `CLEANUP_MAX_GENERATED_POSTS`

### Vercel

1. Create a Vercel project with `frontend/` as the root directory.
2. Set `VITE_API_BASE_URL` to your Render API URL.
3. Deploy. Vercel uses [frontend/vercel.json](frontend/vercel.json) to build the Vite app.

## Current API Routes

- `GET /` — backend status page
- `GET /browser/chrome-debug` — start Chrome with remote debugging on port 9222
- `GET /scrape/x` — start the scraper
- `GET /generate/{topic}` — generate a draft for a topic
- `GET /analytics/top-posts` — top scraped posts
- `GET /analytics/topics` — topic distribution
- `GET /analytics/sentiment` — sentiment distribution
- `POST /embeddings/backfill` — embed recent scraped posts
- `GET /retrieve/similar` — retrieve semantically similar posts
- `GET /retrieve/by-id/{post_id}` — retrieve posts similar to an existing one
- `POST /trends/run` — run clustering/trend detection
- `GET /trends/latest` — most recent trend clusters
- `GET /trends/{cluster_id}/patterns` — cluster pattern summary

---

## Scraper

Scrapers live in `app/scraper/` and implement site-specific logic. If a scraper requires authentication, configure credentials via environment variables before running. For headless browser scrapers, ensure the appropriate browser binaries (Chromium) are available on your system.

---

## Services & NLP

Core application logic is encapsulated in `app/services/`:

- `analytics_service.py` — aggregates and stores analytics
- `generator_service.py` — content generation orchestration
- `nlp_processor.py` — lazy sentiment loading and topic classification helpers

The generator now uses retrieval-augmented prompting: it pulls semantically similar high-performing posts plus trend pattern signals and injects them into the generation backend (e.g. GROQ) before generating a new post.

Review service modules to understand expected inputs/outputs and how routes call them.

---

## Database

MongoDB is used for persistence. Collections are created on demand and indexes are initialized at startup. SQL schema files remain only for legacy reference.

---

## Testing

If the project includes tests, run them with `pytest` (install if needed):

```bash
pip install pytest
pytest -q
```

If there are no tests yet, consider adding unit tests for service logic in `app/services/` and route-level integration tests for `app/routes/`.

---

## Development Notes

- Keep secrets out of source control. Use `.env` and Git ignore rules.
- When adding new scrapers, include site-specific retry/backoff and politeness (rate-limiting).
- `nlp_processor.py` now lazy-loads the sentiment pipeline to avoid blocking startup.
- The app does not use SQL migrations; indexes are created at startup in MongoDB.

---

## Contributing

Contributions are welcome. Please open issues or PRs describing changes. Follow these guidelines:

- Write tests for new features or bugfixes
- Keep commits small and focused
- Document any new environment variables or external services

---

## License

Specify your license here (e.g., MIT). If you don't have one yet, add a `LICENSE` file to the repository root.

---

## Questions / Next Steps

If you'd like, I can:

- Wire a small reward-model scorer for generated posts after you start posting

Tell me which of the above you'd like me to do next.

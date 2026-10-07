# Bulk Certificate Generator

Production-quality FastAPI backend that accepts **one request with many recipients**, validates them, creates a job, generates a PDF certificate per valid recipient from a single predefined template, tracks progress, and serves individual PDFs or a ZIP of all successes.

Designed to be simple enough to explain line-by-line in an interview: readable code, no Celery/Redis, SQLite by default.

---

## Features

- `POST /jobs` – submit a bulk job (202 Accepted)
- Partial failure semantics – invalid or failed recipients do not abort the rest
- Background processing via FastAPI `BackgroundTasks`
- Live progress via `GET /jobs/{job_id}`
- List / filter / paginate items; download one PDF or a ZIP of all successes
- Configurable DB URL (SQLite default, Postgres-ready), certificate directory, max recipients
- Startup recovery for jobs left in `PROCESSING` after a crash
- Structured logging for job lifecycle events

---

## Project structure

```
app/
  main.py, config.py, database.py, models.py, schemas.py
  api/jobs.py
  services/job_service.py, certificate_generator.py, validators.py
  templates/certificate.py
tests/
  conftest.py, test_*.py
README.md, requirements.txt, .gitignore
```

---

## Setup

```bash
# Python 3.11+
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Run the API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open docs at http://127.0.0.1:8000/docs

### Run tests

```bash
pytest -v
```

Tests use a temp SQLite DB and temp certificate directory, and run job processing **synchronously** (`sync_processing=True`) so results are deterministic.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certificates.db` | SQLAlchemy URL (use `postgresql+psycopg2://...` for Postgres) |
| `CERTIFICATES_DIR` | `./certificates` | Root directory for generated PDFs |
| `MAX_RECIPIENTS` | `1000` | Max recipients per job (over → HTTP 413) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `SYNC_PROCESSING` | `false` | If true, skip BackgroundTasks (used by tests) |

Example `.env`:

```env
DATABASE_URL=sqlite:///./certificates.db
CERTIFICATES_DIR=./certificates
MAX_RECIPIENTS=1000
LOG_LEVEL=INFO
```

---

## Example curl flow

### 1. Submit a job (one invalid recipient)

```bash
curl -s -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "certificate_title": "Certificate of Completion",
    "course_name": "Backend Engineering 101",
    "issue_date": "2026-03-01",
    "recipients": [
      {"name": "Alice Smith", "email": "alice@example.com"},
      {"name": "Bob Jones", "email": "bob@example.com"},
      {"name": "", "email": "bad-email"}
    ]
  }'
```

Example response (`202 Accepted`):

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "PENDING",
  "total": 3,
  "valid": 2,
  "invalid": 1
}
```

### 2. Poll status

```bash
curl -s http://127.0.0.1:8000/jobs/<job_id>
```

Example response:

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "COMPLETED_WITH_ERRORS",
  "total": 3,
  "succeeded": 2,
  "failed": 1,
  "pending": 0,
  "progress_percent": 100.0,
  "created_at": "2026-03-01T10:00:00Z",
  "started_at": "2026-03-01T10:00:00Z",
  "finished_at": "2026-03-01T10:00:02Z"
}
```

### 3. List certificates

```bash
curl -s "http://127.0.0.1:8000/jobs/<job_id>/certificates?status=SUCCESS&limit=50&offset=0"
```

Example item:

```json
{
  "id": "11111111-2222-3333-4444-555555555555",
  "recipient_name": "Alice Smith",
  "recipient_email": "alice@example.com",
  "status": "SUCCESS",
  "error_message": null,
  "download_url": "/jobs/<job_id>/certificates/<item_id>/download"
}
```

### 4. Download one PDF

```bash
curl -OJ http://127.0.0.1:8000/jobs/<job_id>/certificates/<item_id>/download
```

### 5. Download ZIP of all successes

```bash
curl -OJ http://127.0.0.1:8000/jobs/<job_id>/download
```

### Health

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok"}
```

---

## Design decisions

### Why FastAPI?

Async-capable, excellent OpenAPI docs out of the box, first-class Pydantic v2 validation, and `BackgroundTasks` for post-response work without extra infrastructure. Ideal for a take-home that must be easy to run and demo.

### Why BackgroundTasks instead of synchronous generation?

`POST /jobs` must return quickly with a `job_id` so clients can poll. Generating hundreds of PDFs inline would block the request (timeouts, poor UX). BackgroundTasks run after the response is sent, in the same process — zero extra deps.

**Why not Celery/RQ yet?** Setup cost (Redis broker, workers, deployment) is high for a take-home. In production you would move `process_job` onto Celery/RQ + Redis for:

- Persistence across process restarts
- Retries with backoff
- Horizontal scaling of workers
- Rate limiting / concurrency controls per worker

### Why partial-failure semantics?

Bulk uploads almost always contain a few bad rows. Failing the entire batch is hostile UX. We validate each recipient independently: invalid ones become `FAILED` items with a clear `error_message`; valid ones still generate. Job ends as `COMPLETED`, `COMPLETED_WITH_ERRORS`, or `FAILED` (all failed).

### Data model

- **jobs** – one row per submission; counts and lifecycle timestamps
- **certificate_items** – one row per recipient; status, error, file path

Indexed on `job_id` and `(job_id, status)` for list/filter queries. Tables created with `create_all` on startup (Alembic is the obvious next step).

### File storage

PDFs live under `{CERTIFICATES_DIR}/{job_id}/{item_id}.pdf`. Paths use **only UUIDs** — never user input — so path traversal is impossible by construction. `CertificateGenerator` still resolves paths and asserts they stay under the configured root.

### Crash recovery

On startup, any job left in `PROCESSING` is marked `FAILED` (not re-queued). BackgroundTasks are in-process and do not survive a crash; re-queueing without idempotency could double-write. Documented trade-off — production would use a durable queue + idempotent generation.

### Security notes

- Path safety via UUID-only segments
- `MAX_RECIPIENTS` size limit (413)
- No auth in this take-home (add API keys / OAuth next)
- Email / name sanitised for display only; never used in paths

---

## Known limitations & next steps

| Limitation | Next step |
|---|---|
| In-process BackgroundTasks | Celery or RQ + Redis |
| Local filesystem | S3 / object storage + signed URLs |
| `create_all` schema | Alembic migrations |
| No auth | API keys or OAuth2 |
| No rate limiting | Per-IP / per-key limits |
| No retries on transient PDF errors | Per-item retry with backoff |
| No idempotency keys | Client-supplied key to dedupe POSTs |
| Single template | Template registry keyed by `template_id` |

---

## Interview cheat sheet

1. **How would you scale this?** Move `process_job` to Celery/RQ workers behind Redis; store PDFs in S3; run multiple API replicas + workers; put Postgres behind the API; add a CDN for downloads.

2. **What if the server crashes mid-job?** On startup we mark `PROCESSING` jobs as `FAILED`. Pending items stay `PENDING`. With Celery, unfinished tasks would be redelivered; generation should be idempotent (overwrite same `{item_id}.pdf`).

3. **How would you add retries?** Catch transient errors in `_process_one_item`, increment an `attempt_count`, re-queue with exponential backoff up to N tries, then mark `FAILED`.

4. **How would you make processing synchronous?** Call `process_job` inline in the route before returning (or set `SYNC_PROCESSING=true`). Fine for tiny jobs; blocks the HTTP worker for large ones.

5. **How would you add a second template?** Introduce `template_id` on the job; `CertificateGenerator` dispatches to `templates/<id>.py` via a registry dict. Keep path layout unchanged.

6. **Why commit after each item?** So `GET /jobs/{id}` shows live progress and a crash loses at most one item’s work, not the whole batch.

7. **Why a separate DB session in the worker?** The request session is closed when the response finishes; BackgroundTasks must open their own session (`SessionLocal()`).

8. **How do you prevent path traversal?** File paths are built only from UUIDs (`job_id` / `item_id`); user strings never enter the path; resolved path must stay under `CERTIFICATES_DIR`.

9. **How would you add auth?** API key middleware or OAuth2 bearer tokens; scope downloads to the owning account; never expose other users’ `job_id`s without checks (UUIDs help but are not auth).

10. **Why SQLite by default?** Zero-setup demos and tests. `DATABASE_URL` swaps to Postgres without code changes because SQLAlchemy abstracts the dialect.

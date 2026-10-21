# Deployment Runbook & Operational Procedures

This runbook provides step-by-step instructions for deploying, operating, monitoring, and maintaining the UK Working-Capital Intelligence & Prospect Scoring Platform across local development, staging/pilot, and production environments.

---

## 1. Environment Configuration & Secrets

The application is configured via environment variables. Ensure the following keys are provided in `.env` or injected by your container orchestrator:

| Variable | Default (Local / Staging) | Production Recommendation | Purpose |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite:///./uk_working_capital.db` | `postgresql+psycopg2://user:pass@host:5432/db` | Database connection string |
| `JWT_SECRET_KEY` | `dev_secret_change_in_production` | Strong random 256-bit token (`openssl rand -hex 32`) | API JWT signing |
| `COMPANIES_HOUSE_API_KEY`| `simulated_key` | Real Companies House API key | Companies House REST / Stream API |
| `CREDITSAFE_WEBHOOK_SECRET`| `dev_creditsafe_secret` | High-entropy shared HMAC key | Creditsafe webhook signature validation |
| `ANTHROPIC_API_KEY` | `""` (Fallback to deterministic) | Real Anthropic API key | LLM natural-language prospect explanations |
| `ENVIRONMENT` | `development` | `production` | Enables strict SSL and production hardening |

---

## 2. Local Startup & Development Runbook

### Step 1: Initialize Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### Step 2: Initialize Database and Default Accounts
```bash
python3 -c "from backend.app.core.database import init_db; init_db()"
```
This provisions default roles, initial test entities, and sets up the tamper-evident genesis audit log.

### Step 3: Run FastAPI Application
```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```
- Interactive Swagger UI: `http://localhost:8000/docs`
- Broker Prospecting Dashboard: `http://localhost:8000/`

---

## 3. Docker Compose Deployment

The repository includes a containerised setup in [`docker-compose.yml`](file:///Users/trinity/Documents/antigravity/eager-planck/docker-compose.yml):

```bash
# Build and run containers in detached mode
docker compose up --build -d

# Inspect running services
docker compose ps

# View backend logs
docker compose logs -f backend
```

Services exposed:
- `backend`: FastAPI API server on port `8000`
- `db`: PostgreSQL 15 on port `5432`

---

## 4. Health Checks & Verification Procedures

### A. System Health Endpoint
```bash
curl -X GET http://localhost:8000/api/v1/system/health
```
**Expected Response:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "scoring_model_version": "v1.0.0",
  "database": "connected",
  "timestamp": "2026-10-21T01:00:00Z"
}
```

### B. Audit Chain Integrity Verification
```bash
curl -X GET http://localhost:8000/api/v1/system/audit-verification \
  -H "Authorization: Bearer <ADMIN_JWT_TOKEN>"
```
**Expected Response:**
```json
{
  "status": "VERIFIED",
  "records_checked": 24,
  "head_hash": "a1b2c3d4...",
  "chain_intact": true
}
```

---

## 5. Dead-Letter Queue (DLQ) Operational Procedures

When invalid or malformed payloads arrive, they are preserved in `dead_letter_events`.

### Inspect Dead-Letter Queue
```bash
curl -X GET http://localhost:8000/api/v1/ingest/dead-letter-queue \
  -H "Authorization: Bearer <ADMIN_JWT_TOKEN>"
```

### Replay Dead-Letter Event
To replay a quarantined event after upstream schema resolution:
```bash
curl -X POST http://localhost:8000/api/v1/ingest/dead-letter-queue/1/replay \
  -H "Authorization: Bearer <ADMIN_JWT_TOKEN>"
```
**Expected Response:**
```json
{
  "status": "SUCCESS",
  "dead_letter_id": 1,
  "replayed_signal_id": 105,
  "message": "Dead letter event reprocessed and ingested successfully."
}
```

---

## 6. Disaster Recovery & Rollback

1. **Database Snapshot**: Execute daily snapshots of PostgreSQL volume or SQLite database file.
2. **Audit Log Verification**: Prior to restoring any backup, run the audit verification routine to confirm hash continuity.
3. **Stream Cursor Reset**: If Companies House streaming drops connections or raises HTTP 416 (timepoint expired), reset `StreamingCheckpoint.cursor_timepoint = "stream_head"` to resume live event consumption without data corruption.

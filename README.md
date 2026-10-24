# VendForge Fleet Management System

Fleet monitoring and telemetry ingestion platform for Bear & Berry automated smoothie vending machines.

---

## 🛠 Tech Stack
- **Python 3.12** & **FastAPI**
- **Pydantic v2** & **SQLAlchemy 2.0**
- **PostgreSQL 16** (Driver: `psycopg3`)
- **Docker & Docker Compose**
- **Pytest** & **HTTPX**
- Direct schema initialization: `db/init.sql` (no Alembic)

---

## 🚀 Quickstart

### Option A: Run via Docker Compose (PostgreSQL 16 + API)
```bash
docker compose up --build -d
```
API runs on `http://localhost:8000`. Health check: `http://localhost:8000/health`.

### Option B: Run Locally with Python 3.12
```powershell
Set-Location "C:\Users\HAMMAD KHAN\.gemini\antigravity\scratch\vendforge"
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🧪 Run Tests

Execute the automated test suite covering all reliability scenarios:
```powershell
Set-Location "C:\Users\HAMMAD KHAN\.gemini\antigravity\scratch\vendforge"
.\.venv\Scripts\pytest -v
```

---

## 📡 API Usage Examples

### 1. Register a Machine
```bash
curl -X POST http://localhost:8000/machines \
  -H "Content-Type: application/json" \
  -d '{
    "id": "bb-101",
    "name": "Bear & Berry Station Central",
    "customer_location": "Concourse A, Gate 4"
  }'
```
*Response*: Status is derived as `"UNKNOWN"` because the machine has never reported.

### 2. Ingest Telemetry Batch (Up to 500 Items)
```bash
curl -X POST http://localhost:8000/telemetry \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "machine_id": "bb-101",
        "ts": "2026-10-24T12:00:00Z",
        "cabinet_temp_c": 3.8,
        "cooling_status": "NORMAL",
        "operating_state": "IDLE",
        "error_codes": []
      }
    ]
  }'
```
*Response*:
```json
{
  "total": 1,
  "inserted": 1,
  "duplicate": 0,
  "rejected": 0,
  "results": [
    {
      "machine_id": "bb-101",
      "ts": "2026-10-24T12:00:00Z",
      "status": "inserted",
      "reason": null
    }
  ]
}
```

### 3. List Machines (Status Derived at Read Time)
```bash
curl -X GET http://localhost:8000/machines
```
*Machine status rules*:
- Never reported: `"UNKNOWN"`
- `now - last_received_at > 180s`: `"OFFLINE"`
- Active faults non-empty: `"ERROR"`
- Otherwise: `last_state` or `"ONLINE"`

### 4. Inspect an Individual Machine
```bash
curl -X GET http://localhost:8000/machines/bb-101
```

### 5. View Historical Telemetry (Ordered by Event Timestamp `ts DESC`)
```bash
curl -X GET "http://localhost:8000/machines/bb-101/history?limit=50"
```

---

## 🔒 Reliability Highlights
- **Idempotent Ingestion**: Duplicates on `(machine_id, ts)` return `"status": "duplicate"` and are ignored.
- **Out-of-Order Safety**: State snapshots only update if `ts > last_event_ts`.
- **Atomic Batches**: One database transaction per batch. If a mid-batch error occurs, the entire batch rolls back (HTTP 500) so the machine can retry.
- **Clock Drift Rejection**: Future timestamps $> \text{now} + 5\text{ min}$ are rejected.
- **Derived Status**: Status is strictly calculated at query time and never stored in the database.

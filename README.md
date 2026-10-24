# VendForge Fleet Management System

A high-reliability fleet monitoring and telemetry ingestion backend for Bear & Berry automated smoothie vending machines.

---

## 🛠 Tech Stack
- **Python 3.12**
- **FastAPI** (Async Web Framework)
- **Pydantic v2** (Validation & Serialization)
- **PostgreSQL 16** (Primary Datastore via `psycopg3`)
- **SQLAlchemy 2.0** (ORM & Core Query Builder)
- **Pytest** & **HTTPX** (Automated Integration Testing)
- Pure SQL schema migrations: `db/init.sql` (no Alembic)

---

## 🐘 Local Setup with Native PostgreSQL

### 1. Prerequisites
Ensure Python 3.12 and PostgreSQL 16 are installed and `psql` is available on your system `PATH`.

### 2. Configure Database and User
Create the `vendforge` application role and databases (`vendforge` for runtime, `vendforge_test` for automated tests):

```bash
# Set password for postgres superuser
export PGPASSWORD="your_postgres_password"

# Create user
psql -U postgres -h localhost -c "CREATE USER vendforge WITH PASSWORD 'vendforge_secret';"

# Create development and test databases
psql -U postgres -h localhost -c "CREATE DATABASE vendforge OWNER vendforge;"
psql -U postgres -h localhost -c "CREATE DATABASE vendforge_test OWNER vendforge;"
```

### 3. Initialize Runtime Schema
Apply the base schema to the development database:
```bash
export PGPASSWORD="vendforge_secret"
psql -U vendforge -d vendforge -h localhost -f db/init.sql
```

### 4. Setup Python Virtual Environment
```bash
# Create and activate Python 3.12 venv
python -m venv .venv

# Windows
.\.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 🧪 Running Automated Tests

The test suite runs against real PostgreSQL 16 using `vendforge_test`. `tests/conftest.py` automatically resets the `public` schema and applies `db/init.sql` on startup, then truncates tables between tests for complete test isolation:

```powershell
# Set test connection string (defaults to local PostgreSQL 16)
$env:TEST_DATABASE_URL = "postgresql+psycopg://vendforge:vendforge_secret@localhost:5432/vendforge_test"

# Run all 22 tests
.\.venv\Scripts\pytest -v
```

---

## 🚀 Running the API Server

Start the FastAPI application with Uvicorn:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://vendforge:vendforge_secret@localhost:5432/vendforge"
.\.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive API documentation (Swagger UI) is available at:
`http://localhost:8000/docs`

---

## 📡 API Usage Examples (cURL)

### 1. Create Customer
```bash
curl -X POST http://localhost:8000/customers \
  -H "Content-Type: application/json" \
  -d '{
    "id": "cust-01",
    "name": "Bear & Berry Global"
  }'
```

### 2. Create Location
```bash
curl -X POST http://localhost:8000/locations \
  -H "Content-Type: application/json" \
  -d '{
    "id": "loc-01",
    "customer_id": "cust-01",
    "name": "Downtown Central Mall",
    "address": "100 Market St, Level 2"
  }'
```

### 3. Register a Machine
```bash
curl -X POST http://localhost:8000/machines \
  -H "Content-Type: application/json" \
  -d '{
    "id": "bb-101",
    "name": "Kiosk #101",
    "location_id": "loc-01"
  }'
```
*Response*:
```json
{
  "id": "bb-101",
  "name": "Kiosk #101",
  "location_id": "loc-01",
  "location_name": "Downtown Central Mall",
  "customer_id": "cust-01",
  "customer_name": "Bear & Berry Global",
  "created_at": "2026-10-24T12:00:00Z",
  "connectivity": "UNKNOWN",
  "operating_state": null,
  "has_active_faults": false,
  "active_faults": [],
  "last_event_ts": null,
  "last_temp": null,
  "last_cooling": null,
  "last_received_at": null
}
```

### 4. Ingest Telemetry Batch (Up to 500 Items)
```bash
curl -X POST http://localhost:8000/telemetry \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "machine_id": "bb-101",
        "ts": "2026-10-24T12:01:00Z",
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
      "ts": "2026-10-24T12:01:00Z",
      "status": "inserted",
      "reason": null
    }
  ]
}
```

### 5. List Machines with Filters
```bash
# Filter by location
curl -X GET "http://localhost:8000/machines?location_id=loc-01"

# Filter by derived connectivity (ONLINE | OFFLINE | UNKNOWN)
curl -X GET "http://localhost:8000/machines?connectivity=ONLINE"
```

### 6. Inspect Machine History
```bash
curl -X GET "http://localhost:8000/machines/bb-101/history?limit=50"
```

---

## 🤖 Running the Fleet Simulator

The simulator executes an end-to-end flow with customer and location setup, normal reporting, fault generation, and burst uploads:

```powershell
.\.venv\Scripts\python scripts/simulate_fleet.py
```

---

## 🐳 Docker Deployment Notice

`docker-compose.yml` and `Dockerfile` are provided in the repository to define the containerized deployment environment (PostgreSQL 16 container mounting `db/init.sql` alongside the FastAPI container).

> [!NOTE]
> **Host Environment Status**: Docker is not installed on this development machine, so `docker-compose.yml` and `Dockerfile` are **UNVERIFIED in local live execution**.
>
> If running on a machine with Docker Desktop:
> - Ensure no local native PostgreSQL service is occupying host port `5432` to avoid port collision.
> - Execute: `docker compose up --build -d`

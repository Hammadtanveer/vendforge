# VendForge Implementation Phases

## Phase Tracker

- [x] **Phase 0: Analysis & Problem Framing**
  - Architecture breakdown, scale math, reliability requirements from assessment brief.
  - Formalized locked decisions into `PLAN.md`.

- [x] **Phase 1: Project Scaffolding & Database Schema**
  - Project directory structure, Python 3.12 virtualenv, dependencies.
  - `db/init.sql` with `machines` and `telemetry` (composite PK `(machine_id, ts)`).
  - `docker-compose.yml` and `Dockerfile`.

- [x] **Phase 2: Machine Registration & Telemetry Ingestion**
  - Registration endpoint (`POST /machines`), list (`GET /machines`), inspect (`GET /machines/{id}`).
  - Batch ingestion endpoint (`POST /telemetry`) with 500-item cap, `ts ASC` sorting, idempotency on conflict, and atomic transaction.

- [x] **Phase 3: Real PostgreSQL Test Suite & Reliability Hardening**
  - Removed SQLite in favor of real PostgreSQL 16.
  - Native `ARRAY(String)` and `pg_insert`.
  - Automated session fixture applying `db/init.sql` and per-test `TRUNCATE CASCADE` isolation.
  - Real database error rollback test under `NUMERIC(5,2)` overflow.
  - 11/11 tests passing on PostgreSQL 16.

- [x] **Phase 4: Design Documentation & Simulator**
  - `DESIGN.md` detailing architecture, scale math, reliability matrix, failure modes, and evolution.
  - `README.md` with step-by-step setup and `curl` API examples.
  - `scripts/simulate_fleet.py` for end-to-end fleet simulation.

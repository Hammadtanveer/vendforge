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

- [x] **Phase 4: Relational Customers & Locations (DONE)**
  - Normalized schema: `customers` $\rightarrow$ `locations` $\rightarrow$ `machines` with foreign key constraints.
  - Client-supplied IDs (`VARCHAR(64)`), duplicate ID returns 409.
  - `POST /customers`, `POST /locations` (validates `customer_id` or 404), `POST /machines` (validates `location_id` or 404).
  - `GET /machines` with `joinedload` (no N+1 queries) and filters: `?location_id=` and `?connectivity=` (`ONLINE`, `OFFLINE`, `UNKNOWN`, invalid returns 422).
  - Enriched `MachineResponse` with `location_id`, `location_name`, `customer_id`, and `customer_name`.
  - Updated fleet simulator with customer/location flows, UNKNOWN machine, and backdated OFFLINE demo.

- [x] **Phase 5: Status Field Split & Test Hardening**
  - Separated `connectivity` (`ONLINE`, `OFFLINE`, `UNKNOWN`) from `operating_state` in `MachineResponse`.
  - Added `has_active_faults` boolean and `active_faults` list.
  - Ensured `tests/conftest.py` drops and recreates `public` schema on session startup.
  - Strengthened rejection tests to verify zero telemetry rows persisted in PostgreSQL.
  - Added tests for offline state preservation, old-data-only reconnect, UNKNOWN on never reported, fault/connectivity orthogonality, and equal timestamp first-write-wins.

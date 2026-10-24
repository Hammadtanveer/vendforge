# Agent Instructions & Hard Rules

## Stack (do not change)
Python 3.12, FastAPI, Pydantic v2, PostgreSQL 16, SQLAlchemy 2.0, psycopg3, pytest, httpx, Docker Compose. No Alembic: use db/init.sql.

## Hard Rules
1. Analyse first, then change. Make the smallest change that works. One task per prompt.
2. Do NOT add Kafka, Redis, Celery, auth, or a frontend.
3. Telemetry ingest logic is fixed (see PLAN.md "Locked decisions"). Never replace it with "latest row = current status".
4. Machine status is DERIVED at read time. Never stored.
5. Always use machine event timestamp (ts) for ordering, never arrival time.
6. Every task ends with exact commands to verify (copy-paste ready).
7. Do not delete or rewrite files outside the task scope.
8. Keep code short, readable, commented where logic is non-obvious (reliability parts).

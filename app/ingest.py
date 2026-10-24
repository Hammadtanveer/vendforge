from datetime import datetime, timezone, timedelta
from typing import List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import update, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import Machine, Telemetry
from app.schemas import TelemetryItem, ItemIngestResult, TelemetryBatchResponse


def ingest_telemetry_batch(db: Session, items: List[TelemetryItem]) -> TelemetryBatchResponse:
    """
    Ingests a batch of telemetry items strictly following the locked decisions:
    - Items sorted by ts ASC.
    - Executed in a single transaction (if mid-batch DB error occurs, caller rolls back, returns 5xx).
    - Future timestamp (> now + 5 min) rejected per-item.
    - Unknown machine_id rejected per-item (no auto-register).
    - Idempotent: duplicates on (machine_id, ts) ignored (first write wins).
    - Cached machine state updated ONLY if incoming event ts is newer.
    - last_received_at updated on every valid received item.
    """
    now = datetime.now(timezone.utc)
    max_future_ts = now + timedelta(minutes=5)

    # Sort items by machine event timestamp (ts) ASC
    sorted_items = sorted(items, key=lambda x: x.ts)

    # Pre-fetch existing machine IDs to detect unknown machines
    machine_ids = {item.machine_id for item in sorted_items}
    existing_machines = set(
        row[0] for row in db.query(Machine.id).filter(Machine.id.in_(machine_ids)).all()
    )

    is_sqlite = db.bind.dialect.name == "sqlite"
    insert_fn = sqlite_insert if is_sqlite else pg_insert

    results: List[ItemIngestResult] = []
    inserted_count = 0
    duplicate_count = 0
    rejected_count = 0

    for item in sorted_items:
        # Normalize item.ts timezone
        item_ts = item.ts
        if item_ts.tzinfo is None:
            item_ts = item_ts.replace(tzinfo=timezone.utc)

        # 1. Validation: Future timestamp check (> now + 5 min)
        if item_ts > max_future_ts:
            results.append(
                ItemIngestResult(
                    machine_id=item.machine_id,
                    ts=item.ts,
                    status="rejected",
                    reason="Future timestamp exceeds 5-minute threshold",
                )
            )
            rejected_count += 1
            continue

        # 2. Validation: Unknown machine check (no auto-register)
        if item.machine_id not in existing_machines:
            results.append(
                ItemIngestResult(
                    machine_id=item.machine_id,
                    ts=item.ts,
                    status="rejected",
                    reason=f"Unknown machine_id '{item.machine_id}'",
                )
            )
            rejected_count += 1
            continue

        # 3. Ingest Step 1: INSERT INTO telemetry ... ON CONFLICT (machine_id, ts) DO NOTHING
        stmt = (
            insert_fn(Telemetry)
            .values(
                machine_id=item.machine_id,
                ts=item_ts,
                cabinet_temp_c=item.cabinet_temp_c,
                cooling_status=item.cooling_status,
                operating_state=item.operating_state,
                error_codes=item.error_codes or [],
                received_at=now,
            )
            .on_conflict_do_nothing(index_elements=["machine_id", "ts"])
            .returning(Telemetry.id)
        )
        insert_res = db.execute(stmt).fetchone()

        if insert_res is not None:
            # Successfully inserted new event
            inserted_count += 1
            results.append(
                ItemIngestResult(machine_id=item.machine_id, ts=item.ts, status="inserted")
            )

            # Ingest Step 2: UPDATE machines SET last_event_ts, last_state, last_temp,
            # last_cooling, active_faults WHERE id = :id AND (last_event_ts IS NULL OR last_event_ts < :ts)
            db.execute(
                update(Machine)
                .where(Machine.id == item.machine_id)
                .where(
                    or_(
                        Machine.last_event_ts.is_(None),
                        Machine.last_event_ts < item_ts,
                    )
                )
                .values(
                    last_event_ts=item_ts,
                    last_state=item.operating_state,
                    last_temp=item.cabinet_temp_c,
                    last_cooling=item.cooling_status,
                    active_faults=item.error_codes or [],
                )
                .execution_options(synchronize_session=False)
            )
        else:
            # Duplicate (machine_id, ts)
            duplicate_count += 1
            results.append(
                ItemIngestResult(
                    machine_id=item.machine_id,
                    ts=item.ts,
                    status="duplicate",
                    reason="Duplicate (machine_id, ts) ignored",
                )
            )

        # Ingest Step 3: Always UPDATE machines SET last_received_at = now()
        db.execute(
            update(Machine)
            .where(Machine.id == item.machine_id)
            .values(last_received_at=now)
            .execution_options(synchronize_session=False)
        )

    return TelemetryBatchResponse(
        total=len(items),
        inserted=inserted_count,
        duplicate=duplicate_count,
        rejected=rejected_count,
        results=results,
    )

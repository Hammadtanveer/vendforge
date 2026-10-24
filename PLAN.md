# VendForge Fleet Management - PLAN

## Locked decisions

| Topic | Decision |
|---|---|
| Report interval | 60s |
| OFFLINE threshold | now - last_received_at > 180s |
| Never reported | UNKNOWN |
| Future timestamp | ts > now + 5 min -> rejected for that item |
| Duplicate (machine_id, ts) | ignored, first write wins |
| Unknown machine_id | item rejected (no auto-register) |
| Batch size cap | 500 items per request |
| Batch handling | items sorted by ts ASC, one transaction, per-item result (inserted / duplicate / rejected) |
| DB error mid-batch | whole batch rolls back, 5xx, machine retries (safe because idempotent) |

## Ingest per item
1. `INSERT INTO telemetry ... ON CONFLICT (machine_id, ts) DO NOTHING`
2. If inserted:
   `UPDATE machines SET last_event_ts, last_state, last_temp, last_cooling, active_faults WHERE id = :id AND (last_event_ts IS NULL OR last_event_ts < :ts)`
3. Always:
   `UPDATE machines SET last_received_at = now()`

## Machine Status Derivation (read time, never stored)
- If `last_received_at IS NULL` -> `UNKNOWN`
- Else if `now() - last_received_at > 180s` -> `OFFLINE`
- Else if `active_faults` is non-empty -> `ERROR`
- Else -> `last_state` (or `ONLINE` if state is normal/idle)

## Checklist (time-boxed)
- [x] 0:00 Analysis review done
- [x] 0:45 Docker + schema up
- [x] 1:45 Register APIs + ingest
- [x] 2:15 Status + inspect + history
- [x] 3:00 Tests green (dup, out-of-order, reconnect batch, future ts, UNKNOWN)
- [x] 3:20 Simulator (optional)
- [x] 3:50 DESIGN.md finished, README run steps

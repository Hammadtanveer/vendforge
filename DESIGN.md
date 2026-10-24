# VendForge Fleet Management System — Architecture & Design

## 1. Problem and Assumptions
VendForge operates a fleet of automated Bear & Berry smoothie vending machines. The central platform monitors deployed machines, evaluates real-time health, tracks historical sensor telemetry, and alerts operators to hardware faults.

### Operational Assumptions
- **Reporting Interval**: Machines report telemetry approximately once every 60 seconds.
- **Clock Drift & Network Disconnects**: Machine onboard hardware clocks can drift or be out of sync. Machines frequently operate offline for hours/days and upload backlogged data in bursts upon reconnection; network re-transmissions cause duplicate packets.
- **Explicit Defaults & Clarifying Questions for Examiner**:
  1. *Offline Definition*: Defaulted to $now - \text{last\_received\_at} > 180\text{s}$ (3 missed intervals). Would ask: Is the timeout dynamic per customer site or globally fixed?
  2. *Clock Trust*: Defaulted to rejecting records $> 5\text{ min}$ in the future while accepting past timestamps into history. Would ask: Should backlogged timestamps older than $N$ days be discarded or routed to cold storage?
  3. *Backlog Burst Cap*: Defaulted to 500 items per batch request. Would ask: What is the maximum local storage capacity of the machine firmware?
  4. *Fault Catalog*: Defaulted to string array codes (`TEXT[]`). Would ask: Is there an authoritative enum of error codes with severity levels?
  5. *Authentication*: Omitted per rules. Would ask: Will machines authenticate using mTLS with embedded client certs or per-device API keys?
  6. *Data Retention*: Raw data retained indefinitely. Would ask: What is the retention policy for sub-minute readings (e.g., 30 days raw, 1 year hourly rollups)?

---

## 2. Scale Math
- **Fleet Size**: 10,000 machines reporting once per minute.
- **Daily Volume**: $10,000 \times 60 \times 24 = 14,400,000$ telemetry rows/day.
- **Throughput**: Average write load is $\frac{14.4\text{M}}{86,400} \approx 167\text{ writes/sec}$ (peak bursts of 500–1,500 writes/sec during localized connectivity restoration).
- **Storage Footprint**: Average row size is $\approx 100\text{ bytes} \rightarrow \approx 1.44\text{ GB/day}$ raw data ($\sim 43\text{ GB/month}$, $\sim 525\text{ GB/year}$).
- **Capacity Assessment**: Plain PostgreSQL 16 easily handles 167 writes/second on modern SSD hardware with batched inserts and composite B-tree indexes. Time-series partitioning or dedicated TSDB is needed within months, not on day one.

---

## 3. Data Model & Snapshot Rationale
```
customers (id VARCHAR(64) PK, name VARCHAR(255))
    │
    ▼ 1:N (ON DELETE CASCADE)
locations (id VARCHAR(64) PK, customer_id FK, name VARCHAR(255), address VARCHAR(512))
    │
    ▼ 1:N (ON DELETE RESTRICT)
machines (id VARCHAR(64) PK, location_id FK, created_at TIMESTAMPTZ,
          last_event_ts TIMESTAMPTZ, last_state VARCHAR(32), last_temp NUMERIC(5,2),
          last_cooling VARCHAR(32), active_faults TEXT[], last_received_at TIMESTAMPTZ)
    │
    ▼ 1:N (ON DELETE CASCADE)
telemetry (machine_id VARCHAR(64), ts TIMESTAMPTZ, cabinet_temp_c NUMERIC(5,2),
           cooling_status VARCHAR(32), operating_state VARCHAR(32), error_codes TEXT[],
           received_at TIMESTAMPTZ,
           PRIMARY KEY (machine_id, ts))
```
- **Why Snapshot Lives on `machines`**: If machine state were calculated strictly by scanning the `telemetry` table, querying fleet status (`GET /machines`) would require a full table scan with `DISTINCT ON (machine_id) ORDER BY machine_id, ts DESC` over tens of millions of rows. Storing a cached snapshot directly on `machines` turns fleet listing into a cheap, single-table query with index lookups (`O(N)` where $N$ is machine count, not telemetry count).

---

## 4. Reliability Matrix & Test Verification

| Problem Scenario | System Handling Strategy | Proving Test in `tests/test_fleet.py` |
|---|---|---|
| **Duplicate Delivery** | `INSERT ... ON CONFLICT (machine_id, ts) DO NOTHING`. First write wins; snapshot update bypassed. | `test_telemetry_duplicate_ignored` |
| **Equal Timestamps** | WHERE clause `last_event_ts < :ts` is false for equal `ts`. First write retains snapshot and history. | `test_equal_ts_first_write_wins` |
| **Late / Out-of-Order** | Appended to telemetry history log. Guarded update (`last_event_ts < :ts`) prevents older data from overwriting newer snapshot. | `test_out_of_order_telemetry_ordering` |
| **Old-Data-Only Batch** | Inserts all historical events into log. Snapshot state/temp unchanged; `last_received_at` updates to `now()` marking machine reachable. | `test_reconnect_with_only_old_data` |
| **Offline Machine** | Connectivity is derived at read time (`now - last_received_at > 180s`). Last known state and `last_event_ts` are preserved and returned. | `test_offline_threshold_derivation`, `test_offline_machine_keeps_last_state` |
| **Bad / Future Clock** | Timestamps where `ts > now + 5 min` rejected per-item; zero telemetry rows stored. | `test_telemetry_future_timestamp_rejected` |
| **Unknown Machine** | Ingest verifies machine exists prior to insertion; rejected per-item (no auto-registration); zero rows stored. | `test_telemetry_unknown_machine_rejected` |
| **Partial / Mid-Batch Failure** | Entire batch executes inside a single database transaction. If DB error occurs (e.g. numeric overflow), entire batch rolls back; returns 500. Retries are safe. | `test_mid_batch_error_rollback` |

---

## 5. Key Architectural Decisions & Trade-Offs

| Decision | Chosen Approach | Alternative Considered | Trade-Off Rationale |
|---|---|---|---|
| **Storage Engine** | Plain PostgreSQL 16 | Kafka + TimescaleDB / ClickHouse | At 167 writes/s, introducing Kafka and TSDB adds distributed operational overhead without performance gains. Plain Postgres easily handles the scale. |
| **Machine Status** | Derived at read time | Stored in database column | Avoids state synchronization bugs, race conditions during out-of-order packet delivery, and scheduled cron jobs to flip status to OFFLINE. |
| **Transaction Boundary** | One transaction per batch | One transaction per item | Single transaction ensures batch atomicity and maximizes write throughput; if mid-batch DB failure occurs, machine retries safely due to idempotency. |
| **Event Time Ordering** | Event timestamp (`ts`) | Ingest arrival time (`received_at`) | Arrival time is non-deterministic under intermittent connectivity and network latency. Only event timestamp represents physical machine chronology. |
| **Concurrency Control** | Guarded atomic `UPDATE` | Explicit row locking (`FOR UPDATE`) | The guarded atomic `UPDATE ... WHERE last_event_ts IS NULL OR last_event_ts < :ts` prevents older delayed events from overwriting a newer snapshot (the WHERE clause re-evaluates against the latest committed row version under PostgreSQL default `READ COMMITTED` isolation). However, this does not eliminate lock contention or deadlocks when batches touch multiple machines in conflicting orders. |

---

## 6. Analytics Design (Queries for Current Schema)

### A. Recurring Faults
Identifies recurring error codes across machines over a 7-day rolling window:
```sql
SELECT
    t.machine_id,
    m.name AS machine_name,
    unnest(t.error_codes) AS fault_code,
    COUNT(*) AS fault_occurrences,
    MIN(t.ts) AS first_seen,
    MAX(t.ts) AS last_seen
FROM telemetry t
JOIN machines m ON m.id = t.machine_id
WHERE t.ts >= NOW() - INTERVAL '7 days'
  AND t.error_codes <> '{}'
GROUP BY t.machine_id, m.name, fault_code
ORDER BY fault_occurrences DESC;
```

### B. Unusual Temperatures (Threshold & Rolling Baseline)
Flags temperatures exceeding critical food-safety thresholds ($> 7^\circ\text{C}$), followed by comparison against the machine's 24-hour historical baseline:
```sql
WITH hourly_stats AS (
    SELECT
        machine_id,
        AVG(cabinet_temp_c) AS baseline_avg_temp,
        STDDEV(cabinet_temp_c) AS baseline_stddev_temp
    FROM telemetry
    WHERE ts >= NOW() - INTERVAL '24 hours'
      AND cabinet_temp_c IS NOT NULL
    GROUP BY machine_id
)
SELECT
    t.machine_id,
    t.ts,
    t.cabinet_temp_c,
    s.baseline_avg_temp,
    ROUND((t.cabinet_temp_c - s.baseline_avg_temp)::numeric, 2) AS temp_deviation_c
FROM telemetry t
JOIN hourly_stats s ON s.machine_id = t.machine_id
WHERE t.ts >= NOW() - INTERVAL '1 hour'
  AND (t.cabinet_temp_c > 7.0 OR t.cabinet_temp_c > (s.baseline_avg_temp + 2 * COALESCE(s.baseline_stddev_temp, 1.0)))
ORDER BY t.ts DESC;
```

---

## 7. Scaling Roadmap

| Fleet Scale | Ingestion Throughput | Storage / Day | Architectural Evolution |
|---|---|---|---|
| **$\sim$1,000** | ~17 writes/s | ~150 MB | Single PostgreSQL 16 instance. Nightly backup and automated 90-day retention cleanup. |
| **$\sim$10,000** | ~167 writes/s | ~1.5 GB | Monthly declarative range partitions on `telemetry (ts)`. Hourly materialized rollup tables. Read replicas for operator queries. |
| **$\sim$50,000+** | ~833 writes/s | ~7.5 GB | Queue buffer (Kafka / AWS SQS) in front of ingestion. Stateless ingestion workers reading from queue and performing bulk `COPY` operations into DB. |
| **100,000+** | ~1,667 writes/s | ~15 GB | Dedicated columnar store for time-series analytics (e.g. ClickHouse). Raw telemetry older than 30 days compacted into Parquet and offloaded to object storage (S3). |

---

## 8. Failure Modes & What Tests Proved
- **Database Failure Mid-Batch**: Tested in `test_mid_batch_error_rollback`. When a record triggers a PostgreSQL `NumericValueOutOfRange` data error, the entire batch transaction aborts and rolls back. PostgreSQL verifies 0 rows inserted and 0 changes to the machine snapshot.
- **Reconnection Burst Storms**: Tested in `test_reconnect_burst_batch`. Out-of-order batches uploaded after network downtime are pre-sorted by `ts ASC`, ensuring the final snapshot reflects the newest event while preserving full history in `ts DESC` order.
- **Network Re-Transmissions**: Tested in `test_telemetry_duplicate_ignored`. Exact duplicate `(machine_id, ts)` packets return `duplicate` status without throwing primary key conflicts.
- **Offline Reachability Tracking**: Tested in `test_offline_machine_keeps_last_state` and `test_offline_threshold_derivation`. Disconnected machines are derived as `OFFLINE` after 180 seconds, but still provide their last known operating state and event timestamp.

---

## 9. Deliberately Deferred (and Risk Analysis)

| Omitted Feature | Reason Deferred | Production Risk Level |
|---|---|---|
| **Per-Machine Auth (mTLS / API Keys)** | Out of assessment scope | **Critical**: Telemetry endpoint is currently unauthenticated; rogue clients could spoof readings. |
| **Rate Limiting** | Kept architecture minimal | **Important**: Malfunctioning machine loops could flood the API. |
| **Database Partitioning** | Unnecessary at current volume | **Important**: Query latency will degrade after 50M+ rows without range partitions. |
| **Proactive OFFLINE Alerting** | Out of scope | **Important**: Inactivity/silence is not an incoming event; requires a periodic background scanner (e.g. celery-beat/pg_cron) to trigger technician dispatch. |
| **Web Dashboard / UI** | Explicitly forbidden by rules | **Optional**: Operators inspect state via REST endpoints or Grafana. |

---

## 10. What to Do Next & Known Limitations
1. **Known Limitations**:
   - *Duplicate Timestamp Collision*: If a machine generates two events with the exact same timestamp but differing payloads, the first write wins and the second is dropped as duplicate. (Production fix: Add a monotonic sequence number to the primary key: `(machine_id, ts, seq)`).
   - *Python-Side Connectivity Filter*: Filtering `?connectivity=` occurs in Python after fetching machines. At larger scale, this must be pushed down into a SQL `CASE` / `WHERE` clause on `last_received_at`.
   - *Concurrent Multi-Machine Deadlocks*: Currently, `app/ingest.py` sorts incoming batch items solely by `ts ASC` (`sorted_items = sorted(items, key=lambda x: x.ts)`). If two concurrent ingest requests touch multiple machines in differing orders (e.g. Batch A updates machine 1 then machine 2, while Batch B updates machine 2 then machine 1), PostgreSQL may encounter a row-level deadlock on `machines`. (Unimplemented mitigation: Process updates grouped by deterministic sorted `machine_id` order while preserving ascending timestamp order for each machine's events).
   - *Simulator Backdating*: `scripts/simulate_fleet.py` uses direct SQL to backdate `last_received_at` for machine `bb-sim-05` to demonstrate the `OFFLINE` state without sleeping 180 seconds.
2. **Next Implementation Steps**:
   - Deploy mTLS reverse proxy (e.g. Nginx/Envoy) verifying hardware X.509 client certificates.
   - Implement declarative monthly partitioning on `telemetry.ts`.
   - Add a lightweight background worker for automated refrigeration failure alerts.

# VendForge Fleet Management - System Design

## 1. Assumptions
- Machines report telemetry approximately once every 60 seconds (`Report interval = 60s`).
- Machine onboard clocks can drift or be misconfigured; network disconnections can last for hours or days.
- When reconnected, machines upload historical backlogs in batches; duplicate packets and out-of-order packets are expected.
- Operators care primarily about:
  1. Is the machine currently reachable?
  2. What is its last known operating state?
  3. Are there active error codes / faults?
  4. What is the historical temperature trend?

---

## 2. Scale Math
- **Fleet scale**: 10,000 machines $\times$ 1 report/min = 14.4 million reports/day.
- **Throughput**: $\frac{14,400,000}{86,400} \approx 167$ writes/second average (bursting to ~500–1,000 writes/sec during localized reconnection storms).
- **Storage**: Each raw telemetry row is $\sim 100$ bytes $\rightarrow \approx 1.44\text{ GB/day}$ raw data ($\sim 45\text{ GB/month}$).
- **Capacity conclusion**: Standard PostgreSQL 16 easily accommodates this write throughput and volume when writes are batched and indexed on `(machine_id, ts DESC)`. Partitioning and retention policies are needed in months, not on day one.

---

## 3. Data Model
```
machines (id PK, name, customer_location, created_at,
          last_event_ts, last_state, last_temp, last_cooling, active_faults,
          last_received_at)
telemetry (id BIGSERIAL PK, machine_id FK, ts TIMESTAMPTZ,
           cabinet_temp_c, cooling_status, operating_state, error_codes,
           received_at, UNIQUE(machine_id, ts))
```
- **Read performance**: The `machines` table maintains a cached snapshot of the latest known event (`last_event_ts`, `last_state`, `last_temp`, `last_cooling`, `active_faults`). Fetching the entire fleet status requires a single cheap `SELECT * FROM machines`, completely avoiding expensive `DISTINCT ON` or window-function scans across millions of telemetry rows.
- **Append-only history**: `telemetry` is an append-only time-series log with an index on `(machine_id, ts DESC)`.

---

## 4. Reliability Matrix

| Problem | Handling Strategy |
|---|---|
| **Duplicates** | Unique constraint `(machine_id, ts)` + `ON CONFLICT DO NOTHING`. First write wins; duplicates return `status: "duplicate"` and are safely ignored. |
| **Late / Out-of-Order** | Stored in historical telemetry log. The machine snapshot is updated **only if** `last_event_ts IS NULL OR last_event_ts < :ts`. Older delayed data never overwrites newer machine state. |
| **Offline Machine** | Machine status is **DERIVED at read time** (`now - last_received_at > 180s`), never stored as a static column. |
| **Reconnect with Old Data Only** | Machine becomes `ONLINE` immediately (since `last_received_at` updates to `now`), while snapshot preserves the highest event `ts`. |
| **Bad / Skewed Clock** | Telemetry with `ts > now + 5 min` is rejected per-item. Both `ts` (machine clock) and `received_at` (server clock) are tracked. |
| **Partial Failure** | Entire batch executes inside a single database transaction. If a DB error occurs mid-batch, it rolls back and returns HTTP 500; machine retries safely due to idempotency. |

---

## 5. Analytics (Design)
- **Recurring Faults**:
  ```sql
  SELECT machine_id, unnest(error_codes) AS fault_code, COUNT(*) AS occurrences
  FROM telemetry
  WHERE ts >= NOW() - INTERVAL '7 days'
  GROUP BY machine_id, fault_code
  ORDER BY occurrences DESC;
  ```
  *Evolution*: As volume scales, transition to a `fault_events` table tracking explicit state transitions (`FAULT_START`, `FAULT_CLEARED`).
- **Unusual Temperatures**:
  - Baseline: Flag temperature readings outside safe operational limits ($> 7^\circ\text{C}$ for smoothie refrigeration).
  - Advanced: Compute moving average and standard deviation over hourly rollups per machine location to detect thermal degradation before compressor failure.

---

## 6. Architecture Evolution

| Fleet Size | Architecture |
|---|---|
| **$\sim$1,000** | Single PostgreSQL instance with `db/init.sql`, automated daily backup, and 90-day retention job. |
| **$\sim$10,000** | Monthly range partitions on `telemetry(ts)` (or TimescaleDB hypertables), hourly rollup aggregate tables, batched bulk inserts. |
| **$\sim$50,000+** | High-throughput ingest buffer (e.g. Kafka / AWS SQS) decoupling HTTP ingest from DB writes, stateless worker pools, read replicas for operators. |
| **100,000+** | Dedicated columnar store for time-series telemetry (e.g. ClickHouse), cold data archived to S3 / Parquet for historical analytics. |

---

## 7. Deliberately Skipped (and Trade-Off Analysis)

| Skipped Component | Reason Skipped | Risk If Omitted in Production |
|---|---|---|
| **Per-machine Auth (mTLS / API Keys)** | Time-boxed assessment scope | **Critical**: Vulnerable to spoofed telemetry injection. |
| **Rate Limiting** | Kept architecture simple | **Important**: Malfunctioning machine loops could flood the API. |
| **Table Partitioning** | Not needed at demo scale | **Important**: Query degradation after tens of millions of rows. |
| **Frontend UI / Push Notifications** | Out of scope per rules | **Optional**: Operators can inspect via REST API / Grafana. |

---

## 8. Failure Modes & Test Verification

The reliability model was tested and verified under real-world failure simulations (`tests/test_fleet.py`):

1. **Mid-Batch Database Crash (`test_mid_batch_error_rollback`)**:
   - *Scenario*: Connection drops or DB failure occurs halfway through processing a 500-item batch.
   - *Result*: The entire transaction rolls back. Zero items from the batch are persisted. API returns `500 Internal Server Error`. The machine client can safely retry because the endpoint is idempotent.
2. **Reconnection Burst Storm (`test_reconnect_burst_batch`)**:
   - *Scenario*: A machine reconnected after hours of network downtime and uploaded reports out of chronological order.
   - *Result*: The API sorts by `ts ASC` and applies conditional updates (`last_event_ts < :ts`). The final snapshot correctly reflects the most recent event (`IDLE`), and `/history` returns events sorted strictly by `ts DESC`.
3. **Duplicate Re-Transmission (`test_telemetry_duplicate_ignored`)**:
   - *Scenario*: Machine firmware retries sending the same telemetry reading due to network timeout.
   - *Result*: First write wins; duplicate packet returns `duplicate` status and is ignored without raising an integrity error or altering the snapshot.
4. **Clock Drift / Future Timestamps (`test_telemetry_future_timestamp_rejected`)**:
   - *Scenario*: Machine clock drifts ahead by 10 minutes.
   - *Result*: Item is flagged as `rejected` with explanation `"Future timestamp exceeds 5-minute threshold"`, protecting query ordering and database integrity.
5. **Connectivity Timeout (`test_offline_threshold_derivation`)**:
   - *Scenario*: Machine stops communicating for $> 180$ seconds.
   - *Result*: Read-time query automatically returns `status: "OFFLINE"`.
6. **Unregistered Devices (`test_telemetry_unknown_machine_rejected`)**:
   - *Scenario*: Rogue or decommissioned machine sends telemetry.
   - *Result*: Item is rejected with `"Unknown machine_id"`; no dirty auto-registration occurs.

---

## 9. Next Steps
1. **Device Authentication**: Secure machine ingestion using mTLS with client device certificates or signed JWTs.
2. **Token Bucket Rate Limiting**: Limit ingest frequency per `machine_id` (e.g. max 2 batches per minute) using a lightweight token bucket.
3. **PostgreSQL Partitioning**: Add declarative range partitioning by month on `telemetry.ts` and automated retention cleanup.
4. **Automated Temperature Degradation Alerts**: Background worker detecting machines with 3 consecutive readings $> 7^\circ\text{C}$ to alert field technicians before smoothie spoilage.

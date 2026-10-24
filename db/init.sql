-- VendForge Fleet Management Database Initialization
-- No Alembic: schema managed directly in this file

CREATE TABLE IF NOT EXISTS customers (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    id VARCHAR(64) PRIMARY KEY,
    customer_id VARCHAR(64) NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    address VARCHAR(512)
);

CREATE TABLE IF NOT EXISTS machines (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    location_id VARCHAR(64) NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Cached telemetry fields updated ONLY when incoming ts > last_event_ts
    last_event_ts TIMESTAMPTZ,
    last_state VARCHAR(32),
    last_temp NUMERIC(5, 2),
    last_cooling VARCHAR(32),
    active_faults TEXT[] NOT NULL DEFAULT '{}',
    -- Updated on EVERY valid received telemetry item (for OFFLINE threshold derivation)
    last_received_at TIMESTAMPTZ
    -- Note: Machine status is DERIVED at read time. NEVER stored.
);

CREATE INDEX IF NOT EXISTS idx_machines_location_id 
    ON machines (location_id);

CREATE TABLE IF NOT EXISTS telemetry (
    machine_id VARCHAR(64) NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,
    cabinet_temp_c NUMERIC(5, 2),
    cooling_status VARCHAR(32),
    operating_state VARCHAR(32),
    error_codes TEXT[] NOT NULL DEFAULT '{}',
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (machine_id, ts)
);

-- Index for fast retrieval ordered by machine event timestamp (ts)
CREATE INDEX IF NOT EXISTS idx_telemetry_machine_ts_desc 
    ON telemetry (machine_id, ts DESC);

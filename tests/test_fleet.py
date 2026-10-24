from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from app.models import Machine, Telemetry


def test_machine_registration_and_unknown_status(client):
    """A registered machine that has never reported has UNKNOWN status."""
    resp = client.post(
        "/machines",
        json={
            "id": "bb-101",
            "name": "Bear & Berry Downtown",
            "customer_location": "Building A, Floor 1",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "bb-101"
    assert data["status"] == "UNKNOWN"
    assert data["last_received_at"] is None

    # Duplicate registration should return 409
    dup_resp = client.post(
        "/machines",
        json={
            "id": "bb-101",
            "name": "Bear & Berry Downtown Duplicate",
            "customer_location": "Building A, Floor 1",
        },
    )
    assert dup_resp.status_code == 409


def test_telemetry_future_timestamp_rejected(client):
    """Items with ts > now + 5 min must be rejected."""
    client.post(
        "/machines",
        json={"id": "bb-102", "name": "Machine 102", "customer_location": "Airport"},
    )

    future_ts = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    resp = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-102",
                    "ts": future_ts,
                    "cabinet_temp_c": 3.5,
                    "cooling_status": "NORMAL",
                    "operating_state": "IDLE",
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["rejected"] == 1
    assert data["results"][0]["status"] == "rejected"
    assert "Future timestamp" in data["results"][0]["reason"]


def test_telemetry_unknown_machine_rejected(client):
    """Telemetry for non-registered machine is rejected (no auto-register)."""
    now_ts = datetime.now(timezone.utc).isoformat()
    resp = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "unregistered-01",
                    "ts": now_ts,
                    "cabinet_temp_c": 4.0,
                    "operating_state": "IDLE",
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rejected"] == 1
    assert data["results"][0]["status"] == "rejected"
    assert "Unknown machine_id" in data["results"][0]["reason"]


def test_telemetry_duplicate_ignored(client):
    """Duplicate (machine_id, ts) is ignored (first write wins)."""
    client.post(
        "/machines",
        json={"id": "bb-103", "name": "Machine 103", "customer_location": "Station"},
    )
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()

    # First write
    resp1 = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-103",
                    "ts": ts,
                    "cabinet_temp_c": 3.8,
                    "operating_state": "IDLE",
                }
            ]
        },
    )
    assert resp1.status_code == 200
    assert resp1.json()["inserted"] == 1

    # Second write with same (machine_id, ts)
    resp2 = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-103",
                    "ts": ts,
                    "cabinet_temp_c": 9.9,
                    "operating_state": "VENDING",
                }
            ]
        },
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["inserted"] == 0
    assert data2["duplicate"] == 1
    assert data2["results"][0]["status"] == "duplicate"

    # Verify first write won and temp was not overwritten to 9.9
    inspect_resp = client.get("/machines/bb-103")
    assert inspect_resp.status_code == 200
    assert float(inspect_resp.json()["last_temp"]) == 3.8
    assert inspect_resp.json()["last_state"] == "IDLE"


def test_out_of_order_telemetry_ordering(client):
    """Older delayed telemetry arriving later must NOT overwrite newer state."""
    client.post(
        "/machines",
        json={"id": "bb-104", "name": "Machine 104", "customer_location": "Mall"},
    )

    base_time = datetime.now(timezone.utc)
    t_older = (base_time - timedelta(minutes=10)).isoformat()
    t_newer = (base_time - timedelta(minutes=2)).isoformat()

    # 1. Ingest newer event first
    resp_newer = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-104",
                    "ts": t_newer,
                    "cabinet_temp_c": 4.1,
                    "operating_state": "VENDING",
                }
            ]
        },
    )
    assert resp_newer.json()["inserted"] == 1

    # Machine state is VENDING
    m_check1 = client.get("/machines/bb-104").json()
    assert m_check1["last_state"] == "VENDING"

    # 2. Ingest delayed older event later
    resp_older = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-104",
                    "ts": t_older,
                    "cabinet_temp_c": 3.0,
                    "operating_state": "IDLE",
                }
            ]
        },
    )
    assert resp_older.json()["inserted"] == 1

    # Machine snapshot must still reflect the newer event (VENDING at t_newer), not IDLE
    m_check2 = client.get("/machines/bb-104").json()
    assert m_check2["last_state"] == "VENDING"
    assert float(m_check2["last_temp"]) == 4.1


def test_reconnect_burst_batch(client):
    """Simulates a machine reconnecting and uploading an unsorted burst batch."""
    client.post(
        "/machines",
        json={"id": "bb-105", "name": "Machine 105", "customer_location": "Gym"},
    )

    base_time = datetime.now(timezone.utc)
    t1 = (base_time - timedelta(minutes=4)).isoformat()
    t2 = (base_time - timedelta(minutes=3)).isoformat()
    t3 = (base_time - timedelta(minutes=2)).isoformat()

    # Provide batch in reverse order
    resp = client.post(
        "/telemetry",
        json={
            "items": [
                {"machine_id": "bb-105", "ts": t3, "cabinet_temp_c": 4.0, "operating_state": "IDLE"},
                {"machine_id": "bb-105", "ts": t1, "cabinet_temp_c": 3.5, "operating_state": "MAINTENANCE"},
                {"machine_id": "bb-105", "ts": t2, "cabinet_temp_c": 3.8, "operating_state": "CLEANING"},
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 3

    # Latest state must be the highest ts (t3 -> IDLE)
    m = client.get("/machines/bb-105").json()
    assert m["last_state"] == "IDLE"

    # Check history endpoint is strictly ordered by ts DESC
    hist_resp = client.get("/machines/bb-105/history")
    assert hist_resp.status_code == 200
    history = hist_resp.json()
    assert len(history) == 3
    ts0 = datetime.fromisoformat(history[0]["ts"])
    ts1 = datetime.fromisoformat(history[1]["ts"])
    ts2 = datetime.fromisoformat(history[2]["ts"])
    assert ts0 > ts1 > ts2
    assert ts0 == datetime.fromisoformat(t3)
    assert ts1 == datetime.fromisoformat(t2)
    assert ts2 == datetime.fromisoformat(t1)


def test_offline_threshold_derivation(client, db_session):
    """Machine status derives to OFFLINE when now - last_received_at > 180s."""
    client.post(
        "/machines",
        json={"id": "bb-106", "name": "Machine 106", "customer_location": "Campus"},
    )

    # Artificially set last_received_at to 200s ago in PostgreSQL
    past_rec = datetime.now(timezone.utc) - timedelta(seconds=200)
    db_session.query(Machine).filter(Machine.id == "bb-106").update(
        {
            "last_received_at": past_rec,
            "last_event_ts": past_rec,
            "last_state": "IDLE",
        }
    )
    db_session.commit()

    resp = client.get("/machines/bb-106")
    assert resp.status_code == 200
    assert resp.json()["status"] == "OFFLINE"


def test_error_status_derivation(client):
    """Active faults derive to ERROR status if recently received."""
    client.post(
        "/machines",
        json={"id": "bb-107", "name": "Machine 107", "customer_location": "Hospital"},
    )
    ts = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-107",
                    "ts": ts,
                    "cabinet_temp_c": 8.5,
                    "operating_state": "FAULT",
                    "error_codes": ["E01_TEMP_HIGH", "E04_COMPRESSOR_FAIL"],
                }
            ]
        },
    )
    resp = client.get("/machines/bb-107")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ERROR"
    assert "E01_TEMP_HIGH" in data["active_faults"]


def test_batch_size_cap(client):
    """Batches exceeding 500 items must be rejected with 422."""
    now_ts = datetime.now(timezone.utc).isoformat()
    items = [
        {"machine_id": "bb-101", "ts": now_ts, "cabinet_temp_c": 4.0}
        for _ in range(501)
    ]
    resp = client.post("/telemetry", json={"items": items})
    assert resp.status_code == 422


def test_mid_batch_error_rollback(client, db_session):
    """
    Test atomic transaction rollback under a REAL PostgreSQL database failure.
    Item 1 is valid, item 2 triggers a real PostgreSQL numeric overflow error
    (cabinet_temp_c=9999.99 overflows NUMERIC(5,2)).
    No monkeypatching.
    Assert: API returns 5xx, zero telemetry rows exist, and machine snapshot is untouched.
    """
    client.post(
        "/machines",
        json={"id": "bb-108", "name": "Machine 108", "customer_location": "Plaza"},
    )

    base_time = datetime.now(timezone.utc)
    t1 = (base_time - timedelta(minutes=2)).isoformat()
    t2 = (base_time - timedelta(minutes=1)).isoformat()

    resp = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-108",
                    "ts": t1,
                    "cabinet_temp_c": 4.0,
                    "operating_state": "IDLE",
                },
                {
                    "machine_id": "bb-108",
                    "ts": t2,
                    "cabinet_temp_c": 9999.99,  # Genuine PostgreSQL numeric overflow
                    "operating_state": "VENDING",
                },
            ]
        },
    )
    # 1. API must return 5xx (HTTP 500)
    assert resp.status_code == 500
    assert "Database error mid-batch" in resp.json()["detail"]

    # 2. Zero telemetry rows exist in PostgreSQL after transaction rollback
    telemetry_count = db_session.query(Telemetry).filter(Telemetry.machine_id == "bb-108").count()
    assert telemetry_count == 0

    # 3. Machine snapshot is completely unchanged (no partial writes)
    machine = db_session.get(Machine, "bb-108")
    assert machine.last_event_ts is None
    assert machine.last_state is None
    assert machine.last_temp is None
    assert machine.last_received_at is None
    assert machine.derive_status() == "UNKNOWN"

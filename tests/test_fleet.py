from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from app.models import Machine, Telemetry


def test_machine_registration_and_unknown_status(client, default_location):
    """A registered machine that has never reported has UNKNOWN connectivity, null operating_state."""
    resp = client.post(
        "/machines",
        json={
            "id": "bb-101",
            "name": "Bear & Berry Downtown",
            "location_id": default_location,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "bb-101"
    assert data["location_id"] == default_location
    assert data["location_name"] == "Downtown Central"
    assert data["customer_id"] == "cust-1"
    assert data["customer_name"] == "Bear & Berry HQ"
    assert data["connectivity"] == "UNKNOWN"
    assert data["operating_state"] is None
    assert data["has_active_faults"] is False
    assert data["last_received_at"] is None

    # Duplicate registration should return 409
    dup_resp = client.post(
        "/machines",
        json={
            "id": "bb-101",
            "name": "Bear & Berry Downtown Duplicate",
            "location_id": default_location,
        },
    )
    assert dup_resp.status_code == 409


def test_telemetry_future_timestamp_rejected(client, db_session, default_location):
    """Items with ts > now + 5 min must be rejected; zero telemetry rows stored."""
    client.post(
        "/machines",
        json={"id": "bb-102", "name": "Machine 102", "location_id": default_location},
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

    # Strengthened: assert zero telemetry rows exist in PostgreSQL for the rejected item
    count = db_session.query(Telemetry).filter(Telemetry.machine_id == "bb-102").count()
    assert count == 0


def test_telemetry_unknown_machine_rejected(client, db_session):
    """Telemetry for non-registered machine is rejected (no auto-register); zero rows stored."""
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

    # Strengthened: assert zero telemetry rows exist in PostgreSQL for the unregistered item
    count = db_session.query(Telemetry).filter(Telemetry.machine_id == "unregistered-01").count()
    assert count == 0


def test_telemetry_duplicate_ignored(client, default_location):
    """Duplicate (machine_id, ts) is ignored (first write wins)."""
    client.post(
        "/machines",
        json={"id": "bb-103", "name": "Machine 103", "location_id": default_location},
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
    assert inspect_resp.json()["operating_state"] == "IDLE"
    assert inspect_resp.json()["connectivity"] == "ONLINE"


def test_out_of_order_telemetry_ordering(client, default_location):
    """Older delayed telemetry arriving later must NOT overwrite newer state."""
    client.post(
        "/machines",
        json={"id": "bb-104", "name": "Machine 104", "location_id": default_location},
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
    assert m_check1["operating_state"] == "VENDING"

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
    assert m_check2["operating_state"] == "VENDING"
    assert float(m_check2["last_temp"]) == 4.1


def test_reconnect_burst_batch(client, default_location):
    """Simulates a machine reconnecting and uploading an unsorted burst batch."""
    client.post(
        "/machines",
        json={"id": "bb-105", "name": "Machine 105", "location_id": default_location},
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
    assert m["operating_state"] == "IDLE"
    assert m["connectivity"] == "ONLINE"

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


def test_offline_threshold_derivation(client, db_session, default_location):
    """Machine status derives to OFFLINE when now - last_received_at > 180s."""
    client.post(
        "/machines",
        json={"id": "bb-106", "name": "Machine 106", "location_id": default_location},
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
    assert resp.json()["connectivity"] == "OFFLINE"
    assert resp.json()["operating_state"] == "IDLE"


def test_error_status_derivation(client, default_location):
    """Active faults set has_active_faults=True while connectivity remains ONLINE if recently received."""
    client.post(
        "/machines",
        json={"id": "bb-107", "name": "Machine 107", "location_id": default_location},
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
    assert data["connectivity"] == "ONLINE"
    assert data["has_active_faults"] is True
    assert "E01_TEMP_HIGH" in data["active_faults"]
    assert "E04_COMPRESSOR_FAIL" in data["active_faults"]


def test_batch_size_cap(client):
    """Batches exceeding 500 items must be rejected with 422."""
    now_ts = datetime.now(timezone.utc).isoformat()
    items = [
        {"machine_id": "bb-101", "ts": now_ts, "cabinet_temp_c": 4.0}
        for _ in range(501)
    ]
    resp = client.post("/telemetry", json={"items": items})
    assert resp.status_code == 422


def test_mid_batch_error_rollback(client, db_session, default_location):
    """
    Test atomic transaction rollback under a REAL PostgreSQL database failure.
    Item 1 is valid, item 2 triggers a real PostgreSQL numeric overflow error
    (cabinet_temp_c=9999.99 overflows NUMERIC(5,2)).
    No monkeypatching.
    Assert: API returns 5xx, zero telemetry rows exist, and machine snapshot is untouched.
    """
    client.post(
        "/machines",
        json={"id": "bb-108", "name": "Machine 108", "location_id": default_location},
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
    assert machine.derive_connectivity() == "UNKNOWN"


def test_offline_machine_keeps_last_state(client, db_session, default_location):
    """After >180s gap, connectivity is OFFLINE, operating_state and last_event_ts are still last known."""
    client.post(
        "/machines",
        json={"id": "bb-201", "name": "Machine 201", "location_id": default_location},
    )
    event_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-201",
                    "ts": event_time.isoformat(),
                    "cabinet_temp_c": 3.9,
                    "cooling_status": "NORMAL",
                    "operating_state": "VENDING",
                }
            ]
        },
    )

    # Set last_received_at to 200s ago (> 180s)
    past_rec = datetime.now(timezone.utc) - timedelta(seconds=200)
    db_session.query(Machine).filter(Machine.id == "bb-201").update(
        {"last_received_at": past_rec}
    )
    db_session.commit()

    resp = client.get("/machines/bb-201")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connectivity"] == "OFFLINE"
    assert data["operating_state"] == "VENDING"
    assert data["last_event_ts"] is not None
    assert float(data["last_temp"]) == 3.9


def test_reconnect_with_only_old_data(client, default_location):
    """
    Machine has newer snapshot, then a batch with only older ts arrives.
    Assert connectivity ONLINE, snapshot (operating_state, last_event_ts, last_temp) unchanged,
    and old rows exist in history.
    """
    client.post(
        "/machines",
        json={"id": "bb-202", "name": "Machine 202", "location_id": default_location},
    )
    now = datetime.now(timezone.utc)
    ts_newer = now - timedelta(minutes=1)
    ts_older = now - timedelta(minutes=15)

    # Ingest newer report first
    client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-202",
                    "ts": ts_newer.isoformat(),
                    "cabinet_temp_c": 4.2,
                    "operating_state": "VENDING",
                }
            ]
        },
    )

    # Ingest batch with ONLY older data
    resp = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-202",
                    "ts": ts_older.isoformat(),
                    "cabinet_temp_c": 2.8,
                    "operating_state": "IDLE",
                }
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 1

    # Verify machine snapshot: connectivity ONLINE, state & temp still from newer report
    m = client.get("/machines/bb-202").json()
    assert m["connectivity"] == "ONLINE"
    assert m["operating_state"] == "VENDING"
    assert float(m["last_temp"]) == 4.2
    assert datetime.fromisoformat(m["last_event_ts"]) == ts_newer

    # Verify both records exist in history
    hist = client.get("/machines/bb-202/history").json()
    assert len(hist) == 2
    assert datetime.fromisoformat(hist[0]["ts"]) == ts_newer
    assert datetime.fromisoformat(hist[1]["ts"]) == ts_older


def test_never_reported_is_unknown(client, default_location):
    """Machine that has never reported has connectivity UNKNOWN, operating_state null, has_active_faults false."""
    client.post(
        "/machines",
        json={"id": "bb-203", "name": "Machine 203", "location_id": default_location},
    )
    resp = client.get("/machines/bb-203")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connectivity"] == "UNKNOWN"
    assert data["operating_state"] is None
    assert data["has_active_faults"] is False
    assert data["active_faults"] == []
    assert data["last_event_ts"] is None
    assert data["last_received_at"] is None


def test_faults_do_not_change_connectivity(client, default_location):
    """Machine with active faults and recent report shows connectivity ONLINE and has_active_faults true."""
    client.post(
        "/machines",
        json={"id": "bb-204", "name": "Machine 204", "location_id": default_location},
    )
    now_ts = datetime.now(timezone.utc).isoformat()
    client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-204",
                    "ts": now_ts,
                    "cabinet_temp_c": 9.5,
                    "operating_state": "ERROR",
                    "error_codes": ["E02_PRESSURE_LOW"],
                }
            ]
        },
    )
    resp = client.get("/machines/bb-204")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connectivity"] == "ONLINE"
    assert data["has_active_faults"] is True
    assert data["active_faults"] == ["E02_PRESSURE_LOW"]
    assert data["operating_state"] == "ERROR"


def test_equal_ts_first_write_wins(client, db_session, default_location):
    """Same machine_id and ts sent twice with different temperatures; history and snapshot keep the first."""
    client.post(
        "/machines",
        json={"id": "bb-205", "name": "Machine 205", "location_id": default_location},
    )
    ts = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()

    # First write
    resp1 = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-205",
                    "ts": ts,
                    "cabinet_temp_c": 3.4,
                    "operating_state": "IDLE",
                }
            ]
        },
    )
    assert resp1.status_code == 200
    assert resp1.json()["inserted"] == 1

    # Second write with identical ts but different temperature
    resp2 = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-205",
                    "ts": ts,
                    "cabinet_temp_c": 8.7,
                    "operating_state": "VENDING",
                }
            ]
        },
    )
    assert resp2.status_code == 200
    assert resp2.json()["duplicate"] == 1

    # History keeps the first write
    history = client.get("/machines/bb-205/history").json()
    assert len(history) == 1
    assert float(history[0]["cabinet_temp_c"]) == 3.4
    assert history[0]["operating_state"] == "IDLE"

    # Snapshot keeps the first write
    m = client.get("/machines/bb-205").json()
    assert float(m["last_temp"]) == 3.4
    assert m["operating_state"] == "IDLE"


# --- New Phase 4 Tests (Customers, Locations & Query Filters) ---

def test_unknown_location_rejected(client, db_session):
    """POST /machines with bad location_id returns 404 and machine is not created."""
    resp = client.post(
        "/machines",
        json={"id": "bb-bad-loc", "name": "Bad Machine", "location_id": "nonexistent-loc"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()

    # Verify machine was not created in DB
    machine = db_session.get(Machine, "bb-bad-loc")
    assert machine is None


def test_unknown_customer_rejected_for_location(client, db_session):
    """POST /locations with bad customer_id returns 404."""
    resp = client.post(
        "/locations",
        json={
            "id": "loc-bad-cust",
            "customer_id": "nonexistent-cust",
            "name": "Ghost Location",
        },
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_customer_location_machine_flow(client):
    """Customer -> Location -> Machine flow succeeds with correct relationships."""
    c_resp = client.post("/customers", json={"id": "cust-flow", "name": "Acme Juices"})
    assert c_resp.status_code == 201

    l_resp = client.post(
        "/locations",
        json={
            "id": "loc-flow",
            "customer_id": "cust-flow",
            "name": "Airport Terminal 3",
            "address": "Gate 22",
        },
    )
    assert l_resp.status_code == 201

    m_resp = client.post(
        "/machines",
        json={"id": "m-flow-01", "name": "Smoothie Station 1", "location_id": "loc-flow"},
    )
    assert m_resp.status_code == 201
    data = m_resp.json()
    assert data["id"] == "m-flow-01"
    assert data["location_id"] == "loc-flow"
    assert data["location_name"] == "Airport Terminal 3"
    assert data["customer_id"] == "cust-flow"
    assert data["customer_name"] == "Acme Juices"

    # Verify GET /machines/{id}
    inspect = client.get("/machines/m-flow-01").json()
    assert inspect["location_name"] == "Airport Terminal 3"
    assert inspect["customer_name"] == "Acme Juices"


def test_filter_by_location(client, default_location):
    """Filtering by location_id returns only matching machines."""
    # Create second customer and location
    client.post("/customers", json={"id": "cust-2", "name": "Metro Retail"})
    client.post("/locations", json={"id": "loc-2", "customer_id": "cust-2", "name": "Metro Station"})

    client.post("/machines", json={"id": "m-loc-1", "name": "M1", "location_id": default_location})
    client.post("/machines", json={"id": "m-loc-2", "name": "M2", "location_id": "loc-2"})

    # Filter by loc-1
    res1 = client.get(f"/machines?location_id={default_location}").json()
    assert len(res1) == 1
    assert res1[0]["id"] == "m-loc-1"

    # Filter by loc-2
    res2 = client.get("/machines?location_id=loc-2").json()
    assert len(res2) == 1
    assert res2[0]["id"] == "m-loc-2"


def test_response_shows_names(client, default_location):
    """MachineResponse accurately shows location_name and customer_name."""
    client.post("/machines", json={"id": "m-names-check", "name": "Named Machine", "location_id": default_location})

    m = client.get("/machines/m-names-check").json()
    assert m["location_id"] == default_location
    assert m["location_name"] == "Downtown Central"
    assert m["customer_id"] == "cust-1"
    assert m["customer_name"] == "Bear & Berry HQ"


def test_filter_by_connectivity(client, db_session, default_location):
    """
    Test ?connectivity= filter for ONLINE, UNKNOWN, OFFLINE;
    invalid filter returns 422.
    """
    # 1. UNKNOWN machine (never reported)
    client.post("/machines", json={"id": "m-conn-unknown", "name": "Unknown", "location_id": default_location})

    # 2. ONLINE machine (reported recently)
    client.post("/machines", json={"id": "m-conn-online", "name": "Online", "location_id": default_location})
    client.post(
        "/telemetry",
        json={"items": [{"machine_id": "m-conn-online", "ts": datetime.now(timezone.utc).isoformat(), "cabinet_temp_c": 3.7}]}
    )

    # 3. OFFLINE machine (backdated last_received_at)
    client.post("/machines", json={"id": "m-conn-offline", "name": "Offline", "location_id": default_location})
    client.post(
        "/telemetry",
        json={"items": [{"machine_id": "m-conn-offline", "ts": datetime.now(timezone.utc).isoformat(), "cabinet_temp_c": 3.7}]}
    )
    past_rec = datetime.now(timezone.utc) - timedelta(seconds=250)
    db_session.query(Machine).filter(Machine.id == "m-conn-offline").update({"last_received_at": past_rec})
    db_session.commit()

    # Filter ONLINE
    res_online = client.get("/machines?connectivity=ONLINE").json()
    assert len(res_online) == 1
    assert res_online[0]["id"] == "m-conn-online"

    # Filter UNKNOWN
    res_unknown = client.get("/machines?connectivity=UNKNOWN").json()
    assert len(res_unknown) == 1
    assert res_unknown[0]["id"] == "m-conn-unknown"

    # Filter OFFLINE
    res_offline = client.get("/machines?connectivity=OFFLINE").json()
    assert len(res_offline) == 1
    assert res_offline[0]["id"] == "m-conn-offline"

    # Invalid connectivity filter returns 422
    res_invalid = client.get("/machines?connectivity=BROKEN")
    assert res_invalid.status_code == 422

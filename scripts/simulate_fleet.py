"""
End-to-end simulator for Bear & Berry fleet telemetry.
Demonstrates:
- Customer and Location registration
- 5 distinct machine states:
  1. bb-sim-01: ONLINE, normal IDLE
  2. bb-sim-02: ONLINE, FAULT with active error codes
  3. bb-sim-03: ONLINE, reconnected burst backlog
  4. bb-sim-04: UNKNOWN (never reported)
  5. bb-sim-05: OFFLINE (backdated last_received_at for demonstration)
"""

import os
from datetime import datetime, timezone, timedelta
import httpx
from sqlalchemy import create_engine, text

API_BASE = "http://localhost:8000"
DEV_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://vendforge:vendforge_secret@localhost:5432/vendforge",
)


def run_simulation():
    client = httpx.Client(base_url=API_BASE, timeout=10.0)

    print("=== Step 1: Register Customer and Location ===")
    c_resp = client.post("/customers", json={"id": "cust-sim", "name": "Bear & Berry Global Corp"})
    if c_resp.status_code in (201, 409):
        print(f"Customer 'cust-sim': status {c_resp.status_code}")
    else:
        print(f"Customer registration failed: {c_resp.text}")

    l_resp = client.post(
        "/locations",
        json={
            "id": "loc-sim",
            "customer_id": "cust-sim",
            "name": "Flagship Innovation Mall",
            "address": "500 Tech Boulevard, Suite 100",
        },
    )
    if l_resp.status_code in (201, 409):
        print(f"Location 'loc-sim': status {l_resp.status_code}")
    else:
        print(f"Location registration failed: {l_resp.text}")

    print("\n=== Step 2: Register Machines ===")
    machines = [
        {"id": "bb-sim-01", "name": "Downtown Kiosk", "location_id": "loc-sim"},
        {"id": "bb-sim-02", "name": "Airport Express", "location_id": "loc-sim"},
        {"id": "bb-sim-03", "name": "TechPark Central", "location_id": "loc-sim"},
        {"id": "bb-sim-04", "name": "Unconnected New Machine", "location_id": "loc-sim"},
        {"id": "bb-sim-05", "name": "Station North (Offline Demo)", "location_id": "loc-sim"},
    ]
    for m in machines:
        resp = client.post("/machines", json=m)
        if resp.status_code in (201, 409):
            print(f"Registered {m['id']}: status {resp.status_code}")
        else:
            print(f"Failed to register {m['id']}: {resp.text}")

    print("\n=== Step 3: Ingest Regular Telemetry for Machine 1 (Normal IDLE) ===")
    now = datetime.now(timezone.utc)
    t1 = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-sim-01",
                    "ts": now.isoformat(),
                    "cabinet_temp_c": 3.8,
                    "cooling_status": "NORMAL",
                    "operating_state": "IDLE",
                }
            ]
        },
    )
    print("Machine 1 Telemetry Ingest:", t1.json())

    print("\n=== Step 4: Ingest Fault Telemetry for Machine 2 (High Temp & Fault Code) ===")
    t2 = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-sim-02",
                    "ts": now.isoformat(),
                    "cabinet_temp_c": 8.9,
                    "cooling_status": "FAULT",
                    "operating_state": "ERROR",
                    "error_codes": ["E01_TEMP_HIGH", "E04_COMPRESSOR_FAIL"],
                }
            ]
        },
    )
    print("Machine 2 Telemetry Ingest:", t2.json())

    print("\n=== Step 5: Simulate Reconnect Burst for Machine 3 (Out-of-Order + Duplicates) ===")
    burst_items = [
        # Older item
        {
            "machine_id": "bb-sim-03",
            "ts": (now - timedelta(minutes=5)).isoformat(),
            "cabinet_temp_c": 4.1,
            "cooling_status": "NORMAL",
            "operating_state": "IDLE",
        },
        # Newer item
        {
            "machine_id": "bb-sim-03",
            "ts": (now - timedelta(minutes=1)).isoformat(),
            "cabinet_temp_c": 3.9,
            "cooling_status": "NORMAL",
            "operating_state": "VENDING",
        },
        # Duplicate of older item
        {
            "machine_id": "bb-sim-03",
            "ts": (now - timedelta(minutes=5)).isoformat(),
            "cabinet_temp_c": 4.1,
            "cooling_status": "NORMAL",
            "operating_state": "IDLE",
        },
    ]
    t3 = client.post("/telemetry", json={"items": burst_items})
    print("Machine 3 Burst Ingest:", t3.json())

    # Machine 4 (bb-sim-04) does not report -> stays UNKNOWN

    print("\n=== Step 6: Ingest Initial Telemetry for Machine 5 and Simulate OFFLINE State ===")
    t5 = client.post(
        "/telemetry",
        json={
            "items": [
                {
                    "machine_id": "bb-sim-05",
                    "ts": (now - timedelta(minutes=10)).isoformat(),
                    "cabinet_temp_c": 4.0,
                    "cooling_status": "NORMAL",
                    "operating_state": "IDLE",
                }
            ]
        },
    )
    print("Machine 5 Ingest:", t5.json())

    # NOTE: DEMO-ONLY BACKDATING.
    # In production, machines transition to OFFLINE naturally when now() - last_received_at > 180s.
    # To demonstrate this immediately without sleeping 181s, we directly backdate last_received_at in PostgreSQL.
    print("Backdating last_received_at for bb-sim-05 to simulate > 180s offline disconnect (demo-only)...")
    try:
        engine = create_engine(DEV_DATABASE_URL)
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE machines SET last_received_at = NOW() - INTERVAL '10 minutes' WHERE id = 'bb-sim-05'")
            )
            conn.commit()
        print("bb-sim-05 last_received_at backdated successfully.")
    except Exception as exc:
        print(f"Warning: could not directly update DB for bb-sim-05 offline demo ({exc})")

    print("\n=== Step 7: Query Fleet Status for All 5 Machines (Derived at Read Time) ===")
    fleet = client.get("/machines").json()
    for m in fleet:
        print(
            f"Machine: {m['id']:<10} | "
            f"Location: {m['location_name']:<25} | "
            f"Customer: {m['customer_name']:<25} | "
            f"Connectivity: {m['connectivity']:<7} | "
            f"State: {str(m['operating_state']):<7} | "
            f"Faults: {m['active_faults']}"
        )


if __name__ == "__main__":
    run_simulation()

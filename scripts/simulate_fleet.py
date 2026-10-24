"""
Lightweight simulator for Bear & Berry fleet telemetry.
Demonstrates normal reporting, fault states, and reconnecting burst uploads.
"""

import time
from datetime import datetime, timezone, timedelta
import httpx

API_BASE = "http://localhost:8000"


def run_simulation():
    client = httpx.Client(base_url=API_BASE, timeout=10.0)

    print("=== Step 1: Register Machines ===")
    machines = [
        {"id": "bb-sim-01", "name": "Bear & Berry Downtown", "customer_location": "Mall Level 1"},
        {"id": "bb-sim-02", "name": "Bear & Berry Airport", "customer_location": "Terminal 2, Gate 15"},
        {"id": "bb-sim-03", "name": "Bear & Berry TechPark", "customer_location": "Building 4 Lobby"},
    ]
    for m in machines:
        resp = client.post("/machines", json=m)
        if resp.status_code in (201, 409):
            print(f"Registered {m['id']}: status {resp.status_code}")
        else:
            print(f"Failed to register {m['id']}: {resp.text}")

    print("\n=== Step 2: Ingest Regular Telemetry for Machine 1 ===")
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

    print("\n=== Step 3: Ingest Fault Telemetry for Machine 2 (High Temp & Fault Code) ===")
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

    print("\n=== Step 4: Simulate Reconnect Burst for Machine 3 (Out-of-Order + Duplicates) ===")
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

    print("\n=== Step 5: Query Current Fleet Status (Derived at Read Time) ===")
    fleet = client.get("/machines").json()
    for m in fleet:
        print(f"Machine: {m['id']} | Connectivity: {m['connectivity']} | State: {m['operating_state']} | Temp: {m['last_temp']} C | Faults: {m['active_faults']}")


if __name__ == "__main__":
    run_simulation()

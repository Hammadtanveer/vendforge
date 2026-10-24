from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Machine, Telemetry
from app.schemas import MachineCreate, MachineResponse, TelemetryItem

router = APIRouter(prefix="/machines", tags=["machines"])


def to_machine_response(machine: Machine) -> MachineResponse:
    # Connectivity is derived at read time, NEVER stored
    conn = machine.derive_connectivity()
    faults = machine.active_faults or []
    return MachineResponse(
        id=machine.id,
        name=machine.name,
        customer_location=machine.customer_location,
        created_at=machine.created_at,
        connectivity=conn,
        operating_state=machine.last_state,
        has_active_faults=bool(faults),
        active_faults=faults,
        last_event_ts=machine.last_event_ts,
        last_temp=machine.last_temp,
        last_cooling=machine.last_cooling,
        last_received_at=machine.last_received_at,
    )


@router.post("", response_model=MachineResponse, status_code=status.HTTP_201_CREATED)
def register_machine(payload: MachineCreate, db: Session = Depends(get_db)):
    existing = db.get(Machine, payload.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Machine '{payload.id}' is already registered",
        )
    machine = Machine(
        id=payload.id,
        name=payload.name,
        customer_location=payload.customer_location,
    )
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return to_machine_response(machine)


@router.get("", response_model=List[MachineResponse])
def list_machines(db: Session = Depends(get_db)):
    machines = db.query(Machine).all()
    return [to_machine_response(m) for m in machines]


@router.get("/{machine_id}", response_model=MachineResponse)
def get_machine(machine_id: str, db: Session = Depends(get_db)):
    machine = db.get(Machine, machine_id)
    if not machine:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Machine '{machine_id}' not found",
        )
    return to_machine_response(machine)


@router.get("/{machine_id}/history", response_model=List[TelemetryItem])
def get_machine_history(
    machine_id: str,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    machine = db.get(Machine, machine_id)
    if not machine:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Machine '{machine_id}' not found",
        )
    # Strictly ordered by machine event timestamp (ts) DESC per Rule 5
    records = (
        db.query(Telemetry)
        .filter(Telemetry.machine_id == machine_id)
        .order_by(desc(Telemetry.ts))
        .limit(limit)
        .all()
    )
    return [
        TelemetryItem(
            machine_id=r.machine_id,
            ts=r.ts,
            cabinet_temp_c=r.cabinet_temp_c,
            cooling_status=r.cooling_status,
            operating_state=r.operating_state,
            error_codes=r.error_codes or [],
        )
        for r in records
    ]

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc

from app.database import get_db
from app.models import Customer, Location, Machine, Telemetry
from app.schemas import (
    CustomerCreate,
    CustomerResponse,
    LocationCreate,
    LocationResponse,
    MachineCreate,
    MachineResponse,
    TelemetryItem,
)

router = APIRouter(tags=["fleet"])

VALID_CONNECTIVITIES = {"ONLINE", "OFFLINE", "UNKNOWN"}


def to_machine_response(machine: Machine) -> MachineResponse:
    # Connectivity is derived at read time, NEVER stored
    conn = machine.derive_connectivity()
    faults = machine.active_faults or []
    loc = machine.location
    cust = loc.customer if loc else None

    return MachineResponse(
        id=machine.id,
        name=machine.name,
        location_id=machine.location_id,
        location_name=loc.name if loc else "",
        customer_id=cust.id if cust else "",
        customer_name=cust.name if cust else "",
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


# --- Customer Endpoints ---

@router.post("/customers", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db)):
    if db.get(Customer, payload.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Customer '{payload.id}' already exists",
        )
    customer = Customer(id=payload.id, name=payload.name)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


# --- Location Endpoints ---

@router.post("/locations", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
def create_location(payload: LocationCreate, db: Session = Depends(get_db)):
    if db.get(Location, payload.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Location '{payload.id}' already exists",
        )
    if not db.get(Customer, payload.customer_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer '{payload.customer_id}' not found",
        )
    location = Location(
        id=payload.id,
        customer_id=payload.customer_id,
        name=payload.name,
        address=payload.address,
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    return location


# --- Machine Endpoints ---

@router.post("/machines", response_model=MachineResponse, status_code=status.HTTP_201_CREATED)
def register_machine(payload: MachineCreate, db: Session = Depends(get_db)):
    if db.get(Machine, payload.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Machine '{payload.id}' is already registered",
        )
    loc = db.get(Location, payload.location_id)
    if not loc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Location '{payload.location_id}' not found",
        )
    machine = Machine(
        id=payload.id,
        name=payload.name,
        location_id=payload.location_id,
    )
    db.add(machine)
    db.commit()
    
    # Eager load location & customer for response
    machine = (
        db.query(Machine)
        .options(joinedload(Machine.location).joinedload(Location.customer))
        .filter(Machine.id == payload.id)
        .first()
    )
    return to_machine_response(machine)


@router.get("/machines", response_model=List[MachineResponse])
def list_machines(
    location_id: Optional[str] = None,
    connectivity: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if connectivity is not None and connectivity not in VALID_CONNECTIVITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid connectivity filter '{connectivity}'. Must be one of {sorted(VALID_CONNECTIVITIES)}",
        )

    query = db.query(Machine).options(
        joinedload(Machine.location).joinedload(Location.customer)
    )
    if location_id is not None:
        query = query.filter(Machine.location_id == location_id)

    machines = query.all()
    responses = [to_machine_response(m) for m in machines]

    if connectivity is not None:
        responses = [r for r in responses if r.connectivity == connectivity]

    return responses


@router.get("/machines/{machine_id}", response_model=MachineResponse)
def get_machine(machine_id: str, db: Session = Depends(get_db)):
    machine = (
        db.query(Machine)
        .options(joinedload(Machine.location).joinedload(Location.customer))
        .filter(Machine.id == machine_id)
        .first()
    )
    if not machine:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Machine '{machine_id}' not found",
        )
    return to_machine_response(machine)


@router.get("/machines/{machine_id}/history", response_model=List[TelemetryItem])
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

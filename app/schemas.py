from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field


class CustomerCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)


class CustomerResponse(BaseModel):
    id: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class LocationCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    customer_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    address: Optional[str] = Field(None, max_length=512)


class LocationResponse(BaseModel):
    id: str
    customer_id: str
    name: str
    address: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class MachineCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    location_id: str = Field(..., min_length=1, max_length=64)


class MachineResponse(BaseModel):
    id: str
    name: str
    location_id: str
    location_name: str
    customer_id: str
    customer_name: str
    created_at: datetime
    connectivity: Literal["ONLINE", "OFFLINE", "UNKNOWN"]
    operating_state: Optional[str] = None
    has_active_faults: bool = False
    active_faults: List[str] = Field(default_factory=list)
    last_event_ts: Optional[datetime] = None
    last_temp: Optional[Decimal] = None
    last_cooling: Optional[str] = None
    last_received_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TelemetryItem(BaseModel):
    machine_id: str = Field(..., min_length=1, max_length=64)
    ts: datetime
    cabinet_temp_c: Optional[Decimal] = None
    cooling_status: Optional[str] = Field(None, max_length=32)
    operating_state: Optional[str] = Field(None, max_length=32)
    error_codes: List[str] = Field(default_factory=list)


class TelemetryBatchRequest(BaseModel):
    items: List[TelemetryItem] = Field(..., max_length=500)


class ItemIngestResult(BaseModel):
    machine_id: str
    ts: datetime
    status: Literal["inserted", "duplicate", "rejected"]
    reason: Optional[str] = None


class TelemetryBatchResponse(BaseModel):
    total: int
    inserted: int
    duplicate: int
    rejected: int
    results: List[ItemIngestResult]

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from sqlalchemy import (
    Column,
    String,
    DateTime,
    Numeric,
    ForeignKey,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import ARRAY
from app.database import Base


class Customer(Base):
    __tablename__ = "customers"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)

    locations = relationship("Location", back_populates="customer", cascade="all, delete-orphan")


class Location(Base):
    __tablename__ = "locations"

    id = Column(String(64), primary_key=True, index=True)
    customer_id = Column(String(64), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    address = Column(String(512), nullable=True)

    customer = relationship("Customer", back_populates="locations")
    machines = relationship("Machine", back_populates="location")


class Machine(Base):
    __tablename__ = "machines"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    location_id = Column(String(64), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    location = relationship("Location", back_populates="machines")

    # Cached telemetry snapshot (updated ONLY when event ts is strictly newer)
    last_event_ts = Column(DateTime(timezone=True), nullable=True)
    last_state = Column(String(32), nullable=True)
    last_temp = Column(Numeric(5, 2), nullable=True)
    last_cooling = Column(String(32), nullable=True)
    active_faults = Column(ARRAY(String), nullable=False, default=list)

    # Updated on every valid incoming report
    last_received_at = Column(DateTime(timezone=True), nullable=True)

    def derive_connectivity(self, now: Optional[datetime] = None) -> str:
        """
        Derives connectivity at read time. Connectivity is NEVER stored.
        Rules:
        - Never reported -> UNKNOWN
        - now - last_received_at > 180s -> OFFLINE
        - Otherwise -> ONLINE
        Faults do NOT change connectivity.
        """
        if self.last_received_at is None:
            return "UNKNOWN"

        if now is None:
            now = datetime.now(timezone.utc)

        # Ensure tz-awareness
        last_rec = self.last_received_at
        if last_rec.tzinfo is None:
            last_rec = last_rec.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        if (now - last_rec).total_seconds() > 180:
            return "OFFLINE"

        return "ONLINE"


class Telemetry(Base):
    __tablename__ = "telemetry"

    machine_id = Column(
        String(64),
        ForeignKey("machines.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ts = Column(DateTime(timezone=True), primary_key=True)
    cabinet_temp_c = Column(Numeric(5, 2), nullable=True)
    cooling_status = Column(String(32), nullable=True)
    operating_state = Column(String(32), nullable=True)
    error_codes = Column(ARRAY(String), nullable=False, default=list)
    received_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

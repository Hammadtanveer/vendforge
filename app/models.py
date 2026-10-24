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
from sqlalchemy.dialects.postgresql import ARRAY
from app.database import Base


class Machine(Base):
    __tablename__ = "machines"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    customer_location = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import TelemetryBatchRequest, TelemetryBatchResponse
from app.ingest import ingest_telemetry_batch

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("", response_model=TelemetryBatchResponse, status_code=status.HTTP_200_OK)
def ingest_telemetry(payload: TelemetryBatchRequest, db: Session = Depends(get_db)):
    """
    Ingest a batch of up to 500 telemetry items in a single transaction.
    If a database error occurs mid-batch, the entire batch rolls back and a 5xx is returned.
    """
    try:
        response = ingest_telemetry_batch(db, payload.items)
        db.commit()
        return response
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error mid-batch, rolled back: {str(exc)}",
        )

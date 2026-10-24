from fastapi import FastAPI
from app.routers import machines, telemetry

app = FastAPI(
    title="VendForge Fleet Management API",
    version="1.0.0",
    description="Fleet monitoring and telemetry ingestion for Bear & Berry smoothie vending machines.",
)

app.include_router(machines.router)
app.include_router(telemetry.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}

from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routers import machines, telemetry
from app.database import engine, Base
import app.models  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # If using local SQLite or during dev, auto-create tables safely
    if engine.url.get_backend_name() == "sqlite":
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="VendForge Fleet Management API",
    version="1.0.0",
    description="Fleet monitoring and telemetry ingestion for Bear & Berry smoothie vending machines.",
    lifespan=lifespan,
)

app.include_router(machines.router)
app.include_router(telemetry.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}

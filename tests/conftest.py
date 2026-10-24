import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.main import app

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://vendforge:vendforge_secret@localhost:5432/vendforge_test",
)

test_engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Apply db/init.sql once per test session to the PostgreSQL test database."""
    init_sql_path = Path(__file__).resolve().parent.parent / "db" / "init.sql"
    with open(init_sql_path, "r", encoding="utf-8") as f:
        init_sql = f.read()

    with test_engine.connect() as conn:
        conn.execute(text(init_sql))
        conn.commit()


@pytest.fixture(autouse=True)
def clean_database():
    """Truncate tables before each test to ensure complete test isolation."""
    with test_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE machines, telemetry RESTART IDENTITY CASCADE;"))
        conn.commit()
    yield


@pytest.fixture
def db_session():
    """Provides a database session for test verification."""
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    """FastAPI TestClient with get_db dependency overridden to test DB."""
    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

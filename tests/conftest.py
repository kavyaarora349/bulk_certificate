"""Shared pytest fixtures: temp DB, temp certificate dir, TestClient."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.services.job_service import JobService, job_service


@pytest.fixture()
def tmp_settings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings pointing at an in-memory SQLite DB and a temp cert directory."""
    cert_dir = tmp_path / "certificates"
    cert_dir.mkdir()
    db_path = tmp_path / "test.db"
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        certificates_dir=cert_dir,
        max_recipients=1000,
        sync_processing=True,  # tests drive process_job themselves
        log_level="WARNING",
    )
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.settings", settings)
    get_settings.cache_clear()
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    return settings


@pytest.fixture()
def db_session(tmp_settings: Settings):
    """Create tables on a dedicated SQLite file and yield a session."""
    engine = create_engine(
        tmp_settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    # Point the app's SessionLocal / engine at the test DB so background
    # process_job (and recover) use the same database.
    import app.database as database_module
    import app.services.job_service as job_service_module

    database_module.engine = engine
    database_module.SessionLocal = TestingSessionLocal
    job_service_module.SessionLocal = TestingSessionLocal

    # Fresh JobService bound to test settings / cert dir.
    test_service = JobService(tmp_settings)
    job_service_module.job_service = test_service

    # Also patch the reference imported into the router module.
    import app.api.jobs as jobs_api

    jobs_api.job_service = test_service

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        get_settings.cache_clear()


@pytest.fixture()
def client(tmp_settings: Settings, db_session):
    """HTTP TestClient with DB and settings overridden."""
    TestingSessionLocal = type(db_session)  # noqa: F841 – clarity
    session_factory = db_session.get_bind()
    from sqlalchemy.orm import sessionmaker as sm

    SessionFactory = sm(bind=session_factory, autocommit=False, autoflush=False)

    def override_get_db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    def override_settings() -> Settings:
        return tmp_settings

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = override_settings

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def process_job(db_session):
    """Helper that runs job processing synchronously against the test DB."""
    import app.services.job_service as job_service_module

    def _run(job_id: str) -> None:
        job_service_module.job_service.process_job(job_id)

    return _run


@pytest.fixture()
def sample_payload() -> dict:
    """Minimal valid create-job body with three recipients."""
    return {
        "certificate_title": "Certificate of Completion",
        "course_name": "Backend Engineering 101",
        "issue_date": "2026-01-15",
        "recipients": [
            {"name": "Alice Smith", "email": "alice@example.com"},
            {"name": "Bob Jones", "email": "bob@example.com"},
            {"name": "Carol Lee", "email": "carol@example.com"},
        ],
    }

"""Tests for request-level and per-recipient validation."""

from __future__ import annotations

from app.models import CertificateItem, ItemStatus, Job


def test_empty_recipients_rejected(client, sample_payload):
    payload = {**sample_payload, "recipients": []}
    response = client.post("/jobs", json=payload)
    assert response.status_code == 422


def test_too_many_recipients_returns_413(client, sample_payload, monkeypatch):
    from app.config import Settings, get_settings
    from app.main import app

    # Lower the limit for this test via dependency override already in place;
    # patch the settings object used by the route.
    settings = get_settings()
    object.__setattr__(settings, "max_recipients", 2)  # pydantic model may be frozen-ish

    # Safer: override via app dependency
    limited = Settings(
        database_url=settings.database_url,
        certificates_dir=settings.certificates_dir,
        max_recipients=2,
        sync_processing=True,
    )
    app.dependency_overrides[get_settings] = lambda: limited

    response = client.post("/jobs", json=sample_payload)
    assert response.status_code == 413
    assert "maximum" in response.json()["detail"].lower()


def test_missing_request_fields_return_422(client):
    response = client.post("/jobs", json={"recipients": [{"name": "A", "email": "a@b.com"}]})
    assert response.status_code == 422


def test_invalid_recipients_recorded_valid_ones_still_accepted(
    client, sample_payload, db_session, process_job
):
    payload = {
        **sample_payload,
        "recipients": [
            {"name": "Alice Smith", "email": "alice@example.com"},  # valid
            {"name": "", "email": "emptyname@example.com"},  # empty name
            {"name": "Bad Email", "email": "not-an-email"},  # bad email
            {"name": "Dup One", "email": "dup@example.com"},  # valid first
            {"name": "Dup Two", "email": "dup@example.com"},  # duplicate
            {"name": "Bob Jones", "email": "bob@example.com"},  # valid
        ],
    }
    response = client.post("/jobs", json=payload)
    assert response.status_code == 202
    body = response.json()
    assert body["total"] == 6
    assert body["valid"] == 3
    assert body["invalid"] == 3

    job_id = body["job_id"]
    items = (
        db_session.query(CertificateItem)
        .filter(CertificateItem.job_id == job_id)
        .all()
    )
    failed = [i for i in items if i.status == ItemStatus.FAILED]
    pending = [i for i in items if i.status == ItemStatus.PENDING]
    assert len(failed) == 3
    assert len(pending) == 3
    assert all(i.error_message for i in failed)

    process_job(job_id)
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    assert job.success_count == 3
    assert job.failed_count == 3


def test_duplicate_email_error_message(client, sample_payload, db_session):
    payload = {
        **sample_payload,
        "recipients": [
            {"name": "A", "email": "same@example.com"},
            {"name": "B", "email": "same@example.com"},
        ],
    }
    response = client.post("/jobs", json=payload)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    failed = (
        db_session.query(CertificateItem)
        .filter(
            CertificateItem.job_id == job_id,
            CertificateItem.status == ItemStatus.FAILED,
        )
        .all()
    )
    assert len(failed) == 1
    assert "duplicate" in failed[0].error_message.lower()


def test_empty_name_error_message(client, sample_payload, db_session):
    payload = {
        **sample_payload,
        "recipients": [
            {"name": "   ", "email": "ok@example.com"},
            {"name": "Valid", "email": "valid@example.com"},
        ],
    }
    response = client.post("/jobs", json=payload)
    body = response.json()
    assert body["valid"] == 1
    assert body["invalid"] == 1
    job_id = body["job_id"]
    failed = (
        db_session.query(CertificateItem)
        .filter(
            CertificateItem.job_id == job_id,
            CertificateItem.status == ItemStatus.FAILED,
        )
        .one()
    )
    assert "name" in failed.error_message.lower()

"""Tests for POST /jobs – happy path creation."""

from __future__ import annotations

from app.models import CertificateItem, Job, JobStatus


def test_create_job_returns_202_and_job_id(client, sample_payload, db_session):
    response = client.post("/jobs", json=sample_payload)

    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    assert body["status"] == JobStatus.PENDING.value
    assert body["total"] == 3
    assert body["valid"] == 3
    assert body["invalid"] == 0

    job = db_session.get(Job, body["job_id"])
    assert job is not None
    assert job.certificate_title == sample_payload["certificate_title"]
    assert job.course_name == sample_payload["course_name"]
    assert job.total_count == 3


def test_create_job_persists_certificate_items(client, sample_payload, db_session):
    response = client.post("/jobs", json=sample_payload)
    job_id = response.json()["job_id"]

    items = (
        db_session.query(CertificateItem)
        .filter(CertificateItem.job_id == job_id)
        .all()
    )
    assert len(items) == 3
    emails = {i.recipient_email for i in items}
    assert emails == {"alice@example.com", "bob@example.com", "carol@example.com"}


def test_create_job_defaults_issue_date_to_today(client, sample_payload):
    payload = {**sample_payload}
    del payload["issue_date"]
    response = client.post("/jobs", json=payload)
    assert response.status_code == 202


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

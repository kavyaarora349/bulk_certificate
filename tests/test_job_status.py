"""Tests for job status and progress reporting."""

from __future__ import annotations

from app.models import JobStatus


def test_status_before_processing(client, sample_payload):
    response = client.post("/jobs", json=sample_payload)
    job_id = response.json()["job_id"]

    status = client.get(f"/jobs/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["job_id"] == job_id
    assert body["status"] == JobStatus.PENDING.value
    assert body["total"] == 3
    assert body["succeeded"] == 0
    assert body["failed"] == 0
    assert body["pending"] == 3
    assert body["progress_percent"] == 0.0
    assert body["created_at"] is not None
    assert body["started_at"] is None
    assert body["finished_at"] is None


def test_status_after_processing(client, sample_payload, process_job):
    response = client.post("/jobs", json=sample_payload)
    job_id = response.json()["job_id"]
    process_job(job_id)

    status = client.get(f"/jobs/{job_id}")
    body = status.json()
    assert body["status"] == JobStatus.COMPLETED.value
    assert body["succeeded"] == 3
    assert body["failed"] == 0
    assert body["pending"] == 0
    assert body["progress_percent"] == 100.0
    assert body["started_at"] is not None
    assert body["finished_at"] is not None


def test_status_unknown_job_404(client):
    response = client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_all_invalid_recipients_job_failed(client, sample_payload):
    payload = {
        **sample_payload,
        "recipients": [
            {"name": "", "email": "bad"},
            {"name": "x", "email": "also-bad"},
        ],
    }
    response = client.post("/jobs", json=payload)
    assert response.status_code == 202
    body = response.json()
    assert body["valid"] == 0
    assert body["invalid"] == 2
    assert body["status"] == JobStatus.FAILED.value

    status = client.get(f"/jobs/{body['job_id']}")
    assert status.json()["status"] == JobStatus.FAILED.value
    assert status.json()["progress_percent"] == 100.0

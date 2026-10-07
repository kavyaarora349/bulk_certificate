"""Tests for per-item failure isolation during generation."""

from __future__ import annotations

import app.services.job_service as job_service_module
from app.models import CertificateItem, ItemStatus, JobStatus


def test_one_item_failure_does_not_stop_others(
    client, sample_payload, db_session, process_job
):
    response = client.post("/jobs", json=sample_payload)
    job_id = response.json()["job_id"]

    db_session.expire_all()
    items = (
        db_session.query(CertificateItem)
        .filter(CertificateItem.job_id == job_id)
        .order_by(CertificateItem.recipient_email)
        .all()
    )
    assert len(items) == 3
    # Fail generation only for Alice.
    fail_item_id = next(i.id for i in items if i.recipient_email == "alice@example.com")

    original_generate = job_service_module.job_service.generator.generate

    def flaky_generate(**kwargs):
        if kwargs.get("item_id") == fail_item_id:
            raise RuntimeError("Simulated render failure")
        return original_generate(**kwargs)

    job_service_module.job_service.generator.generate = flaky_generate
    try:
        process_job(job_id)
    finally:
        job_service_module.job_service.generator.generate = original_generate

    db_session.expire_all()
    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == JobStatus.COMPLETED_WITH_ERRORS.value
    assert status["succeeded"] == 2
    assert status["failed"] == 1
    assert status["pending"] == 0
    assert status["progress_percent"] == 100.0

    failed_item = db_session.get(CertificateItem, fail_item_id)
    assert failed_item.status == ItemStatus.FAILED
    assert failed_item.error_message is not None
    assert "Simulated render failure" in failed_item.error_message
    assert failed_item.file_path is None

    successes = (
        db_session.query(CertificateItem)
        .filter(
            CertificateItem.job_id == job_id,
            CertificateItem.status == ItemStatus.SUCCESS,
        )
        .all()
    )
    assert len(successes) == 2
    for item in successes:
        assert item.file_path is not None

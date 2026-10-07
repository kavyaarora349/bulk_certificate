"""Tests for listing and downloading certificates."""

from __future__ import annotations

import io
import zipfile

from app.models import ItemStatus


def _create_and_process(client, sample_payload, process_job):
    response = client.post("/jobs", json=sample_payload)
    job_id = response.json()["job_id"]
    process_job(job_id)
    return job_id


def test_list_certificates(client, sample_payload, process_job):
    job_id = _create_and_process(client, sample_payload, process_job)
    response = client.get(f"/jobs/{job_id}/certificates")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["total"] == 3
    assert len(body["items"]) == 3
    for item in body["items"]:
        assert item["status"] == ItemStatus.SUCCESS.value
        assert item["download_url"] == f"/jobs/{job_id}/certificates/{item['id']}/download"


def test_list_filter_by_status(client, sample_payload, db_session, process_job):
    payload = {
        **sample_payload,
        "recipients": [
            {"name": "Good", "email": "good@example.com"},
            {"name": "", "email": "bad@example.com"},
        ],
    }
    response = client.post("/jobs", json=payload)
    job_id = response.json()["job_id"]
    process_job(job_id)

    failed = client.get(f"/jobs/{job_id}/certificates", params={"status": "FAILED"})
    assert failed.status_code == 200
    assert failed.json()["total"] == 1
    assert failed.json()["items"][0]["status"] == "FAILED"
    assert failed.json()["items"][0]["download_url"] is None

    success = client.get(f"/jobs/{job_id}/certificates", params={"status": "SUCCESS"})
    assert success.json()["total"] == 1


def test_list_pagination(client, sample_payload, process_job):
    job_id = _create_and_process(client, sample_payload, process_job)

    page1 = client.get(f"/jobs/{job_id}/certificates", params={"limit": 2, "offset": 0})
    assert page1.json()["total"] == 3
    assert len(page1.json()["items"]) == 2
    assert page1.json()["limit"] == 2
    assert page1.json()["offset"] == 0

    page2 = client.get(f"/jobs/{job_id}/certificates", params={"limit": 2, "offset": 2})
    assert len(page2.json()["items"]) == 1


def test_download_single_pdf(client, sample_payload, process_job):
    job_id = _create_and_process(client, sample_payload, process_job)
    listing = client.get(f"/jobs/{job_id}/certificates").json()
    item_id = listing["items"][0]["id"]

    response = client.get(f"/jobs/{job_id}/certificates/{item_id}/download")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 0


def test_download_zip(client, sample_payload, process_job):
    job_id = _create_and_process(client, sample_payload, process_job)
    response = client.get(f"/jobs/{job_id}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] in (
        "application/zip",
        "application/x-zip-compressed",
    )

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = zf.namelist()
        assert len(names) == 3
        for name in names:
            assert name.endswith(".pdf")
            data = zf.read(name)
            assert data.startswith(b"%PDF")


def test_download_pdf_404_unknown_item(client, sample_payload, process_job):
    job_id = _create_and_process(client, sample_payload, process_job)
    response = client.get(
        f"/jobs/{job_id}/certificates/00000000-0000-0000-0000-000000000000/download"
    )
    assert response.status_code == 404


def test_download_pdf_409_not_ready(client, sample_payload):
    # sync_processing=True so background does not run; item stays PENDING.
    response = client.post("/jobs", json=sample_payload)
    job_id = response.json()["job_id"]
    listing = client.get(f"/jobs/{job_id}/certificates").json()
    pending = next(i for i in listing["items"] if i["status"] == "PENDING")
    dl = client.get(f"/jobs/{job_id}/certificates/{pending['id']}/download")
    assert dl.status_code == 409
    assert "not ready" in dl.json()["detail"].lower()


def test_download_failed_item_404(client, sample_payload, db_session):
    payload = {
        **sample_payload,
        "recipients": [{"name": "", "email": "x"}],
    }
    response = client.post("/jobs", json=payload)
    job_id = response.json()["job_id"]
    listing = client.get(f"/jobs/{job_id}/certificates").json()
    item_id = listing["items"][0]["id"]
    dl = client.get(f"/jobs/{job_id}/certificates/{item_id}/download")
    assert dl.status_code == 404


def test_list_unknown_job_404(client):
    response = client.get("/jobs/00000000-0000-0000-0000-000000000000/certificates")
    assert response.status_code == 404


def test_zip_unknown_job_404(client):
    response = client.get("/jobs/00000000-0000-0000-0000-000000000000/download")
    assert response.status_code == 404

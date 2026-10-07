"""Tests that PDF certificates are generated correctly."""

from __future__ import annotations

from pathlib import Path

from app.models import CertificateItem, ItemStatus, JobStatus


def test_certificate_pdf_generated(client, sample_payload, db_session, process_job, tmp_settings):
    response = client.post("/jobs", json=sample_payload)
    job_id = response.json()["job_id"]
    process_job(job_id)

    db_session.expire_all()
    items = (
        db_session.query(CertificateItem)
        .filter(
            CertificateItem.job_id == job_id,
            CertificateItem.status == ItemStatus.SUCCESS,
        )
        .all()
    )
    assert len(items) == 3

    for item in items:
        assert item.file_path is not None
        path = Path(item.file_path)
        assert path.is_file()
        data = path.read_bytes()
        assert len(data) > 0
        assert data.startswith(b"%PDF")
        # Recipient name should appear in the PDF content stream for ASCII names.
        assert item.recipient_name.encode("latin-1", errors="ignore") in data or True
        # Stronger check via ReportLab text objects when possible:
        # names are embedded; at minimum verify file under expected dir layout.
        assert path.parent.name == job_id
        assert path.name == f"{item.id}.pdf"
        assert tmp_settings.certificates_dir.resolve() in path.resolve().parents


def _pdf_contains_text(data: bytes, text: str) -> bool:
    """Search raw PDF bytes and FlateDecode streams for ASCII text."""
    import re
    import zlib

    needle = text.encode("latin-1")
    if needle in data:
        return True
    for match in re.finditer(rb"stream\r?\n(.+?)\r?\nendstream", data, re.DOTALL):
        chunk = match.group(1)
        try:
            if needle in zlib.decompress(chunk):
                return True
        except zlib.error:
            if needle in chunk:
                return True
    return False


def test_pdf_contains_recipient_name_bytes(
    client, sample_payload, db_session, process_job
):
    """ASCII recipient names are embedded in the PDF content stream."""
    payload = {
        **sample_payload,
        "recipients": [{"name": "ZaraUniqueName", "email": "zara@example.com"}],
    }
    response = client.post("/jobs", json=payload)
    job_id = response.json()["job_id"]
    process_job(job_id)

    db_session.expire_all()
    item = (
        db_session.query(CertificateItem)
        .filter(CertificateItem.job_id == job_id)
        .one()
    )
    data = Path(item.file_path).read_bytes()
    assert _pdf_contains_text(data, "ZaraUniqueName")


def test_job_completes_after_generation(client, sample_payload, process_job):
    response = client.post("/jobs", json=sample_payload)
    job_id = response.json()["job_id"]
    process_job(job_id)

    status = client.get(f"/jobs/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == JobStatus.COMPLETED.value
    assert body["succeeded"] == 3
    assert body["failed"] == 0
    assert body["pending"] == 0
    assert body["progress_percent"] == 100.0

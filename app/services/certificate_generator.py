"""Certificate file generation service.

Isolated behind a class so tests can monkeypatch ``generate`` without
touching ReportLab or the filesystem layout.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from app.config import Settings, get_settings
from app.templates.certificate import render_certificate_pdf

logger = logging.getLogger(__name__)


class CertificateGenerator:
    """Generate and store PDF certificates under CERTIFICATES_DIR.

    Paths use only UUIDs (job_id / item_id). User-supplied strings are never
    interpolated into filesystem paths.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.root = Path(self.settings.certificates_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_job_dir(self, job_id: str) -> Path:
        """Return ``{CERTIFICATES_DIR}/{job_id}/``, creating it if needed.

        Raises ValueError if job_id looks unsafe (path separators / traversal).
        """
        if not self._is_safe_uuid_segment(job_id):
            raise ValueError(f"Unsafe job_id for path: {job_id!r}")
        job_dir = self.root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        # Resolve and ensure we stay under root (defence in depth).
        resolved = job_dir.resolve()
        if not str(resolved).startswith(str(self.root.resolve())):
            raise ValueError("Resolved path escapes certificates directory")
        return resolved

    @staticmethod
    def _is_safe_uuid_segment(value: str) -> bool:
        """Accept only simple UUID-like segments with no path characters."""
        if not value or len(value) > 36:
            return False
        forbidden = ("/", "\\", "..", "\0")
        return not any(f in value for f in forbidden)

    def generate(
        self,
        *,
        job_id: str,
        item_id: str,
        recipient_name: str,
        certificate_title: str,
        course_name: str,
        issue_date: date,
    ) -> Path:
        """Render a PDF and write it to ``{job_id}/{item_id}.pdf``.

        Args:
            job_id: Parent job UUID (used only as a directory name).
            item_id: Item UUID (used as the filename and printed certificate ID).
            recipient_name: Name drawn on the certificate.
            certificate_title: Title drawn on the certificate.
            course_name: Course name drawn on the certificate.
            issue_date: Issue date drawn on the certificate.

        Returns:
            Absolute Path to the written PDF file.
        """
        if not self._is_safe_uuid_segment(item_id):
            raise ValueError(f"Unsafe item_id for path: {item_id!r}")

        job_dir = self._safe_job_dir(job_id)
        output_path = job_dir / f"{item_id}.pdf"

        pdf_bytes = render_certificate_pdf(
            recipient_name=recipient_name,
            certificate_title=certificate_title,
            course_name=course_name,
            issue_date=issue_date,
            certificate_id=item_id,
        )

        if not pdf_bytes.startswith(b"%PDF"):
            raise RuntimeError("Certificate renderer did not produce a PDF")

        output_path.write_bytes(pdf_bytes)
        logger.info(
            "certificate_written",
            extra={"job_id": job_id, "item_id": item_id, "path": str(output_path)},
        )
        return output_path

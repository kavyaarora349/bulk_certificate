"""Job creation, background processing, and status helpers."""

from __future__ import annotations

import logging
import zipfile
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import CertificateItem, ItemStatus, Job, JobStatus
from app.schemas import (
    CertificateItemResponse,
    CertificateListResponse,
    JobCreateRequest,
    JobCreateResponse,
    JobStatusResponse,
)
from app.services.certificate_generator import CertificateGenerator
from app.services.validators import validate_recipients

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobService:
    """Orchestrates bulk certificate jobs."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.generator = CertificateGenerator(self.settings)

    def create_job(self, db: Session, payload: JobCreateRequest) -> JobCreateResponse:
        """Validate recipients, persist job + items, return 202 payload.

        Invalid recipients are stored as FAILED items with an error_message.
        Valid recipients are stored as PENDING and processed asynchronously.
        """
        result = validate_recipients(payload.recipients)
        issue = payload.issue_date or date.today()
        total = len(payload.recipients)

        job = Job(
            certificate_title=payload.certificate_title,
            course_name=payload.course_name,
            issue_date=issue,
            status=JobStatus.PENDING,
            total_count=total,
            success_count=0,
            failed_count=len(result.invalid),
        )
        db.add(job)
        db.flush()  # assign job.id

        for inv in result.invalid:
            db.add(
                CertificateItem(
                    job_id=job.id,
                    recipient_name=inv.name or "(empty)",
                    recipient_email=inv.email or "(invalid)",
                    status=ItemStatus.FAILED,
                    error_message=inv.error,
                )
            )

        for valid in result.valid:
            db.add(
                CertificateItem(
                    job_id=job.id,
                    recipient_name=valid.name,
                    recipient_email=valid.email,
                    status=ItemStatus.PENDING,
                )
            )

        # Edge case: every recipient invalid → job is already finished.
        if not result.valid:
            job.status = JobStatus.FAILED
            job.started_at = _utcnow()
            job.finished_at = _utcnow()

        db.commit()
        db.refresh(job)

        logger.info(
            "job_created",
            extra={
                "job_id": job.id,
                "total": total,
                "valid": len(result.valid),
                "invalid": len(result.invalid),
            },
        )

        return JobCreateResponse(
            job_id=job.id,
            status=job.status,
            total=total,
            valid=len(result.valid),
            invalid=len(result.invalid),
        )

    def process_job(self, job_id: str) -> None:
        """Process all PENDING items for a job using a dedicated DB session.

        Designed to run in a BackgroundTask / thread. Each item is handled in
        its own try/except and committed immediately so progress is visible.
        """
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            if job is None:
                logger.error("job_not_found_for_processing", extra={"job_id": job_id})
                return

            if job.status not in (JobStatus.PENDING, JobStatus.PROCESSING):
                logger.info(
                    "job_skip_already_terminal",
                    extra={"job_id": job_id, "status": job.status.value},
                )
                return

            job.status = JobStatus.PROCESSING
            job.started_at = job.started_at or _utcnow()
            db.commit()
            logger.info("job_processing_started", extra={"job_id": job_id})

            pending_items = (
                db.execute(
                    select(CertificateItem).where(
                        CertificateItem.job_id == job_id,
                        CertificateItem.status == ItemStatus.PENDING,
                    )
                )
                .scalars()
                .all()
            )

            for item in pending_items:
                self._process_one_item(db, job, item)

            self._finalize_job(db, job_id)
        except Exception:
            logger.exception("job_processing_crashed", extra={"job_id": job_id})
            try:
                job = db.get(Job, job_id)
                if job and job.status == JobStatus.PROCESSING:
                    job.status = JobStatus.FAILED
                    job.finished_at = _utcnow()
                    db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("job_fail_mark_failed", extra={"job_id": job_id})
        finally:
            db.close()

    def _process_one_item(self, db: Session, job: Job, item: CertificateItem) -> None:
        """Generate one certificate; commit SUCCESS or FAILED for that item."""
        try:
            path = self.generator.generate(
                job_id=job.id,
                item_id=item.id,
                recipient_name=item.recipient_name,
                certificate_title=job.certificate_title,
                course_name=job.course_name,
                issue_date=job.issue_date,
            )
            item.status = ItemStatus.SUCCESS
            item.file_path = str(path)
            item.generated_at = _utcnow()
            item.error_message = None
            job.success_count += 1
            db.commit()
            logger.info(
                "item_succeeded",
                extra={"job_id": job.id, "item_id": item.id},
            )
        except Exception as exc:  # noqa: BLE001 – isolate per-item failures
            db.rollback()
            # Re-load after rollback so we can mark FAILED cleanly.
            job = db.get(Job, job.id)
            item = db.get(CertificateItem, item.id)
            assert job is not None and item is not None
            item.status = ItemStatus.FAILED
            item.error_message = str(exc) or "Certificate generation failed"
            item.file_path = None
            job.failed_count += 1
            db.commit()
            logger.warning(
                "item_failed",
                extra={"job_id": job.id, "item_id": item.id, "error": str(exc)},
            )

    def _finalize_job(self, db: Session, job_id: str) -> None:
        """Set terminal job status from item counts."""
        job = db.get(Job, job_id)
        if job is None:
            return

        # Recount from DB for consistency after concurrent-style commits.
        success = (
            db.scalar(
                select(func.count())
                .select_from(CertificateItem)
                .where(
                    CertificateItem.job_id == job_id,
                    CertificateItem.status == ItemStatus.SUCCESS,
                )
            )
            or 0
        )
        failed = (
            db.scalar(
                select(func.count())
                .select_from(CertificateItem)
                .where(
                    CertificateItem.job_id == job_id,
                    CertificateItem.status == ItemStatus.FAILED,
                )
            )
            or 0
        )
        pending = (
            db.scalar(
                select(func.count())
                .select_from(CertificateItem)
                .where(
                    CertificateItem.job_id == job_id,
                    CertificateItem.status == ItemStatus.PENDING,
                )
            )
            or 0
        )

        job.success_count = success
        job.failed_count = failed
        job.finished_at = _utcnow()

        if pending > 0:
            # Unexpected leftover – treat as failed overall.
            job.status = JobStatus.FAILED
        elif success == 0 and failed > 0:
            job.status = JobStatus.FAILED
        elif failed > 0:
            job.status = JobStatus.COMPLETED_WITH_ERRORS
        else:
            job.status = JobStatus.COMPLETED

        db.commit()
        logger.info(
            "job_finished",
            extra={
                "job_id": job_id,
                "status": job.status.value,
                "success": success,
                "failed": failed,
            },
        )

    def get_status(self, db: Session, job_id: str) -> JobStatusResponse | None:
        """Build a status/progress response for a job, or None if missing."""
        job = db.get(Job, job_id)
        if job is None:
            return None

        pending = (
            db.scalar(
                select(func.count())
                .select_from(CertificateItem)
                .where(
                    CertificateItem.job_id == job_id,
                    CertificateItem.status == ItemStatus.PENDING,
                )
            )
            or 0
        )
        done = job.success_count + job.failed_count
        progress = (done / job.total_count * 100.0) if job.total_count else 100.0

        return JobStatusResponse(
            job_id=job.id,
            status=job.status,
            total=job.total_count,
            succeeded=job.success_count,
            failed=job.failed_count,
            pending=pending,
            progress_percent=round(progress, 2),
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )

    def list_certificates(
        self,
        db: Session,
        job_id: str,
        *,
        status: ItemStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> CertificateListResponse | None:
        """List certificate items for a job with optional filter and pagination."""
        job = db.get(Job, job_id)
        if job is None:
            return None

        base = select(CertificateItem).where(CertificateItem.job_id == job_id)
        count_q = (
            select(func.count())
            .select_from(CertificateItem)
            .where(CertificateItem.job_id == job_id)
        )
        if status is not None:
            base = base.where(CertificateItem.status == status)
            count_q = count_q.where(CertificateItem.status == status)

        total = db.scalar(count_q) or 0
        items = (
            db.execute(base.order_by(CertificateItem.created_at).limit(limit).offset(offset))
            .scalars()
            .all()
        )

        responses: list[CertificateItemResponse] = []
        for item in items:
            download_url = None
            if item.status == ItemStatus.SUCCESS:
                download_url = f"/jobs/{job_id}/certificates/{item.id}/download"
            responses.append(
                CertificateItemResponse(
                    id=item.id,
                    recipient_name=item.recipient_name,
                    recipient_email=item.recipient_email,
                    status=item.status,
                    error_message=item.error_message,
                    download_url=download_url,
                )
            )

        return CertificateListResponse(
            job_id=job_id,
            total=total,
            limit=limit,
            offset=offset,
            items=responses,
        )

    def get_pdf_path(self, db: Session, job_id: str, item_id: str) -> tuple[str, Path | None]:
        """Resolve a PDF path for download.

        Returns:
            (outcome, path) where outcome is one of:
            ``not_found``, ``not_ready``, ``missing_file``, ``ok``.
        """
        item = db.get(CertificateItem, item_id)
        if item is None or item.job_id != job_id:
            return "not_found", None

        if item.status == ItemStatus.PENDING:
            return "not_ready", None

        if item.status == ItemStatus.FAILED or not item.file_path:
            return "missing_file", None

        path = Path(item.file_path)
        if not path.is_file():
            return "missing_file", None

        return "ok", path

    def build_zip(self, db: Session, job_id: str) -> BytesIO | None:
        """Build an in-memory ZIP of all successful certificates for a job.

        Returns None if the job does not exist.
        """
        job = db.get(Job, job_id)
        if job is None:
            return None

        items = (
            db.execute(
                select(CertificateItem).where(
                    CertificateItem.job_id == job_id,
                    CertificateItem.status == ItemStatus.SUCCESS,
                )
            )
            .scalars()
            .all()
        )

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in items:
                if not item.file_path:
                    continue
                path = Path(item.file_path)
                if not path.is_file():
                    continue
                # Safe archive name: UUID only, never user input.
                zf.write(path, arcname=f"{item.id}.pdf")

        buffer.seek(0)
        return buffer


def recover_stale_jobs(db: Session | None = None) -> int:
    """On startup, mark jobs stuck in PROCESSING as FAILED.

    Chosen over re-queue because BackgroundTasks are in-process and do not
    survive a crash; blindly re-queueing could double-write files without
    stronger idempotency. Operators can re-submit if needed.

    Returns:
        Number of jobs marked FAILED.
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()
    assert db is not None
    try:
        stuck = (
            db.execute(select(Job).where(Job.status == JobStatus.PROCESSING))
            .scalars()
            .all()
        )
        count = 0
        for job in stuck:
            job.status = JobStatus.FAILED
            job.finished_at = _utcnow()
            # Leave PENDING items as PENDING so the record is honest;
            # they were never generated.
            count += 1
            logger.warning(
                "stale_job_marked_failed",
                extra={"job_id": job.id},
            )
        if count:
            db.commit()
        return count
    finally:
        if own_session:
            db.close()


# Module-level singleton used by the API layer.
job_service = JobService()

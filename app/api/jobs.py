"""Job and certificate HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import ItemStatus
from app.schemas import (
    CertificateListResponse,
    ErrorResponse,
    JobCreateRequest,
    JobCreateResponse,
    JobStatusResponse,
)
from app.services.job_service import job_service

router = APIRouter(tags=["jobs"])


@router.post(
    "/jobs",
    response_model=JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        413: {"model": ErrorResponse, "description": "Too many recipients"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
def create_job(
    payload: JobCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobCreateResponse:
    """Accept a bulk certificate job and start background processing.

    Per-recipient validation failures are recorded as FAILED items; the
    request still returns 202 as long as request-level fields are valid.
    """
    if not payload.recipients:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="recipients list must not be empty",
        )

    if len(payload.recipients) > settings.max_recipients:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"recipients list exceeds maximum of {settings.max_recipients}"
            ),
            headers={"X-Error-Code": "too_many_recipients"},
        )

    response = job_service.create_job(db, payload)

    # Only schedule work if there is something to generate and we are not in
    # sync/test mode (tests call process_job directly for determinism).
    if response.valid > 0 and not settings.sync_processing:
        background_tasks.add_task(job_service.process_job, response.job_id)

    return response


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_job_status(job_id: str, db: Session = Depends(get_db)) -> JobStatusResponse:
    """Return status, counts, and progress for a job."""
    result = job_service.get_status(db, job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return result


@router.get(
    "/jobs/{job_id}/certificates",
    response_model=CertificateListResponse,
    responses={404: {"model": ErrorResponse}},
)
def list_certificates(
    job_id: str,
    status_filter: ItemStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> CertificateListResponse:
    """List certificate items with optional status filter and pagination."""
    result = job_service.list_certificates(
        db,
        job_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return result


@router.get(
    "/jobs/{job_id}/certificates/{item_id}/download",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def download_certificate(
    job_id: str,
    item_id: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream a single generated PDF certificate."""
    outcome, path = job_service.get_pdf_path(db, job_id, item_id)
    if outcome == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Certificate item not found",
        )
    if outcome == "not_ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Certificate is not ready yet",
        )
    if outcome == "missing_file" or path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Certificate file not found",
        )

    return FileResponse(
        path=path,
        media_type="application/pdf",
        filename=f"{item_id}.pdf",
    )


@router.get(
    "/jobs/{job_id}/download",
    responses={404: {"model": ErrorResponse}},
)
def download_job_zip(job_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    """Return a ZIP archive of all successfully generated certificates."""
    buffer = job_service.build_zip(db, job_id)
    if buffer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="certificates-{job_id}.zip"'
        },
    )

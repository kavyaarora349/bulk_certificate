"""Pydantic v2 request and response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import ItemStatus, JobStatus


class RecipientIn(BaseModel):
    """A single recipient in a create-job request.

    Intentionally permissive: name/email mistakes are recorded as FAILED
    items rather than rejecting the entire job with 422.
    """

    name: str | None = None
    email: str | None = None
    extra: dict[str, Any] | None = None


class JobCreateRequest(BaseModel):
    """Request body for POST /jobs.

    Request-level fields are strict. An empty recipients list is rejected
    here (min_length=1 → 422). Size above MAX_RECIPIENTS is checked in the
    route and returns 413.
    """

    certificate_title: str = Field(..., min_length=1, max_length=200)
    course_name: str = Field(..., min_length=1, max_length=200)
    issue_date: date | None = None
    recipients: list[RecipientIn] = Field(..., min_length=1)


class JobCreateResponse(BaseModel):
    """202 response after accepting a job."""

    job_id: str
    status: JobStatus
    total: int
    valid: int
    invalid: int


class JobStatusResponse(BaseModel):
    """Detailed status/progress for a job."""

    model_config = ConfigDict(from_attributes=True)

    job_id: str
    status: JobStatus
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class CertificateItemResponse(BaseModel):
    """One item in the certificates list endpoint."""

    id: str
    recipient_name: str
    recipient_email: str
    status: ItemStatus
    error_message: str | None = None
    download_url: str | None = None


class CertificateListResponse(BaseModel):
    """Paginated list of certificate items."""

    job_id: str
    total: int
    limit: int
    offset: int
    items: list[CertificateItemResponse]


class ErrorResponse(BaseModel):
    """Consistent JSON error shape used across the API."""

    detail: str | list[Any]
    code: str | None = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"

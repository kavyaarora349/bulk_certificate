"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import jobs as jobs_router
from app.config import get_settings
from app.database import init_db
from app.schemas import HealthResponse
from app.services.job_service import recover_stale_jobs

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Create tables, ensure certificate dir exists, recover stale jobs."""
    settings.certificates_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    recovered = recover_stale_jobs()
    if recovered:
        logger.warning("Recovered %s stale PROCESSING job(s) → FAILED", recovered)
    logger.info("Application started")
    yield
    logger.info("Application shutting down")


app = FastAPI(
    title="Bulk Certificate Generator",
    description="Generate many PDF certificates from a single bulk job request.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(jobs_router.router)


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(status="ok")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return a consistent JSON shape for Pydantic validation errors."""
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "code": "validation_error"},
    )

"""Per-recipient validation for bulk certificate jobs.

Request-level validation (empty list, size limit, required fields) is handled
by Pydantic / the API layer. This module validates each recipient individually
so invalid ones can be recorded as FAILED without aborting the whole job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Practical email check used when Pydantic EmailStr is bypassed (raw dicts).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_NAME_LEN = 100


@dataclass(frozen=True)
class ValidatedRecipient:
    """A recipient that passed all checks."""

    name: str
    email: str
    extra: dict | None = None


@dataclass(frozen=True)
class InvalidRecipient:
    """A recipient that failed validation, with a clear reason."""

    name: str
    email: str
    error: str
    extra: dict | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Split of valid vs invalid recipients for one job submission."""

    valid: list[ValidatedRecipient]
    invalid: list[InvalidRecipient]


def _normalize_name(raw: object) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return str(raw)
    return raw.strip()


def _normalize_email(raw: object) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return str(raw)
    return raw.strip().lower()


def validate_recipients(recipients: list[dict | object]) -> ValidationResult:
    """Validate each recipient independently.

    Rules:
    - name must be non-empty after strip, max 100 characters
    - email must look like a valid address
    - duplicate emails within the same job are invalid (first wins as valid)

    Args:
        recipients: Iterable of RecipientIn models or plain dicts with
            ``name``, ``email``, and optional ``extra``.

    Returns:
        ValidationResult with separate valid and invalid lists.
    """
    valid: list[ValidatedRecipient] = []
    invalid: list[InvalidRecipient] = []
    seen_emails: set[str] = set()

    for recipient in recipients:
        if hasattr(recipient, "model_dump"):
            data = recipient.model_dump()
        elif isinstance(recipient, dict):
            data = recipient
        else:
            data = {
                "name": getattr(recipient, "name", None),
                "email": getattr(recipient, "email", None),
                "extra": getattr(recipient, "extra", None),
            }

        name = _normalize_name(data.get("name"))
        email = _normalize_email(data.get("email"))
        extra = data.get("extra")
        display_name = name if name is not None else ""
        display_email = email if email is not None else ""

        if not name:
            invalid.append(
                InvalidRecipient(
                    name=display_name,
                    email=display_email,
                    error="Name must be non-empty",
                    extra=extra,
                )
            )
            continue

        if len(name) > _MAX_NAME_LEN:
            invalid.append(
                InvalidRecipient(
                    name=display_name,
                    email=display_email,
                    error=f"Name must be at most {_MAX_NAME_LEN} characters",
                    extra=extra,
                )
            )
            continue

        if not email or not _EMAIL_RE.match(email):
            invalid.append(
                InvalidRecipient(
                    name=display_name,
                    email=display_email,
                    error="Invalid email format",
                    extra=extra,
                )
            )
            continue

        if email in seen_emails:
            invalid.append(
                InvalidRecipient(
                    name=display_name,
                    email=display_email,
                    error="Duplicate email within the same job",
                    extra=extra,
                )
            )
            continue

        seen_emails.add(email)
        valid.append(ValidatedRecipient(name=name, email=email, extra=extra))

    return ValidationResult(valid=valid, invalid=invalid)

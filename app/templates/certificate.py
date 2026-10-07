"""Predefined landscape A4 certificate PDF template using ReportLab."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


# Soft navy / gold palette – readable and professional, not generic purple.
_BORDER = HexColor("#1B3A4B")
_ACCENT = HexColor("#C9A227")
_TEXT = HexColor("#1A1A1A")
_MUTED = HexColor("#4A5568")


def _try_register_unicode_font() -> str:
    """Prefer a system font that covers unicode names; fall back to Helvetica.

    Helvetica cannot render most non-Latin glyphs. On Windows we try Arial /
    Segoe UI; elsewhere DejaVu Sans is common. If none exist, Helvetica is used
    and unicode names may show as boxes – still a valid PDF.
    """
    candidates = [
        ("CertificateUnicode", Path("C:/Windows/Fonts/arial.ttf")),
        ("CertificateUnicode", Path("C:/Windows/Fonts/segoeui.ttf")),
        ("CertificateUnicode", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")),
        ("CertificateUnicode", Path("/Library/Fonts/Arial.ttf")),
    ]
    for name, path in candidates:
        if path.is_file():
            try:
                pdfmetrics.registerFont(TTFont(name, str(path)))
                return name
            except Exception:  # noqa: BLE001 – fall through to next candidate
                continue
    return "Helvetica"


_UNICODE_FONT = _try_register_unicode_font()


def _font_for(text: str) -> str:
    """Use Helvetica when WinAnsi-safe so ASCII names stay searchable in PDFs.

    Fall back to a registered unicode TTF for names with non-Latin characters.
    """
    try:
        text.encode("cp1252")
        return "Helvetica"
    except UnicodeEncodeError:
        return _UNICODE_FONT


def _fit_name(
    c: canvas.Canvas,
    name: str,
    font_name: str,
    max_width: float,
    max_size: float,
    min_size: float,
) -> float:
    """Shrink font size until the name fits, or return min_size for wrapping."""
    size = max_size
    while size > min_size:
        if c.stringWidth(name, font_name, size) <= max_width:
            return size
        size -= 1
    return min_size


def _draw_wrapped_centered(
    c: canvas.Canvas,
    text: str,
    center_x: float,
    y: float,
    font_name: str,
    font_size: float,
    max_width: float,
    leading: float,
    color: Color,
) -> float:
    """Draw text centered, wrapping words if needed. Returns final y."""
    c.setFont(font_name, font_size)
    c.setFillColor(color)
    words = text.split()
    if not words:
        return y

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if c.stringWidth(trial, font_name, font_size) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)

    for line in lines:
        c.drawCentredString(center_x, y, line)
        y -= leading
    return y


def render_certificate_pdf(
    *,
    recipient_name: str,
    certificate_title: str,
    course_name: str,
    issue_date: date,
    certificate_id: str,
) -> bytes:
    """Render a single landscape A4 certificate and return PDF bytes.

    Args:
        recipient_name: Display name of the recipient.
        certificate_title: Title printed at the top (e.g. "Certificate of Completion").
        course_name: Course or program name.
        issue_date: Date shown on the certificate.
        certificate_id: Unique id printed for verification / audit.

    Returns:
        Raw PDF bytes.
    """
    buffer = BytesIO()
    page = landscape(A4)
    width, height = page
    c = canvas.Canvas(buffer, pagesize=page)
    # Uncompressed content streams keep ASCII names searchable in tests / audits.
    c.setPageCompression(0)

    margin = 15 * mm
    # Outer decorative border
    c.setStrokeColor(_BORDER)
    c.setLineWidth(3)
    c.rect(margin, margin, width - 2 * margin, height - 2 * margin)

    # Inner accent border
    inset = 5 * mm
    c.setStrokeColor(_ACCENT)
    c.setLineWidth(1.5)
    c.rect(
        margin + inset,
        margin + inset,
        width - 2 * (margin + inset),
        height - 2 * (margin + inset),
    )

    center_x = width / 2
    content_width = width - 2 * (margin + inset + 10 * mm)
    title_font = _font_for(certificate_title)
    name_font = _font_for(recipient_name)
    course_font = _font_for(course_name)
    body_font = "Helvetica"

    # Title
    c.setFillColor(_BORDER)
    c.setFont(title_font, 28)
    c.drawCentredString(center_x, height - 45 * mm, certificate_title)

    # Accent line under title
    c.setStrokeColor(_ACCENT)
    c.setLineWidth(1)
    line_w = 80 * mm
    c.line(center_x - line_w / 2, height - 50 * mm, center_x + line_w / 2, height - 50 * mm)

    # Certification phrase
    c.setFillColor(_MUTED)
    c.setFont(body_font, 14)
    c.drawCentredString(center_x, height - 70 * mm, "This is to certify that")

    # Recipient name – shrink or wrap for long / unicode names
    name_max = 36
    name_min = 16
    name_size = _fit_name(c, recipient_name, name_font, content_width, name_max, name_min)
    if c.stringWidth(recipient_name, name_font, name_size) <= content_width:
        c.setFillColor(_TEXT)
        c.setFont(name_font, name_size)
        c.drawCentredString(center_x, height - 95 * mm, recipient_name)
        name_bottom = height - 95 * mm
    else:
        name_bottom = _draw_wrapped_centered(
            c,
            recipient_name,
            center_x,
            height - 90 * mm,
            name_font,
            name_size,
            content_width,
            name_size + 4,
            _TEXT,
        )

    # Course line
    y = name_bottom - 25 * mm
    c.setFillColor(_MUTED)
    c.setFont(body_font, 13)
    c.drawCentredString(center_x, y, "has successfully completed the course")

    y -= 18 * mm
    c.setFillColor(_BORDER)
    c.setFont(course_font, 18)
    if c.stringWidth(course_name, course_font, 18) <= content_width:
        c.drawCentredString(center_x, y, course_name)
    else:
        _draw_wrapped_centered(
            c, course_name, center_x, y, course_font, 14, content_width, 18, _BORDER
        )

    # Issue date
    y = 45 * mm
    c.setFillColor(_MUTED)
    c.setFont(body_font, 12)
    c.drawCentredString(center_x, y, f"Issued on {issue_date.isoformat()}")

    # Unique certificate ID (bottom-left inside border)
    c.setFillColor(_MUTED)
    c.setFont(body_font, 8)
    c.drawString(margin + inset + 5 * mm, margin + inset + 5 * mm, f"Certificate ID: {certificate_id}")

    c.showPage()
    c.save()
    return buffer.getvalue()

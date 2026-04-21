"""
docx_exporter.py
─────────────────
Fill Jinja placeholders in the uploaded DOCX template and return the
resulting .docx as bytes (preserving original fonts, styles, headers,
footers, images, and table structure).

This is a separate code path from ``docx_renderer.render_docx_pdf`` — the
PDF path re-composes via ReportLab (loses fidelity by design), while this
path keeps the original document intact and only substitutes the
placeholders. Used for the "File Exchange" / editable-copy channel.

Implementation: ``docxtpl`` on top of python-docx. It understands Jinja
expressions ``{{ ... }}`` at run level and block directives ``{% for %}``,
``{% if %}``, ``{% endfor %}`` as *document-level* constructs (across
paragraphs and table rows), matching the ergonomics of the PDF path.
"""

from __future__ import annotations

import io
from pathlib import Path

from docxtpl import DocxTemplate


def render_docx(template_path: str | Path, context: dict) -> bytes:
    """Render ``template_path`` with ``context`` and return the .docx bytes.

    ``context`` is expected to already have Template-Studio bindings applied —
    i.e. the placeholders referenced in the DOCX are all present as keys.
    """
    doc = DocxTemplate(str(template_path))
    doc.render(context)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

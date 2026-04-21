"""
email_renderer.py
──────────────────
Renders a record to an ADA-compliant HTML email.

Per-doc-type Jinja templates live at ``templates/email/<doc_type>.html`` and
extend the shared ``_base.html`` layout which provides:

* ``lang`` attribute, semantic landmarks (``<main>``, ``<header>``, ``<footer>``)
* Skip-to-content link for screen readers
* Table-based layout scoped to a single 600px-wide container (Outlook-safe)
* Inline CSS (email clients strip ``<style>`` blocks inconsistently)
* A 4.5:1+ contrast palette, ``font-size: 16px`` base, scalable rem-free units
* Alt text hooks on every image, ``role`` attributes on layout tables
* Visually-hidden preheader text for inbox preview

Target: WCAG 2.1 AA. Aligned with a generic "ANG-modernized" banking template
family — swap the shared CSS tokens at the top of ``_base.html`` to re-brand.
"""

from __future__ import annotations

from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates" / "email"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    auto_reload=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _fmt_currency(value, symbol: str = "$") -> str:
    try:
        return f"{symbol}{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_number(value, decimals: int = 0) -> str:
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_percent(value) -> str:
    try:
        v = float(value)
        return f"{v * 100:.2f}%" if v <= 1 else f"{v:.2f}%"
    except (TypeError, ValueError):
        return str(value)


jinja_env.filters["currency"] = _fmt_currency
jinja_env.filters["number"] = _fmt_number
jinja_env.filters["percent"] = _fmt_percent


def render_email_html(doc_type: str, context: dict) -> str:
    """Render the ADA-aligned HTML email body for ``doc_type``."""
    template = jinja_env.get_template(f"{doc_type}.html")
    return template.render(**context)

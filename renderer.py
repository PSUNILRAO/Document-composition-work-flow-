"""
renderer.py
────────────
Renders Jinja2 HTML templates → PDF bytes.
Uses WeasyPrint if available (best fidelity), falls back to ReportLab.

Edit templates in templates/*.html at any time.
Changes take effect on the next render — no restart needed.
"""

import io
import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

# ── Jinja2 environment ────────────────────────────────────────────────────────
# auto_reload=True ensures template edits take effect immediately
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR), followlinks=True),
    autoescape=select_autoescape(["html"]),
    auto_reload=True,
)

# ── Custom Jinja2 filters ─────────────────────────────────────────────────────
def _fmt_currency(value, symbol="$") -> str:
    try:
        return f"{symbol}{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)

def _fmt_percent(value) -> str:
    try:
        v = float(value)
        return f"{v * 100:.2f}%" if v <= 1 else f"{v:.2f}%"
    except (TypeError, ValueError):
        return str(value)

def _fmt_number(value, decimals=0) -> str:
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)

def _apply_style(field_name: str, styles: dict) -> str:
    return styles.get(field_name, "")

def _row_style(row: dict, style_rules: list) -> str:
    """Given a data row dict and a list of style rules, return CSS string."""
    from rules_engine import _safe_eval
    for rule in style_rules:
        ctx = {**row, "amount": float(row.get("amount", 0) or 0)}
        if _safe_eval(rule.get("condition", "false"), ctx):
            return rule.get("style", "")
    return ""

jinja_env.filters["currency"]   = _fmt_currency
jinja_env.filters["percent"]    = _fmt_percent
jinja_env.filters["number"]     = _fmt_number
jinja_env.filters["row_style"]  = _row_style
jinja_env.globals["apply_style"] = _apply_style


# ── HTML rendering ────────────────────────────────────────────────────────────
def render_html(doc_type: str, context: dict) -> str:
    """Render the Jinja2 template for a doc_type with the given context."""
    from data_loader import get_doc_schema
    schema    = get_doc_schema(doc_type)
    template  = jinja_env.get_template(schema["template"])
    return template.render(**context)


# ── PDF conversion ────────────────────────────────────────────────────────────
def html_to_pdf(html: str) -> bytes:
    """Convert HTML string to PDF bytes. WeasyPrint preferred."""
    try:
        from weasyprint import HTML as WP
        return WP(string=html, base_url=str(TEMPLATES_DIR)).write_pdf()
    except ImportError:
        pass

    # ── ReportLab fallback ─────────────────────────────────────────────────
    return _reportlab_from_html(html)


def _reportlab_from_html(html: str) -> bytes:
    """
    Lightweight HTML → ReportLab PDF.
    Good for text-heavy documents; use WeasyPrint for pixel-perfect layouts.
    """
    import re as _re
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
    from reportlab.platypus import (SimpleDocTemplate, Paragraph,
                                    Spacer, Table, TableStyle, HRFlowable)

    PRIMARY    = colors.HexColor("#1E3A5F")
    ACCENT     = colors.HexColor("#F0F4FF")
    LIGHT      = colors.HexColor("#E5E7EB")
    DARK       = colors.HexColor("#1F2937")
    MUTED      = colors.HexColor("#6B7280")
    WARNING    = colors.HexColor("#FEF3C7")
    CRITICAL   = colors.HexColor("#FEE2E2")
    SUCCESS_BG = colors.HexColor("#DCFCE7")

    base = getSampleStyleSheet()
    S = {
        "h1":      ParagraphStyle("H1",  parent=base["Title"],
                                  fontSize=18, textColor=PRIMARY,
                                  fontName="Helvetica-Bold", spaceAfter=6),
        "h2":      ParagraphStyle("H2",  parent=base["Heading2"],
                                  fontSize=12, textColor=PRIMARY,
                                  fontName="Helvetica-Bold",
                                  spaceBefore=12, spaceAfter=4),
        "body":    ParagraphStyle("Bod", parent=base["Normal"],
                                  fontSize=10, leading=15,
                                  fontName="Helvetica", textColor=DARK,
                                  alignment=TA_LEFT, spaceAfter=4),
        "label":   ParagraphStyle("Lbl", parent=base["Normal"],
                                  fontSize=8.5, textColor=MUTED,
                                  fontName="Helvetica"),
        "th":      ParagraphStyle("TH",  parent=base["Normal"],
                                  fontSize=9, fontName="Helvetica-Bold",
                                  textColor=colors.white),
        "td":      ParagraphStyle("TD",  parent=base["Normal"],
                                  fontSize=9, fontName="Helvetica",
                                  textColor=DARK),
        "alert_w": ParagraphStyle("AW",  parent=base["Normal"],
                                  fontSize=9.5, fontName="Helvetica",
                                  textColor=colors.HexColor("#92400E"),
                                  backColor=WARNING, borderPadding=6),
        "alert_c": ParagraphStyle("AC",  parent=base["Normal"],
                                  fontSize=9.5, fontName="Helvetica-Bold",
                                  textColor=colors.HexColor("#991B1B"),
                                  backColor=CRITICAL, borderPadding=6),
        "alert_i": ParagraphStyle("AI",  parent=base["Normal"],
                                  fontSize=9.5, fontName="Helvetica",
                                  textColor=colors.HexColor("#1E40AF"),
                                  backColor=colors.HexColor("#DBEAFE"),
                                  borderPadding=6),
        "footer":  ParagraphStyle("Ftr", parent=base["Normal"],
                                  fontSize=8, textColor=MUTED,
                                  fontName="Helvetica"),
    }

    # --- strip tags, extract structure ---
    html_clean = _re.sub(r"<style[^>]*>.*?</style>", "", html,
                         flags=_re.DOTALL | _re.IGNORECASE)
    html_clean = _re.sub(r"<script[^>]*>.*?</script>", "", html_clean,
                         flags=_re.DOTALL | _re.IGNORECASE)

    # Collect paragraphs, headings, tables
    blocks = []
    pos = 0
    tag_re = _re.compile(
        r"<(h[1-3]|p|tr|th|td|table|/table|hr|div)[^>]*>(.*?)</\1>|"
        r"<hr[^>]*/?>",
        _re.DOTALL | _re.IGNORECASE,
    )
    current_table = None

    for m in _re.finditer(
        r"<(/?(?:h[1-3]|p|table|tr|th|td|hr|div))[^>]*>",
        html_clean, _re.IGNORECASE
    ):
        tag = m.group(1).lower()
        if tag == "table":
            current_table = []
        elif tag == "/table" and current_table is not None:
            blocks.append(("table", current_table))
            current_table = None
        elif tag in ("tr",) and current_table is not None:
            current_table.append([])
        elif tag in ("td", "th") and current_table and current_table[-1] is not None:
            # find end tag
            end = _re.search(f"</{tag}>", html_clean[m.end():], _re.IGNORECASE)
            if end:
                cell = _re.sub(r"<[^>]+>", "", html_clean[m.end(): m.end() + end.start()]).strip()
                current_table[-1].append((cell, tag == "th"))

    # Also pull text from <p> and <h1/h2/h3>
    for m in _re.finditer(
        r"<(h[1-3]|p)(?:[^>]*)>(.*?)</\1>", html_clean, _re.DOTALL | _re.IGNORECASE
    ):
        tag  = m.group(1).lower()
        text = _re.sub(r"<[^>]+>", " ", m.group(2)).strip()
        text = _re.sub(r"&nbsp;", " ", text)
        text = _re.sub(r"&amp;",  "&", text)
        text = _re.sub(r"\s+",    " ", text).strip()
        if text:
            blocks.append((tag, text))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=18*mm, bottomMargin=18*mm)
    story = []
    usable_w = A4[0] - 40*mm

    for btype, bdata in blocks:
        if btype == "hr":
            story.append(HRFlowable(width="100%", thickness=1.5,
                                    color=PRIMARY, spaceAfter=8))
        elif btype in ("h1", "h2", "h3"):
            story.append(Paragraph(bdata, S.get(btype, S["h2"])))
        elif btype == "p":
            story.append(Paragraph(bdata, S["body"]))
        elif btype == "table" and bdata:
            col_n  = max(len(r) for r in bdata)
            col_w  = usable_w / col_n
            tdata  = [
                [Paragraph(cell, S["th"] if is_hdr else S["td"])
                 for cell, is_hdr in row]
                for row in bdata if row
            ]
            t = Table(tdata, colWidths=[col_w]*col_n, hAlign="LEFT", repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND",     (0,0), (-1,0),  PRIMARY),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, ACCENT]),
                ("GRID",           (0,0), (-1,-1), 0.3, LIGHT),
                ("TOPPADDING",     (0,0), (-1,-1), 5),
                ("BOTTOMPADDING",  (0,0), (-1,-1), 5),
                ("LEFTPADDING",    (0,0), (-1,-1), 7),
                ("VALIGN",         (0,0), (-1,-1), "MIDDLE"),
            ]))
            story.append(Spacer(1, 5))
            story.append(t)
            story.append(Spacer(1, 8))

    if not story:
        story.append(Paragraph("Document generated.", S["body"]))

    doc.build(story)
    return buf.getvalue()


# ── High-level render function ────────────────────────────────────────────────
def render_pdf(doc_type: str, context: dict) -> bytes:
    """Full pipeline: context → HTML → PDF bytes."""
    html = render_html(doc_type, context)
    return html_to_pdf(html)

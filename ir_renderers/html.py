"""
ir_renderers.html
─────────────────
Render a canonical ``Document`` IR to semantic HTML.

The same renderer serves three flavours:

* ``"email"`` — ADA/WCAG-2.1-AA-aligned HTML email: 600px table-layout
  container, fully inline CSS, skip-to-content link, ``<html lang>``,
  visually-hidden preheader. Produces a complete document.
* ``"print"`` — A4 page-oriented HTML suitable for WeasyPrint → PDF.
  Uses modern CSS (grid / flex) but stays single-column. Produces a
  complete document.
* ``"fragment"`` — The document body only, with no surrounding
  ``<html>/<head>/<body>``. Used by web previews that want to embed the
  IR output inside an existing page shell.

Only the IR is consumed — no Jinja, no per-doc-type template files.
"""

from __future__ import annotations

from html import escape
from typing import Literal

from document_ir import (
    BulletList,
    Callout,
    Document,
    Emphasis,
    Heading,
    KeyValueGrid,
    LineBreak,
    Link,
    Paragraph,
    Section,
    Separator,
    Strong,
    Table,
    Text,
)

Flavor = Literal["email", "print", "fragment"]


# ── Tokens ───────────────────────────────────────────────────────────────────
# Shared colour / type tokens. Kept small & high-contrast to pass WCAG 2.1 AA
# against the white background used by all flavours.

_TOKENS = {
    "brand":       "#1E3A5F",
    "accent_bg":   "#F0F4FF",
    "text":        "#1F2937",
    "muted":       "#4B5563",
    "border":      "#D1D5DB",
    "info_bg":     "#DDF4FF",
    "info_fg":     "#0550AE",
    "warn_bg":     "#FFF8C5",
    "warn_fg":     "#7C4A00",
    "crit_bg":     "#FFEBE9",
    "crit_fg":     "#9A1F1F",
    "body_font":   ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
                    "Helvetica,Arial,sans-serif"),
}


def _style(props: dict[str, str]) -> str:
    return ";".join(f"{k}:{v}" for k, v in props.items())


# ── Inline rendering ─────────────────────────────────────────────────────────
def _render_inlines(paragraph: Paragraph) -> str:
    out: list[str] = []
    for node in paragraph.inlines:
        if isinstance(node, Text):
            out.append(escape(node.value))
        elif isinstance(node, Strong):
            out.append(f"<strong>{escape(node.value)}</strong>")
        elif isinstance(node, Emphasis):
            out.append(f"<em>{escape(node.value)}</em>")
        elif isinstance(node, LineBreak):
            out.append("<br>")
        elif isinstance(node, Link):
            out.append(
                f'<a href="{escape(node.href, quote=True)}">'
                f"{escape(node.text)}</a>"
            )
    return "".join(out)


# ── Block rendering ──────────────────────────────────────────────────────────
def _render_heading(node: Heading, flavor: Flavor) -> str:
    level = max(1, min(6, int(node.level)))
    if flavor == "email":
        style = _style({
            "margin": "0 0 10px",
            "font-size": f"{20 - (level - 1) * 2}px",
            "color": _TOKENS["brand"],
            "font-weight": "600",
        })
        return f'<h{level} style="{style}">{escape(node.text)}</h{level}>'
    return f"<h{level}>{escape(node.text)}</h{level}>"


def _render_paragraph(node: Paragraph, flavor: Flavor) -> str:
    inner = _render_inlines(node)
    if flavor == "email":
        return (
            f'<p style="margin:0 0 14px;font-size:15px;line-height:1.55;'
            f'color:{_TOKENS["text"]};">{inner}</p>'
        )
    return f"<p>{inner}</p>"


def _render_key_value_grid(node: KeyValueGrid, flavor: Flavor) -> str:
    if flavor == "email":
        rows = []
        for kv in node.items:
            rows.append(
                '<tr>'
                '<th scope="row" style="text-align:left;padding:8px 0;'
                f'font-weight:500;color:{_TOKENS["muted"]};'
                f'border-bottom:1px solid {_TOKENS["border"]};font-size:14px;">'
                f"{escape(kv.label)}</th>"
                '<td style="text-align:right;padding:8px 0;'
                f'border-bottom:1px solid {_TOKENS["border"]};'
                'font-size:15px;font-weight:600;">'
                f"{escape(kv.value)}</td>"
                "</tr>"
            )
        caption = (
            f'<caption style="text-align:left;font-size:14px;font-weight:600;'
            f'color:{_TOKENS["brand"]};padding:0 0 6px;">'
            f"{escape(node.caption)}</caption>"
            if node.caption else ""
        )
        return (
            '<table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0" border="0" '
            'style="border-collapse:collapse;margin:0 0 22px;">'
            f"{caption}{''.join(rows)}</table>"
        )
    # Semantic definition list for print / fragment.
    items = []
    for kv in node.items:
        items.append(
            f"<dt>{escape(kv.label)}</dt>"
            f"<dd>{escape(kv.value)}</dd>"
        )
    caption = f"<p><strong>{escape(node.caption)}</strong></p>" if node.caption else ""
    return f"{caption}<dl>{''.join(items)}</dl>"


def _render_table(node: Table, flavor: Flavor) -> str:
    head_cells = []
    for cell in node.headers:
        if flavor == "email":
            style = _style({
                "text-align": cell.align,
                "padding": "8px 10px",
                "border-bottom": f"1px solid {_TOKENS['border']}",
                "background-color": _TOKENS["accent_bg"],
                "font-weight": "600",
                "color": _TOKENS["text"],
            })
            head_cells.append(
                f'<th scope="col" style="{style}">{escape(cell.value)}</th>'
            )
        else:
            head_cells.append(
                f'<th scope="col" style="text-align:{cell.align}">'
                f"{escape(cell.value)}</th>"
            )

    body_rows = []
    for row in node.rows:
        tds = []
        for cell in row:
            if flavor == "email":
                tds.append(
                    f'<td style="padding:8px 10px;border-bottom:1px solid '
                    f'{_TOKENS["border"]};text-align:{cell.align};'
                    'font-variant-numeric:tabular-nums;">'
                    f"{escape(cell.value)}</td>"
                )
            else:
                tds.append(
                    f'<td style="text-align:{cell.align}">'
                    f"{escape(cell.value)}</td>"
                )
        body_rows.append(f"<tr>{''.join(tds)}</tr>")

    caption = (
        f"<caption>{escape(node.caption)}</caption>"
        if node.caption else ""
    )

    if flavor == "email":
        return (
            '<table role="table" width="100%" cellpadding="0" cellspacing="0" '
            'border="0" style="border-collapse:collapse;font-size:14px;'
            'margin:0 0 18px;">'
            f"{caption}<thead><tr>{''.join(head_cells)}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody></table>"
        )
    return (
        f"<table>{caption}<thead><tr>{''.join(head_cells)}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )


def _render_bullet_list(node: BulletList, flavor: Flavor) -> str:
    items = "".join(f"<li>{escape(item)}</li>" for item in node.items)
    if flavor == "email":
        return (
            '<ul style="margin:0 0 14px 20px;padding:0;font-size:15px;'
            'line-height:1.55;">' + items + "</ul>"
        )
    return f"<ul>{items}</ul>"


def _render_callout(node: Callout, flavor: Flavor) -> str:
    role = {"info": "status", "warning": "alert", "critical": "alert"}[node.severity]
    bg = {"info":   _TOKENS["info_bg"],
          "warning": _TOKENS["warn_bg"],
          "critical": _TOKENS["crit_bg"]}[node.severity]
    fg = {"info":   _TOKENS["info_fg"],
          "warning": _TOKENS["warn_fg"],
          "critical": _TOKENS["crit_fg"]}[node.severity]
    if flavor == "email":
        style = _style({
            "margin": "0 0 14px",
            "padding": "10px 14px",
            "border-radius": "4px",
            "background-color": bg,
            "color": fg,
            "font-size": "14px",
            "line-height": "1.5",
            "border-left": f"4px solid {fg}",
        })
        return (
            f'<div role="{role}" style="{style}">'
            f"<strong>{escape(node.severity.title())}:</strong> "
            f"{escape(node.text)}</div>"
        )
    return f'<div class="callout callout-{node.severity}" role="{role}">{escape(node.text)}</div>'


def _render_separator(_node: Separator, flavor: Flavor) -> str:
    if flavor == "email":
        return (
            '<hr style="border:0;border-top:1px solid '
            f'{_TOKENS["border"]};margin:18px 0;">'
        )
    return "<hr>"


def _render_section(node: Section, flavor: Flavor) -> str:
    parts: list[str] = []
    if node.heading is not None:
        parts.append(_render_heading(node.heading, flavor))
    for block in node.blocks:
        parts.append(_render_block(block, flavor))
    inner = "".join(parts)
    tag = {"header": "header",
           "main":   "main",
           "footer": "footer",
           "aside":  "aside"}.get(node.role or "", "section")
    return f"<{tag}>{inner}</{tag}>"


def _render_block(block, flavor: Flavor) -> str:
    # Order matters: Section is checked last because it's recursive.
    if isinstance(block, Heading):
        return _render_heading(block, flavor)
    if isinstance(block, Paragraph):
        return _render_paragraph(block, flavor)
    if isinstance(block, KeyValueGrid):
        return _render_key_value_grid(block, flavor)
    if isinstance(block, Table):
        return _render_table(block, flavor)
    if isinstance(block, BulletList):
        return _render_bullet_list(block, flavor)
    if isinstance(block, Callout):
        return _render_callout(block, flavor)
    if isinstance(block, Separator):
        return _render_separator(block, flavor)
    if isinstance(block, Section):
        return _render_section(block, flavor)
    return ""


# ── Document shells ──────────────────────────────────────────────────────────
def _email_shell(doc: Document, body: str) -> str:
    closing = doc.metadata.get("closing_balance", "")
    preheader_raw = doc.metadata.get("preheader") or (
        f"{doc.title} — closing balance {closing}".strip()
        if closing else doc.title
    )
    preheader = escape(preheader_raw)
    body_style = _style({
        "margin": "0",
        "padding": "0",
        "background-color": "#F3F4F6",
        "font-family": _TOKENS["body_font"],
        "color": _TOKENS["text"],
    })
    container_style = _style({
        "width": "600px",
        "max-width": "100%",
        "margin": "0 auto",
        "background-color": "#FFFFFF",
        "padding": "28px 32px",
    })
    preheader_style = _style({
        "display": "none",
        "visibility": "hidden",
        "opacity": "0",
        "color": "transparent",
        "height": "0",
        "width": "0",
        "overflow": "hidden",
    })
    return (
        f'<!DOCTYPE html>\n'
        f'<html lang="{escape(doc.language, quote=True)}">'
        f"<head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{escape(doc.title)}</title>"
        f"</head>"
        f'<body style="{body_style}">'
        f'<div style="{preheader_style}">{preheader}</div>'
        f'<a href="#main-content" style="position:absolute;left:-9999px;top:auto;'
        f'width:1px;height:1px;overflow:hidden;">Skip to main content</a>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"><tr><td align="center">'
        f'<div style="{container_style}" id="main-content">'
        f"{body}"
        f"</div></td></tr></table></body></html>"
    )


def _print_shell(doc: Document, body: str) -> str:
    css = (
        "@page{size:A4;margin:18mm 16mm;}"
        f"body{{font-family:{_TOKENS['body_font']};"
        f"color:{_TOKENS['text']};font-size:10.5pt;line-height:1.55;}}"
        f"h1,h2,h3,h4{{color:{_TOKENS['brand']};}}"
        "table{width:100%;border-collapse:collapse;margin:8px 0;}"
        f"th,td{{padding:6px 8px;border-bottom:1px solid {_TOKENS['border']};}}"
        f"th{{background:{_TOKENS['accent_bg']};text-align:left;}}"
        "dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 18px;}"
        f"dt{{color:{_TOKENS['muted']};}}"
        f"dd{{margin:0;font-weight:600;}}"
        f".callout{{padding:8px 12px;border-radius:4px;margin:6px 0;}}"
        f".callout-info{{background:{_TOKENS['info_bg']};color:{_TOKENS['info_fg']};}}"
        f".callout-warning{{background:{_TOKENS['warn_bg']};color:{_TOKENS['warn_fg']};}}"
        f".callout-critical{{background:{_TOKENS['crit_bg']};color:{_TOKENS['crit_fg']};}}"
    )
    return (
        f'<!DOCTYPE html>\n<html lang="{escape(doc.language, quote=True)}">'
        f"<head><meta charset=\"utf-8\">"
        f"<title>{escape(doc.title)}</title>"
        f"<style>{css}</style></head>"
        f"<body>{body}</body></html>"
    )


# ── Public entrypoint ────────────────────────────────────────────────────────
def render_html(doc: Document, flavor: Flavor = "email") -> str:
    """Render ``doc`` to HTML for the given channel flavour.

    * ``"email"``    — complete ADA-aligned HTML email document.
    * ``"print"``    — complete A4 HTML document for PDF pipeline.
    * ``"fragment"`` — body-only HTML (no ``<html>/<head>/<body>``).
    """
    # Alerts render above the main content for all flavours.
    alert_blocks = "".join(_render_callout(a, flavor) for a in doc.alerts)
    body_blocks = "".join(_render_block(b, flavor) for b in doc.blocks)
    body = alert_blocks + body_blocks

    if flavor == "email":
        return _email_shell(doc, body)
    if flavor == "print":
        return _print_shell(doc, body)
    return body

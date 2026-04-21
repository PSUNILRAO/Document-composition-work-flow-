"""
ir_renderers.text
─────────────────
Render a canonical ``Document`` IR to plain text.

Two flavours:

* ``"full"``    — preserves the full tree: headings underlined, tables
  aligned in fixed-width columns, key/value grids as ``label: value``
  lines. Suitable for ``File Exchange`` text dumps or debug previews.
* ``"compact"`` — short-form reduction for SMS. Drops tables / bullet
  lists / separators and emits a single line per top-level block so the
  output fits comfortably inside the 3GPP concatenated-SMS budget.

The compact flavour is deliberately conservative — it never walks into
``Table`` rows or ``BulletList`` items, so long transaction histories
don't accidentally balloon the SMS payload. Callers that need richer
short-form output should either project the Document at build time or
use ``"full"`` and let the SMS segmenter do the splitting.
"""

from __future__ import annotations

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
    inline_text,
)

Flavor = Literal["full", "compact"]


# ── Inlines ──────────────────────────────────────────────────────────────────
def _render_inlines_compact(paragraph: Paragraph) -> str:
    """Flatten inlines to a single line, dropping line breaks as spaces."""
    return inline_text(paragraph).replace("\n", " ").strip()


def _render_inlines_full(paragraph: Paragraph) -> str:
    out: list[str] = []
    for node in paragraph.inlines:
        if isinstance(node, Text):
            out.append(node.value)
        elif isinstance(node, Strong):
            out.append(node.value.upper())
        elif isinstance(node, Emphasis):
            out.append(f"*{node.value}*")
        elif isinstance(node, LineBreak):
            out.append("\n")
        elif isinstance(node, Link):
            out.append(f"{node.text} ({node.href})")
    return "".join(out)


# ── Block rendering — full ───────────────────────────────────────────────────
def _render_table_full(node: Table) -> str:
    widths = [len(h.value) for h in node.headers]
    for row in node.rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell.value))

    def _fmt_row(cells, header: bool = False) -> str:
        parts = []
        for cell, w in zip(cells, widths):
            if cell.align == "right":
                parts.append(cell.value.rjust(w))
            elif cell.align == "center":
                parts.append(cell.value.center(w))
            else:
                parts.append(cell.value.ljust(w))
        return "  ".join(parts).rstrip()

    lines = []
    if node.caption:
        lines.append(node.caption)
    lines.append(_fmt_row(node.headers, header=True))
    lines.append("  ".join("-" * w for w in widths))
    for row in node.rows:
        lines.append(_fmt_row(row))
    return "\n".join(lines)


def _render_block_full(block) -> str:
    if isinstance(block, Heading):
        underline = "=" if block.level == 1 else "-"
        return f"{block.text}\n{underline * max(len(block.text), 1)}"
    if isinstance(block, Paragraph):
        return _render_inlines_full(block)
    if isinstance(block, KeyValueGrid):
        head = f"{block.caption}\n" if block.caption else ""
        return head + "\n".join(
            f"{kv.label}: {kv.value}" for kv in block.items
        )
    if isinstance(block, Table):
        return _render_table_full(block)
    if isinstance(block, BulletList):
        return "\n".join(f"- {item}" for item in block.items)
    if isinstance(block, Callout):
        return f"[{block.severity.upper()}] {block.text}"
    if isinstance(block, Separator):
        return "-" * 60
    if isinstance(block, Section):
        parts = []
        if block.heading is not None:
            parts.append(_render_block_full(block.heading))
        for child in block.blocks:
            parts.append(_render_block_full(child))
        return "\n\n".join(p for p in parts if p)
    return ""


# ── Block rendering — compact ────────────────────────────────────────────────
def _render_block_compact(block) -> str:
    if isinstance(block, Heading):
        return block.text.strip()
    if isinstance(block, Paragraph):
        return _render_inlines_compact(block)
    if isinstance(block, KeyValueGrid):
        # ``label: value`` pairs joined by ``; `` — one line per grid.
        return "; ".join(f"{kv.label}: {kv.value}" for kv in block.items)
    if isinstance(block, Callout):
        return f"[{block.severity.upper()}] {block.text}"
    if isinstance(block, Section):
        # Sections flatten: emit each child block on its own line, skipping
        # Table / BulletList / Separator so the compact form stays short.
        parts: list[str] = []
        if block.heading is not None:
            parts.append(_render_block_compact(block.heading))
        for child in block.blocks:
            line = _render_block_compact(child)
            if line:
                parts.append(line)
        return "\n".join(parts)
    # Table / BulletList / Separator: intentionally dropped for compact.
    return ""


# ── Public entrypoint ────────────────────────────────────────────────────────
def render_text(doc: Document, flavor: Flavor = "compact") -> str:
    """Render ``doc`` to plain text.

    * ``"compact"`` — single, short body (SMS-friendly). Tables and bullet
      lists are dropped.
    * ``"full"``    — lossless-ish plain-text projection: headings
      underlined, tables aligned, key/value grids as lines.
    """
    if flavor == "compact":
        alert_lines = [f"[{a.severity.upper()}] {a.text}" for a in doc.alerts]
        block_lines = [_render_block_compact(b) for b in doc.blocks]
        # Whitespace-normalise each line and drop empties so the SMS
        # segmenter sees a tight payload.
        lines = [ln.strip() for ln in alert_lines + block_lines if ln and ln.strip()]
        return "\n".join(lines)

    alerts = [f"[{a.severity.upper()}] {a.text}" for a in doc.alerts]
    blocks = [_render_block_full(b) for b in doc.blocks]
    return "\n\n".join(p for p in alerts + blocks if p)

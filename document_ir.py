"""
document_ir.py
──────────────
Channel-neutral Canonical Intermediate Representation (IR) for a composed
document.

The IR is a small tree of dataclasses that can be produced once per
``(record, rules)`` pair and then fed into any channel renderer
(PDF / HTML email / plain-text SMS / …) without re-running the business
logic. Renderers consume the IR; they do not inspect the raw record.

Design goals:
  • *Lossless enough for long-form channels*: HTML email, PDF-UA can be
    re-emitted from the IR without losing semantic information a
    screen-reader or a compliance tool cares about (landmarks, heading
    levels, row/col scope, alt text, language).
  • *Reducible for short-form channels*: SMS / plain-text renderers can
    walk the same tree and drop blocks that don't compress (e.g. long
    tables) while still keeping the key headline fields.
  • *Serialisable*: the whole tree is plain dataclasses so it can be
    pickled, hashed for reproducibility audits, or shipped as JSON for
    debugging.

This is *only* the data model; concrete builders (``context → Document``)
live in ``ir_builders/`` and concrete renderers (``Document → bytes/str``)
live in ``ir_renderers/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Union


# ── Inlines ──────────────────────────────────────────────────────────────────
# Inline nodes are the leaves inside a ``Paragraph``. They express
# presentation-agnostic emphasis, not visual styling.

@dataclass(frozen=True)
class Text:
    """A run of plain text."""
    value: str


@dataclass(frozen=True)
class Strong:
    """A run emphasised with strong importance (``<strong>``)."""
    value: str


@dataclass(frozen=True)
class Emphasis:
    """A run emphasised with stress/italic (``<em>``)."""
    value: str


@dataclass(frozen=True)
class LineBreak:
    """An explicit soft break inside a paragraph (``<br>``)."""


@dataclass(frozen=True)
class Link:
    """A hyperlink with visible text."""
    href: str
    text: str


Inline = Union[Text, Strong, Emphasis, LineBreak, Link]


# ── Blocks ───────────────────────────────────────────────────────────────────
# Block nodes make up the document's top-level flow.

Severity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class Heading:
    """A section or sub-section heading. ``level`` is 1–6 (same as HTML h1–h6)."""
    level: int
    text: str


@dataclass(frozen=True)
class Paragraph:
    """A run of inlines that forms one paragraph."""
    inlines: tuple[Inline, ...]


@dataclass(frozen=True)
class KeyValue:
    """One row of a key/value grid."""
    label: str
    value: str


@dataclass(frozen=True)
class KeyValueGrid:
    """
    A grid of label/value pairs. Rendered as a definition list in HTML, as
    ``label: value`` lines in plain text, as a two-column table in DOCX.
    """
    items: tuple[KeyValue, ...]
    caption: str | None = None


@dataclass(frozen=True)
class TableCell:
    """A single cell in a Table; align drives text alignment in renderers."""
    value: str
    align: Literal["left", "right", "center"] = "left"


@dataclass(frozen=True)
class Table:
    """
    A data table with an explicit header row. ``headers`` carry the same
    alignment metadata as body cells so renderers can align consistently.
    """
    headers: tuple[TableCell, ...]
    rows: tuple[tuple[TableCell, ...], ...]
    caption: str | None = None


@dataclass(frozen=True)
class BulletList:
    """A flat unordered list of short strings."""
    items: tuple[str, ...]


@dataclass(frozen=True)
class Callout:
    """
    A semantic alert / callout block (info / warning / critical). Renderers
    translate severity into the channel-appropriate styling (ARIA role,
    background colour, bracketed ``[WARNING]`` prefix in SMS, etc.).
    """
    severity: Severity
    text: str


@dataclass(frozen=True)
class Separator:
    """A visual divider (``<hr>`` in HTML, blank line in text)."""


# Forward declaration so ``Section`` can contain itself and other blocks.
Block = Union[
    "Heading",
    "Paragraph",
    "KeyValueGrid",
    "Table",
    "BulletList",
    "Callout",
    "Separator",
    "Section",
]


@dataclass(frozen=True)
class Section:
    """
    A grouping of blocks under a shared heading. Sections are purely
    semantic — renderers use them to emit ``<section>`` landmarks in HTML
    or to keep related rows together in paginated PDF output.
    """
    blocks: tuple[Block, ...]
    heading: Heading | None = None
    role: Literal["header", "main", "footer", "aside", None] = None


# ── Root ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Document:
    """
    The root of the IR tree for one composed document.

    Attributes:
        doc_type   : logical doc type (``bank_statement``, …).
        title      : short title used in HTML <title>, email subject fallbacks.
        language   : BCP-47 language tag (default ``"en"``).
        metadata   : arbitrary ``str → str`` key/values that renderers may
                     surface in headers (doc id, period, etc.). Stored as a
                     sorted tuple of pairs so ``Document`` stays hashable
                     (the whole point of ``frozen=True``). Callers normally
                     build it with the ``metadata(...)`` factory below and
                     read it with ``Document.meta(key, default)``. Not
                     schema-enforced; renderers ignore keys they don't
                     recognise.
        blocks     : top-level flow. Typically one ``Section`` each for
                     ``header``, ``main`` and ``footer`` — but any block is
                     allowed at the root.
        alerts     : rules-engine alerts preserved on the root so short-form
                     channels (SMS) can surface them without walking the
                     whole tree.
    """
    doc_type: str
    title: str
    blocks: tuple[Block, ...]
    language: str = "en"
    metadata: tuple[tuple[str, str], ...] = ()
    alerts: tuple[Callout, ...] = ()

    def meta(self, key: str, default: str = "") -> str:
        """Return the metadata value for ``key`` or ``default``."""
        for k, v in self.metadata:
            if k == key:
                return v
        return default

    def meta_dict(self) -> dict[str, str]:
        """Return a fresh mutable dict view of the metadata pairs."""
        return dict(self.metadata)


def metadata(
    source: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    **kwargs: str,
) -> tuple[tuple[str, str], ...]:
    """Build a hashable ``Document.metadata`` tuple from dict / pairs / kwargs.

    Empty-string values are dropped so renderers don't have to re-check
    every key; keys are sorted so equal metadata produces equal tuples
    (and thus equal hashes) regardless of insertion order.
    """
    items: dict[str, str] = {}
    if source is not None:
        if isinstance(source, Mapping):
            items.update(source)
        else:
            items.update(dict(source))
    items.update(kwargs)
    return tuple(
        (k, v) for k, v in sorted(items.items(), key=lambda kv: kv[0])
        if v != ""
    )


# ── Small conveniences ───────────────────────────────────────────────────────
def para(*parts: str | Inline) -> Paragraph:
    """Build a Paragraph from a mix of raw strings and Inline nodes."""
    inlines: list[Inline] = []
    for p in parts:
        inlines.append(Text(p) if isinstance(p, str) else p)
    return Paragraph(tuple(inlines))


def inline_text(doc_block: Paragraph) -> str:
    """Flatten a Paragraph's inlines to a single plain-text string.

    Used by short-form renderers (SMS) and by test assertions.
    """
    out: list[str] = []
    for node in doc_block.inlines:
        if isinstance(node, (Text, Strong, Emphasis)):
            out.append(node.value)
        elif isinstance(node, Link):
            out.append(node.text)
        elif isinstance(node, LineBreak):
            out.append(" ")
    return "".join(out)

"""
sms_renderer.py
────────────────
Renders a record to one or more SMS segments.

A short plain-text Jinja template per doc_type lives at
``templates/sms/<doc_type>.txt``.

Segmentation follows 3GPP:
  * If the message is representable in the GSM-7 default + extension alphabet
    it is encoded as GSM-7 (160 chars/part single, 153 chars/part concat).
  * Otherwise UCS-2 (70 chars/part single, 67 chars/part concat).

A ``(1/n)`` prefix is prepended to every part so the recipient sees a stable
ordering regardless of gateway support for UDH concatenation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates" / "sms"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

# GSM-7 default alphabet (3GPP TS 23.038)
_GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ ÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM7_EXT = set("^{}\\[~]|€\f")  # each counts as 2 septets

# Concatenated-SMS per-part character budgets (with 6-byte UDH headers)
GSM7_SINGLE = 160
GSM7_CONCAT = 153
UCS2_SINGLE = 70
UCS2_CONCAT = 67

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(enabled_extensions=()),  # plain text — no HTML escaping
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


jinja_env.filters["currency"] = _fmt_currency
jinja_env.filters["number"] = _fmt_number


class SMSResult(TypedDict):
    text: str
    encoding: str       # "GSM-7" | "UCS-2"
    length: int         # septets (GSM-7) or code units (UCS-2)
    parts: list[str]    # segmented payloads with "(i/n) " prefix
    part_count: int


def _is_gsm7(text: str) -> bool:
    """True if every character fits the GSM-7 default + extension alphabet."""
    return all((ch in _GSM7_BASIC) or (ch in _GSM7_EXT) for ch in text)


def _gsm7_length(text: str) -> int:
    """Length in septets (extension chars count as 2)."""
    return sum(2 if ch in _GSM7_EXT else 1 for ch in text)


def _segment(text: str, encoding: str) -> list[str]:
    """Split ``text`` into segments without overrunning the per-part budget.

    For GSM-7 we measure septets and are careful not to split so that an
    extension pair ``['^']`` (2 septets) straddles a segment boundary.
    For UCS-2 we measure UTF-16 code units and never split a surrogate pair.
    """
    if encoding == "GSM-7":
        single, concat = GSM7_SINGLE, GSM7_CONCAT
        length = _gsm7_length(text)
    else:
        single, concat = UCS2_SINGLE, UCS2_CONCAT
        length = len(text.encode("utf-16-le")) // 2  # code units

    if length <= single:
        return [text]

    parts: list[str] = []
    buf, buf_len = "", 0
    for ch in text:
        if encoding == "GSM-7":
            step = 2 if ch in _GSM7_EXT else 1
        else:
            # Non-BMP => surrogate pair (2 code units) and must not straddle
            step = 2 if ord(ch) > 0xFFFF else 1
        if buf_len + step > concat:
            parts.append(buf)
            buf, buf_len = "", 0
        buf += ch
        buf_len += step
    if buf:
        parts.append(buf)
    return parts


def render_sms(doc_type: str, context: dict) -> SMSResult:
    """Render SMS text for ``doc_type`` + segment it for the wire.

    The rendered body is stripped of leading/trailing whitespace and
    internal runs of blank lines are collapsed so templates can be
    laid out readably without paying for the extra characters.
    """
    template = jinja_env.get_template(f"{doc_type}.txt")
    raw = template.render(**context).strip()
    # Collapse any run of 2+ newlines to one
    body = "\n".join(line.rstrip() for line in raw.splitlines() if line.strip())

    if _is_gsm7(body):
        encoding = "GSM-7"
        length = _gsm7_length(body)
    else:
        encoding = "UCS-2"
        length = len(body.encode("utf-16-le")) // 2

    segs = _segment(body, encoding)
    n = len(segs)
    prefixed = [f"({i + 1}/{n}) {seg}" if n > 1 else seg for i, seg in enumerate(segs)]

    return {
        "text": body,
        "encoding": encoding,
        "length": length,
        "parts": prefixed,
        "part_count": n,
    }


def render_sms_text(doc_type: str, context: dict) -> str:
    """Convenience wrapper: returns one string with each part on its own line."""
    result = render_sms(doc_type, context)
    return "\n".join(result["parts"])

"""Per-doc-type builders that produce a canonical Document IR from context."""

from __future__ import annotations

from typing import Callable

from document_ir import Document


_registry: dict[str, Callable[[dict], Document]] = {}


def register(doc_type: str, builder: Callable[[dict], Document]) -> None:
    """Register an IR builder for a given ``doc_type``."""
    _registry[doc_type] = builder


def get_builder(doc_type: str) -> Callable[[dict], Document] | None:
    """Return the registered IR builder for ``doc_type``, or ``None``."""
    return _registry.get(doc_type)


def has_builder(doc_type: str) -> bool:
    """True if a builder is registered for ``doc_type``."""
    return doc_type in _registry


def build(doc_type: str, context: dict) -> Document:
    """Look up the builder for ``doc_type`` and run it against ``context``."""
    builder = _registry.get(doc_type)
    if builder is None:
        raise KeyError(f"No IR builder registered for doc_type={doc_type!r}")
    return builder(context)


# Register built-in builders on import so callers only need to
# ``import ir_builders``.
from . import bank_statement  # noqa: E402,F401

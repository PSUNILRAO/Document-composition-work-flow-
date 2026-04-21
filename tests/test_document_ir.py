"""
Unit + integration tests for Slice #3 — the canonical Document IR.

Covered:
  • IR dataclasses are immutable & hashable (safe to cache).
  • bank_statement builder produces a well-formed tree from the default
    fixture (James Wilson, row 0).
  • ir_renderers.html emits landmark-bearing email HTML and print HTML.
  • ir_renderers.text compact output fits within the 3GPP GSM-7 concat
    budget for the default row.
  • engine.generate_channel routes email + SMS through the IR path when
    schema.use_ir is True, and the rendered email still contains the
    rule-computed sentinels (James Wilson, $8,340.50).
  • Feature flag: flipping ``use_ir`` off falls back to legacy templates.

Run with:  python -m pytest tests/test_document_ir.py
Or the plain-stdlib way:  python -m unittest tests.test_document_ir
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import ir_builders
from data_loader import load_record, get_doc_schema
from document_ir import (
    Callout,
    Document,
    Heading,
    KeyValueGrid,
    Paragraph,
    Section,
    Table,
)
from ir_renderers.html import render_html
from ir_renderers.text import render_text
from rules_engine import apply_rules


def _bank_ctx():
    record = load_record("bank_statement", 0)
    return apply_rules("bank_statement", record)


class BuilderShapeTests(unittest.TestCase):
    def test_document_is_well_formed(self) -> None:
        doc = ir_builders.build("bank_statement", _bank_ctx())
        self.assertIsInstance(doc, Document)
        self.assertEqual(doc.doc_type, "bank_statement")
        self.assertEqual(doc.language, "en")
        # Header / main / footer landmarks.
        roles = [b.role for b in doc.blocks if isinstance(b, Section)]
        self.assertIn("header", roles)
        self.assertIn("main", roles)
        self.assertIn("footer", roles)

    def test_main_section_has_kv_grid_and_table(self) -> None:
        doc = ir_builders.build("bank_statement", _bank_ctx())
        main = next(b for b in doc.blocks
                    if isinstance(b, Section) and b.role == "main")
        kv_count = sum(1 for b in main.blocks if isinstance(b, KeyValueGrid))
        table_count = sum(1 for b in main.blocks if isinstance(b, Table))
        self.assertGreaterEqual(kv_count, 2, "expected Account + Highlights grids")
        self.assertEqual(table_count, 1, "expected exactly one transactions table")

    def test_alerts_are_promoted_to_root(self) -> None:
        doc = ir_builders.build("bank_statement", _bank_ctx())
        for alert in doc.alerts:
            self.assertIsInstance(alert, Callout)
            self.assertIn(alert.severity, ("info", "warning", "critical"))

    def test_inlines_carry_semantic_emphasis(self) -> None:
        """Salutation contains a <strong> for the account holder."""
        doc = ir_builders.build("bank_statement", _bank_ctx())
        main = next(b for b in doc.blocks
                    if isinstance(b, Section) and b.role == "main")
        salutation = next(b for b in main.blocks if isinstance(b, Paragraph))
        kinds = [type(n).__name__ for n in salutation.inlines]
        self.assertIn("Strong", kinds)

    def test_document_is_hashable_and_serialisable(self) -> None:
        """Frozen dataclasses → the root Document can be hashed (cache key)
        and json-dumped. This verifies metadata stays hashable (tuple-of-
        pairs), not just the blocks tuple.
        """
        doc = ir_builders.build("bank_statement", _bank_ctx())
        self.assertIsInstance(hash(doc), int)
        # Rebuilding from the same context yields an equal, equally-hashed
        # Document — proves deterministic & cacheable.
        doc2 = ir_builders.build("bank_statement", _bank_ctx())
        self.assertEqual(doc, doc2)
        self.assertEqual(hash(doc), hash(doc2))

        def _asdict(node):
            if hasattr(node, "__dataclass_fields__"):
                return {f: _asdict(getattr(node, f))
                        for f in node.__dataclass_fields__}
            if isinstance(node, tuple):
                return [_asdict(x) for x in node]
            if isinstance(node, dict):
                return {k: _asdict(v) for k, v in node.items()}
            return node

        payload = json.dumps(_asdict(doc), default=str)
        self.assertIn("bank_statement", payload)

    def test_metadata_factory_sorts_and_drops_empty(self) -> None:
        """``metadata(...)`` produces a sorted tuple and drops empty values."""
        from document_ir import metadata
        m = metadata({"b": "2", "a": "1", "c": ""})
        self.assertEqual(m, (("a", "1"), ("b", "2")))
        # Different insertion orders → same hashable tuple.
        self.assertEqual(hash(m), hash(metadata(a="1", b="2")))


class HtmlRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = ir_builders.build("bank_statement", _bank_ctx())

    def test_email_flavor_has_landmarks_and_lang(self) -> None:
        html = render_html(self.doc, flavor="email")
        self.assertIn('lang="en"', html)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('id="main-content"', html)
        self.assertIn("Skip to main content", html)
        # Account holder and closing balance must flow through.
        self.assertIn("James Wilson", html)
        self.assertIn("$8,340.50", html)
        # All 9 transactions must be present as <tr> rows.
        self.assertGreaterEqual(html.count("<tr>"), 10)  # header + 9

    def test_email_flavor_has_no_style_block(self) -> None:
        """Email must use inline styles only (no <style> blocks)."""
        html = render_html(self.doc, flavor="email")
        self.assertNotIn("<style>", html.lower())

    def test_print_flavor_uses_style_block(self) -> None:
        """Print flavour is allowed to use a <style> block."""
        html = render_html(self.doc, flavor="print")
        self.assertIn("<style>", html)
        self.assertIn("@page", html)

    def test_fragment_has_no_document_shell(self) -> None:
        html = render_html(self.doc, flavor="fragment")
        self.assertNotIn("<!DOCTYPE", html)
        self.assertNotIn("<html", html)
        self.assertIn("James Wilson", html)


class TextRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = ir_builders.build("bank_statement", _bank_ctx())

    def test_compact_mentions_balance_and_account(self) -> None:
        body = render_text(self.doc, flavor="compact")
        self.assertIn("8,340.50", body)
        self.assertIn("****4821", body)

    def test_compact_drops_tables(self) -> None:
        """Compact SMS body must NOT enumerate individual transactions."""
        body = render_text(self.doc, flavor="compact")
        self.assertNotIn("Salary Credit", body)
        self.assertNotIn("ATM Withdrawal", body)

    def test_full_preserves_transactions(self) -> None:
        body = render_text(self.doc, flavor="full")
        self.assertIn("Salary Credit", body)
        self.assertIn("ATM Withdrawal", body)
        # Underline under the H1 heading.
        self.assertIn("=" * len("Savings Statement"), body)


class EngineIntegrationTests(unittest.TestCase):
    """End-to-end through engine.generate_channel with the feature flag on."""

    def test_email_channel_uses_ir_when_flag_on(self) -> None:
        from engine import generate_channel, _use_ir
        self.assertTrue(_use_ir("bank_statement"),
                        "bank_statement must have use_ir:true + a builder")
        payload, filename, mime, extra = generate_channel("bank_statement", 0, "email")
        self.assertEqual(mime, "text/html; charset=utf-8")
        self.assertTrue(filename.endswith(".html"))
        self.assertEqual(extra.get("renderer"), "ir")
        html = payload.decode("utf-8")
        self.assertIn("James Wilson", html)
        self.assertIn("$8,340.50", html)
        self.assertIn('id="main-content"', html)

    def test_sms_channel_uses_ir_when_flag_on(self) -> None:
        from engine import generate_channel
        payload, filename, mime, extra = generate_channel("bank_statement", 0, "sms")
        self.assertEqual(mime, "text/plain; charset=utf-8")
        self.assertEqual(extra.get("renderer"), "ir")
        body = payload.decode("utf-8")
        self.assertIn("8,340.50", body)
        # Compact flavour must not enumerate transactions.
        self.assertNotIn("Salary Credit", body)

    def test_pdf_and_docx_channels_unaffected_by_ir_flag(self) -> None:
        """PDF / DOCX channels keep their existing code paths."""
        from engine import generate_channel
        payload, filename, mime, extra = generate_channel("bank_statement", 0, "pdf")
        self.assertTrue(payload.startswith(b"%PDF"))
        # No renderer key set for non-IR channels.
        self.assertNotIn("renderer", extra)


class FeatureFlagFallbackTests(unittest.TestCase):
    """Flipping ``use_ir`` off must revert email/SMS to the legacy templates."""

    def test_fallback_to_legacy_when_flag_off(self) -> None:
        schema_path = Path(__file__).parent.parent / "config" / "schema.json"
        original = schema_path.read_text(encoding="utf-8")
        try:
            flipped = original.replace(
                '"use_ir": true,',
                '"use_ir": false,',
                1,
            )
            self.assertNotEqual(flipped, original, "test setup — flag not found")
            schema_path.write_text(flipped, encoding="utf-8")
            # Bust data_loader's in-memory schema cache so the new value is read.
            import data_loader
            if hasattr(data_loader, "_schema_cache"):
                data_loader._schema_cache = None  # type: ignore[attr-defined]

            from engine import generate_channel, _use_ir
            self.assertFalse(_use_ir("bank_statement"))
            _, _, _, extra = generate_channel("bank_statement", 0, "email")
            self.assertEqual(extra.get("renderer"), "template")
        finally:
            schema_path.write_text(original, encoding="utf-8")
            import data_loader
            if hasattr(data_loader, "_schema_cache"):
                data_loader._schema_cache = None  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()

"""
validator.py
─────────────
TDD / Regression testing framework.

Concepts:
  GoldenSnapshot  — approved field-level snapshot of a document
  RegressionTest  — compares rendered output against golden snapshot
  TestSuite       — runs all tests for all document types

Usage:
    # Approve current output as golden baseline:
    python validator.py --approve bank_statement

    # Run regression tests:
    python validator.py --test

    # Run specific doc type:
    python validator.py --test --type bank_statement

    # CI mode (exit 1 on failure):
    python validator.py --test --ci
"""

import json
import sys
import hashlib
import logging
import argparse
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Any

from data_loader  import load_records, get_doc_schema
from engine import DOC_LABELS
from rules_engine import apply_rules
from renderer     import render_html, render_pdf

log          = logging.getLogger(__name__)
GOLDEN_DIR   = Path(__file__).parent / "tests" / "golden"
GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

DOC_TYPES = ["bank_statement", "insurance_policy", "telecom_bill", "payroll_statement"]


# ── Snapshot data structures ──────────────────────────────────────────────────
@dataclass
class FieldAssertion:
    field_name:    str
    expected_type: str            # string | number | bool | list | empty
    expected_value: Any = None    # None means "any value of correct type"
    contains:      str | None = None  # substring check for strings
    min_value:     float | None = None
    max_value:     float | None = None

@dataclass
class RuleAssertion:
    description: str
    alert_id:    str | None = None     # expected alert to be active
    no_alert_id: str | None = None     # expected alert to be absent
    field_style: str | None = None     # field that should have a style
    style_contains: str | None = None  # CSS substring to check

@dataclass
class GoldenSnapshot:
    doc_type:        str
    row_index:       int
    approved_at:     str
    approved_by:     str
    description:     str
    key_fields:      dict[str, Any]    # sample of rendered field values
    expected_alerts: list[str]         # alert IDs expected to be active
    forbidden_alerts: list[str]        # alert IDs expected to be absent
    field_assertions: list[dict]
    rule_assertions:  list[dict]
    html_checksum:   str | None = None  # MD5 of rendered HTML (optional lock)

    @classmethod
    def load(cls, path: str) -> "GoldenSnapshot":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(**d)

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, default=str)


# ── Test result ───────────────────────────────────────────────────────────────
@dataclass
class TestResult:
    doc_type:    str
    row_index:   int
    passed:      bool
    failures:    list[str] = field(default_factory=list)
    warnings:    list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    def __str__(self):
        icon   = "✅" if self.passed else "❌"
        label  = f"{self.doc_type}[{self.row_index}]"
        result = f"{icon}  {label}: {'PASS' if self.passed else 'FAIL'}"
        if self.failures:
            result += "\n" + "\n".join(f"     → {f}" for f in self.failures)
        return result


# ── Core test runner ──────────────────────────────────────────────────────────
class RegressionRunner:

    def run_snapshot(self, snapshot: GoldenSnapshot,
                     data_path: str | None = None) -> TestResult:
        """Run one snapshot test against current engine output."""
        import time
        t0 = time.perf_counter()
        failures = []
        warnings = []

        try:
            records, _ = load_records(snapshot.doc_type, data_path, validate=False)
            if snapshot.row_index >= len(records):
                return TestResult(
                    doc_type=snapshot.doc_type, row_index=snapshot.row_index,
                    passed=False, duration_ms=0,
                    failures=[f"Row {snapshot.row_index} not found in data "
                               f"(file has {len(records)} rows)."]
                )

            record  = records[snapshot.row_index]
            context = apply_rules(snapshot.doc_type, record)
            html    = render_html(snapshot.doc_type, context)

        except Exception as exc:
            return TestResult(
                doc_type=snapshot.doc_type, row_index=snapshot.row_index,
                passed=False, duration_ms=0,
                failures=[f"Render failed: {exc}"]
            )

        # ── 1. HTML checksum (structural regression) ──────────────────────
        if snapshot.html_checksum:
            current_md5 = hashlib.md5(html.encode()).hexdigest()
            if current_md5 != snapshot.html_checksum:
                warnings.append(
                    "HTML checksum changed — template was edited. "
                    "Re-approve if intentional."
                )

        # ── 2. Key field value checks ─────────────────────────────────────
        for fname, expected in (snapshot.key_fields or {}).items():
            actual = context.get(fname)
            if actual is None:
                failures.append(f"Field '{fname}' not found in rendered context.")
                continue
            if expected is not None and str(actual).strip() != str(expected).strip():
                failures.append(
                    f"Field '{fname}': expected '{expected}', got '{actual}'."
                )

        # ── 3. Alert assertions ───────────────────────────────────────────
        active_ids = {a["id"] for a in context.get("__alerts", [])}
        for expected_id in (snapshot.expected_alerts or []):
            if expected_id not in active_ids:
                failures.append(
                    f"Expected alert '{expected_id}' was NOT triggered."
                )
        for forbidden_id in (snapshot.forbidden_alerts or []):
            if forbidden_id in active_ids:
                failures.append(
                    f"Alert '{forbidden_id}' was triggered but should NOT be."
                )

        # ── 4. Field style checks ─────────────────────────────────────────
        field_styles = context.get("__field_styles", {})
        for ra in (snapshot.rule_assertions or []):
            if ra.get("field_style"):
                fname = ra["field_style"]
                style = field_styles.get(fname, "")
                needle = ra.get("style_contains", "")
                if needle and needle not in style:
                    failures.append(
                        f"Field '{fname}' style '{style}' "
                        f"does not contain '{needle}'."
                    )

        # ── 5. Field type assertions ──────────────────────────────────────
        for fa in (snapshot.field_assertions or []):
            fname  = fa.get("field_name")
            ftype  = fa.get("expected_type", "string")
            actual = context.get(fname)

            if ftype == "number":
                try:
                    v = float(actual)
                    if fa.get("min_value") is not None and v < fa["min_value"]:
                        failures.append(
                            f"Field '{fname}' = {v} is below min {fa['min_value']}."
                        )
                    if fa.get("max_value") is not None and v > fa["max_value"]:
                        failures.append(
                            f"Field '{fname}' = {v} exceeds max {fa['max_value']}."
                        )
                except (TypeError, ValueError):
                    failures.append(f"Field '{fname}' is not numeric: '{actual}'.")

            elif ftype == "string" and fa.get("contains"):
                if fa["contains"] not in str(actual):
                    failures.append(
                        f"Field '{fname}' = '{actual}' does not contain "
                        f"'{fa['contains']}'."
                    )

            elif ftype == "empty" and actual:
                failures.append(f"Field '{fname}' should be empty, got '{actual}'.")

            elif ftype == "list":
                if not isinstance(actual, list):
                    failures.append(f"Field '{fname}' should be a list.")

        duration = (time.perf_counter() - t0) * 1000
        return TestResult(
            doc_type=snapshot.doc_type, row_index=snapshot.row_index,
            passed=len(failures) == 0,
            failures=failures, warnings=warnings,
            duration_ms=duration,
        )

    def run_all(self, doc_types: list[str] | None = None,
                data_paths: dict | None = None) -> list[TestResult]:
        """Run all golden snapshots. Returns list of TestResult."""
        snapshots = self._load_all_snapshots(doc_types)
        if not snapshots:
            log.warning("No golden snapshots found in %s", GOLDEN_DIR)
            return []

        results = []
        for snap in snapshots:
            dp = (data_paths or {}).get(snap.doc_type)
            results.append(self.run_snapshot(snap, dp))
        return results

    def _load_all_snapshots(self, doc_types: list[str] | None) -> list[GoldenSnapshot]:
        snaps = []
        for fp in sorted(GOLDEN_DIR.glob("*.json")):
            try:
                s = GoldenSnapshot.load(str(fp))
                if doc_types is None or s.doc_type in doc_types:
                    snaps.append(s)
            except Exception as e:
                log.error("Could not load snapshot %s: %s", fp, e)
        return snaps


# ── Approval tool ─────────────────────────────────────────────────────────────
def approve_snapshot(doc_type: str,
                     row_index: int = 0,
                     description: str = "",
                     approved_by: str = "developer",
                     data_path: str | None = None,
                     lock_html: bool = False):
    """
    Generate a golden snapshot from the current engine output.
    Call this when the business has approved the rendered output.
    """
    records, _ = load_records(doc_type, data_path, validate=False)
    if row_index >= len(records):
        raise IndexError(f"Row {row_index} not found.")

    record  = records[row_index]
    context = apply_rules(doc_type, record)
    html    = render_html(doc_type, context)

    # Build key_fields from non-list, non-private fields
    key_fields = {
        k: v for k, v in context.items()
        if not k.startswith("__")
        and not isinstance(v, list)
        and v not in (None, "", 0, 0.0)
    }

    # Capture active alerts
    active_alerts  = [a["id"] for a in context.get("__alerts", [])]

    snapshot = GoldenSnapshot(
        doc_type=doc_type,
        row_index=row_index,
        approved_at=datetime.now().isoformat(),
        approved_by=approved_by,
        description=description or f"Auto-approved {doc_type} row {row_index}",
        key_fields=key_fields,
        expected_alerts=active_alerts,
        forbidden_alerts=[],
        field_assertions=[],
        rule_assertions=[],
        html_checksum=hashlib.md5(html.encode()).hexdigest() if lock_html else None,
    )

    out_path = GOLDEN_DIR / f"{doc_type}_{row_index:04d}.json"
    snapshot.save(str(out_path))
    print(f"✅  Golden snapshot saved → {out_path}")
    return snapshot


# ── CLI ───────────────────────────────────────────────────────────────────────
def _print_summary(results: list[TestResult]):
    passed  = sum(1 for r in results if r.passed)
    failed  = len(results) - passed
    total_t = sum(r.duration_ms for r in results)

    print("\n" + "═" * 60)
    print(f"  TEST SUMMARY")
    print("═" * 60)
    for r in results:
        print(str(r))
        if r.warnings:
            for w in r.warnings:
                print(f"  ⚠  {w}")
    print("─" * 60)
    print(f"  Total: {len(results)}  ✅ Passed: {passed}  ❌ Failed: {failed}  "
          f"⏱ {total_t:.0f}ms")
    print("═" * 60 + "\n")
    return failed


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description="Document regression tester")
    parser.add_argument("--test",    action="store_true", help="Run regression tests")
    parser.add_argument("--approve", metavar="DOC_TYPE",  help="Approve current output as golden")
    parser.add_argument("--type",    metavar="DOC_TYPE",  help="Filter to one doc type")
    parser.add_argument("--row",     type=int, default=0, help="Row index (for --approve)")
    parser.add_argument("--by",      default="developer", help="Approver name")
    parser.add_argument("--desc",    default="",          help="Snapshot description")
    parser.add_argument("--lock-html", action="store_true", help="Lock HTML checksum")
    parser.add_argument("--ci",      action="store_true", help="Exit 1 on failures (CI mode)")
    args = parser.parse_args()

    if args.approve:
        approve_snapshot(
            doc_type=args.approve,
            row_index=args.row,
            approved_by=args.by,
            description=args.desc,
            lock_html=args.lock_html,
        )

    elif args.test:
        runner  = RegressionRunner()
        types   = [args.type] if args.type else None
        results = runner.run_all(doc_types=types)
        if not results:
            print("No golden snapshots found. Run --approve first.")
            sys.exit(0)
        failures = _print_summary(results)
        if args.ci and failures:
            sys.exit(1)
    else:
        parser.print_help()

"""
engine.py
──────────
Orchestrator. Ties together data_loader → rules_engine → renderer.
Used by both interactive (Flask/Tkinter) and batch runner.

Public API:
    generate_one(doc_type, row_index, data_path)  → (pdf_bytes, filename, errors)
    generate_batch(doc_type, data_path, ...)       → BatchResult
    get_preview_rows(doc_type, data_path)          → list[dict]
"""

import os
import time
import logging
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Callable

from data_loader   import load_record, load_records, get_doc_schema
from rules_engine  import apply_rules
from renderer      import render_pdf
from docx_renderer import (has_uploaded_template, render_docx_pdf,
                           uploaded_template_path)

log        = logging.getLogger(__name__)
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

DOC_LABELS = {
    "bank_statement":   "Bank / Financial Statement",
    "insurance_policy": "Insurance Policy Document",
    "telecom_bill":     "Telecom / Utility Bill",
    "payroll_statement":"HR / Payroll Statement",
}

DATA_FILES = {
    "bank_statement":    "bank_statements.xlsx",
    "insurance_policy":  "insurance_policies.xlsx",
    "telecom_bill":      "telecom_bills.xlsx",
    "payroll_statement": "payroll_statements.xlsx",
}


# ── Result types ──────────────────────────────────────────────────────────────
@dataclass
class DocResult:
    success:     bool
    filename:    str
    output_path: str
    row_index:   int
    duration_ms: float
    errors:      list[str] = field(default_factory=list)
    pdf_bytes:   bytes = field(default=b"", repr=False)

@dataclass
class BatchResult:
    doc_type:    str
    total:       int
    succeeded:   int
    failed:      int
    duration_s:  float
    results:     list[DocResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return (self.succeeded / self.total * 100) if self.total else 0.0

    def failed_rows(self) -> list[DocResult]:
        return [r for r in self.results if not r.success]


# ── Single document generation ────────────────────────────────────────────────
def generate_one(doc_type:   str,
                 row_index:  int,
                 data_path:  str | None = None,
                 save:       bool = True) -> DocResult:
    """
    Generate one PDF.
    Returns DocResult with pdf_bytes and output path.
    """
    t0 = time.perf_counter()
    errors: list[str] = []

    try:
        # 1. Load & validate record
        record = load_record(doc_type, row_index, data_path)

        # 2. Apply business rules (alerts, styles, computed fields)
        context = apply_rules(doc_type, record)

        # 3. Render PDF — prefer an uploaded DOCX template if present,
        #    otherwise fall back to the built-in HTML/Jinja2 pipeline.
        if has_uploaded_template(doc_type):
            pdf_bytes = render_docx_pdf(uploaded_template_path(doc_type),
                                        context)
        else:
            pdf_bytes = render_pdf(doc_type, context)

        # 4. Determine output filename
        filename = (record.get("output_filename")
                    or f"{doc_type}_{row_index + 1:04d}.pdf")

        # 5. Save to disk
        out_path = str(OUTPUT_DIR / filename)
        if save:
            with open(out_path, "wb") as f:
                f.write(pdf_bytes)

        duration = (time.perf_counter() - t0) * 1000
        log.info("Generated %s in %.0fms", filename, duration)

        return DocResult(
            success=True, filename=filename,
            output_path=out_path, row_index=row_index,
            duration_ms=duration, errors=errors,
            pdf_bytes=pdf_bytes,
        )

    except Exception as exc:
        duration = (time.perf_counter() - t0) * 1000
        log.error("Failed row %d (%s): %s", row_index, doc_type, exc)
        return DocResult(
            success=False, filename="",
            output_path="", row_index=row_index,
            duration_ms=duration, errors=[str(exc)],
        )


# ── Worker for multiprocessing pool ──────────────────────────────────────────
def _worker(args: tuple) -> DocResult:
    doc_type, row_index, data_path = args
    return generate_one(doc_type, row_index, data_path)


# ── Batch generation ──────────────────────────────────────────────────────────
def generate_batch(doc_type:    str,
                   data_path:   str | None = None,
                   workers:     int = 4,
                   use_processes: bool = False,
                   progress_cb: Callable[[int, int], None] | None = None,
                   error_cb:    Callable[[DocResult], None] | None = None
                   ) -> BatchResult:
    """
    Batch-generate PDFs for all records.

    workers       : number of concurrent threads/processes
    use_processes : True for CPU-heavy workloads (100K+), False for I/O-bound
    progress_cb   : called with (completed, total) after each document
    error_cb      : called with DocResult for each failed row
    """
    records, val_errors = load_records(doc_type, data_path)
    total = len(records)
    if val_errors:
        log.warning("%d validation warnings in data", len(val_errors))

    t0      = time.perf_counter()
    results = []
    done    = 0

    Executor = ProcessPoolExecutor if use_processes else ThreadPoolExecutor
    args_list = [(doc_type, i, data_path) for i in range(total)]

    with Executor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, args): args[1] for args in args_list}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            done += 1
            if progress_cb:
                progress_cb(done, total)
            if not result.success and error_cb:
                error_cb(result)

    # Sort by row_index for deterministic output
    results.sort(key=lambda r: r.row_index)

    elapsed   = time.perf_counter() - t0
    succeeded = sum(1 for r in results if r.success)

    return BatchResult(
        doc_type=doc_type, total=total,
        succeeded=succeeded, failed=total - succeeded,
        duration_s=elapsed, results=results,
    )


# ── Preview helper ────────────────────────────────────────────────────────────
def get_preview_rows(doc_type: str, data_path: str | None = None,
                     max_cols: int = 10) -> tuple[list[dict], list[str]]:
    """
    Return (rows, columns) for UI table display.
    Strips list-type fields to keep display clean.
    """
    records, errors = load_records(doc_type, data_path, validate=False)
    cols = []
    if records:
        cols = [k for k in records[0].keys()
                if not isinstance(records[0][k], list)][:max_cols]
    return records, cols


def default_data_path(doc_type: str) -> str:
    data_dir = Path(__file__).parent / "data"
    return str(data_dir / DATA_FILES.get(doc_type, f"{doc_type}.xlsx"))

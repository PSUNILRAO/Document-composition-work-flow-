"""
batch_runner.py
────────────────
Production batch runner.

Modes:
  Interactive  — generate one document, stream to stdout/UI
  Small batch  — ThreadPoolExecutor, real-time progress bar
  Large batch  — chunked ProcessPoolExecutor, audit log, retry on failure

Usage:
    # Single doc:
    python batch_runner.py --type bank_statement --row 0

    # Full batch:
    python batch_runner.py --type bank_statement --all

    # All doc types overnight:
    python batch_runner.py --all-types

    # With concurrency tuning:
    python batch_runner.py --type payroll_statement --all --workers 8

    # Retry only failed rows from previous run:
    python batch_runner.py --type bank_statement --retry-failed

    # Schedule a nightly run at 02:00:
    python batch_runner.py --schedule "02:00" --all-types
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

from engine import (generate_one, generate_batch,
                    get_preview_rows, default_data_path,
                    DOC_LABELS, BatchResult, DocResult)

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "batch.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("batch")


# ── Progress bar ──────────────────────────────────────────────────────────────
def _progress(done: int, total: int, label: str = "", width: int = 40):
    pct   = done / total if total else 0
    filled = int(width * pct)
    bar    = "█" * filled + "░" * (width - filled)
    sys.stdout.write(f"\r  [{bar}] {done}/{total} ({pct:.0%}) {label}    ")
    sys.stdout.flush()
    if done == total:
        print()


# ── Audit log ─────────────────────────────────────────────────────────────────
def write_audit_log(result: BatchResult, run_id: str):
    log_path = LOG_DIR / f"audit_{run_id}.json"
    summary = {
        "run_id":       run_id,
        "doc_type":     result.doc_type,
        "started_at":   datetime.now().isoformat(),
        "total":        result.total,
        "succeeded":    result.succeeded,
        "failed":       result.failed,
        "success_rate": f"{result.success_rate:.1f}%",
        "duration_s":   round(result.duration_s, 2),
        "rate_per_min": round(result.total / result.duration_s * 60, 0) if result.duration_s else 0,
        "failures": [
            {"row": r.row_index, "error": r.errors}
            for r in result.results if not r.success
        ],
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return log_path


# ── Retry store ───────────────────────────────────────────────────────────────
RETRY_FILE = LOG_DIR / "failed_rows.json"

def save_failed_rows(doc_type: str, result: BatchResult):
    data = {}
    if RETRY_FILE.exists():
        with open(RETRY_FILE) as f:
            data = json.load(f)
    data[doc_type] = [r.row_index for r in result.results if not r.success]
    with open(RETRY_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_failed_rows(doc_type: str) -> list[int]:
    if not RETRY_FILE.exists():
        return []
    with open(RETRY_FILE) as f:
        return json.load(f).get(doc_type, [])


# ── Print helpers ─────────────────────────────────────────────────────────────
def print_header(label: str):
    print("\n" + "═" * 62)
    print(f"  {label}")
    print("═" * 62)

def print_result_summary(result: BatchResult, log_path: str = ""):
    rate = result.total / result.duration_s * 60 if result.duration_s else 0
    print(f"\n  {'Doc type':<22} {DOC_LABELS.get(result.doc_type, result.doc_type)}")
    print(f"  {'Total records':<22} {result.total}")
    print(f"  {'Succeeded':<22} {result.succeeded}  ✅")
    print(f"  {'Failed':<22} {result.failed}  {'❌' if result.failed else '—'}")
    print(f"  {'Success rate':<22} {result.success_rate:.1f}%")
    print(f"  {'Duration':<22} {result.duration_s:.2f}s")
    print(f"  {'Rate':<22} {rate:.0f} docs/min")
    if log_path:
        print(f"  {'Audit log':<22} {log_path}")
    if result.failed_rows():
        print(f"\n  Failed rows:")
        for r in result.failed_rows():
            print(f"    Row {r.row_index}: {r.errors[0] if r.errors else 'unknown'}")


# ── Interactive single-document run ──────────────────────────────────────────
def run_interactive(doc_type: str, row_index: int,
                    data_path: str | None = None) -> DocResult:
    print_header(f"INTERACTIVE — {DOC_LABELS.get(doc_type, doc_type)}")
    print(f"  Row     : {row_index}")
    print(f"  Data    : {data_path or default_data_path(doc_type)}")
    print()

    result = generate_one(doc_type, row_index, data_path)

    if result.success:
        print(f"  ✅  {result.filename}  ({result.duration_ms:.0f}ms)")
        print(f"  📂  {result.output_path}")
    else:
        print(f"  ❌  Failed: {result.errors}")
    return result


# ── Batch run ─────────────────────────────────────────────────────────────────
def run_batch(doc_type:  str,
              data_path: str | None = None,
              workers:   int = 4,
              row_filter: list[int] | None = None) -> BatchResult:
    """
    Run batch for one doc type.
    row_filter: if set, only process these row indices (used for retry).
    """
    print_header(f"BATCH — {DOC_LABELS.get(doc_type, doc_type)}")

    # If filtering rows, generate one-at-a-time with progress
    if row_filter is not None:
        results  = []
        total    = len(row_filter)
        t0       = time.perf_counter()
        for done, idx in enumerate(row_filter, 1):
            r = generate_one(doc_type, idx, data_path)
            results.append(r)
            _progress(done, total, r.filename or "ERROR")
        elapsed   = time.perf_counter() - t0
        succeeded = sum(1 for r in results if r.success)
        batch_res = BatchResult(
            doc_type=doc_type, total=total,
            succeeded=succeeded, failed=total-succeeded,
            duration_s=elapsed, results=results,
        )
    else:
        # Full parallel batch
        label_buf = [""]
        def _progress_cb(done, total):
            _progress(done, total, label_buf[0])
        def _error_cb(result: DocResult):
            label_buf[0] = f"⚠ row {result.row_index} failed"

        batch_res = generate_batch(
            doc_type=doc_type,
            data_path=data_path,
            workers=workers,
            progress_cb=_progress_cb,
            error_cb=_error_cb,
        )

    run_id   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = write_audit_log(batch_res, f"{doc_type}_{run_id}")
    save_failed_rows(doc_type, batch_res)
    print_result_summary(batch_res, str(log_path))
    return batch_res


# ── All doc types run ─────────────────────────────────────────────────────────
def run_all_types(workers: int = 4):
    print_header("OVERNIGHT BATCH — ALL DOCUMENT TYPES")
    overall_start = time.perf_counter()
    totals = {"total": 0, "succeeded": 0, "failed": 0}

    for doc_type in DOC_LABELS.keys():
        result = run_batch(doc_type, workers=workers)
        totals["total"]     += result.total
        totals["succeeded"] += result.succeeded
        totals["failed"]    += result.failed

    elapsed = time.perf_counter() - overall_start
    print_header("OVERALL SUMMARY")
    print(f"  Documents processed : {totals['total']}")
    print(f"  Succeeded           : {totals['succeeded']}  ✅")
    print(f"  Failed              : {totals['failed']}  {'❌' if totals['failed'] else '—'}")
    print(f"  Total duration      : {elapsed:.1f}s")
    print(f"  Audit logs          : {LOG_DIR}/")


# ── Scheduler ─────────────────────────────────────────────────────────────────
def schedule_nightly(run_time: str, doc_types: list[str] | None = None,
                     workers: int = 4):
    """
    Block and run batch at a fixed daily time.
    run_time format: "HH:MM"  e.g. "02:00"
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("APScheduler not installed. Run: pip install apscheduler")
        sys.exit(1)

    h, m = run_time.split(":")
    print(f"\n  ⏰  Scheduled nightly batch at {run_time}")
    print("  Press Ctrl+C to stop.\n")

    def _job():
        if doc_types:
            for dt in doc_types:
                run_batch(dt, workers=workers)
        else:
            run_all_types(workers=workers)

    scheduler = BlockingScheduler()
    scheduler.add_job(_job, CronTrigger(hour=int(h), minute=int(m)))
    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\n  Scheduler stopped.")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Document batch runner")

    parser.add_argument("--type",          metavar="DOC_TYPE", help="Document type")
    parser.add_argument("--row",           type=int, default=0, help="Row index (interactive)")
    parser.add_argument("--all",           action="store_true", help="Batch all rows")
    parser.add_argument("--all-types",     action="store_true", help="Run all document types")
    parser.add_argument("--workers",       type=int, default=4, help="Parallel workers")
    parser.add_argument("--retry-failed",  action="store_true", help="Retry previously failed rows")
    parser.add_argument("--data",          metavar="PATH",      help="Custom data file path")
    parser.add_argument("--schedule",      metavar="HH:MM",     help="Schedule nightly batch at this time")
    parser.add_argument("--list-types",    action="store_true", help="List available document types")

    args = parser.parse_args()

    if args.list_types:
        print("\n  Available document types:")
        for k, v in DOC_LABELS.items():
            print(f"    {k:<24} {v}")
        print()
        sys.exit(0)

    if args.schedule:
        types = [args.type] if args.type else None
        schedule_nightly(args.schedule, types, workers=args.workers)

    elif args.all_types:
        run_all_types(workers=args.workers)

    elif args.type:
        if args.retry_failed:
            failed = load_failed_rows(args.type)
            if not failed:
                print(f"  No failed rows recorded for '{args.type}'.")
            else:
                run_batch(args.type, data_path=args.data, row_filter=failed)
        elif args.all:
            run_batch(args.type, data_path=args.data, workers=args.workers)
        else:
            run_interactive(args.type, args.row, data_path=args.data)
    else:
        parser.print_help()

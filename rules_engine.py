"""
rules_engine.py
────────────────
Evaluates rules.yaml against a record dict.
Produces:
  - active_alerts   : list of {id, severity, message} to show in document
  - field_styles    : dict of field_name → CSS style string
  - computed_fields : extra derived values (days_to_expiry, data_pct, etc.)

No HTML or PDF logic here — pure rule evaluation.
"""

import ast
import operator as _op
import re
import logging
from pathlib import Path
from datetime import datetime, date as date_type

import yaml

log = logging.getLogger(__name__)

RULES_PATH = Path(__file__).parent / "config" / "rules.yaml"
# Backwards-compatibility: rules.yaml used to live at the repo root; fall back
# to that location when the config/ directory is not present.
if not RULES_PATH.exists():
    _root_rules = Path(__file__).parent / "rules.yaml"
    if _root_rules.exists():
        RULES_PATH = _root_rules
_rules_cache: dict | None = None


# ── YAML loader ───────────────────────────────────────────────────────────────
def load_rules() -> dict:
    global _rules_cache
    if _rules_cache is None:
        with open(RULES_PATH, encoding="utf-8") as f:
            _rules_cache = yaml.safe_load(f)
    return _rules_cache

def reload_rules():
    """Force reload — call after editing rules.yaml without restarting."""
    global _rules_cache
    _rules_cache = None
    return load_rules()


# ── Safe expression evaluator ─────────────────────────────────────────────────
#
# The previous implementation used `eval()` on a string built from the YAML
# rules. `__builtins__={}` is a well-known incomplete sandbox (attackers can
# escape via `().__class__.__mro__[...]`), so we replaced it with a strict
# AST walker that only accepts the operators actually used in rules.yaml:
#
#   ==  !=  <  <=  >  >=  in  not in
#   and  or  not
#   unary +/-
#   identifier lookups against the supplied context
#   literal constants (numbers, strings, True/False/None)
#
# Anything else — function calls, attribute access, subscripts, imports,
# comprehensions, lambdas, etc. — causes the expression to evaluate to False.

_ALLOWED_CMPOPS: dict[type, object] = {
    ast.Eq:    _op.eq,
    ast.NotEq: _op.ne,
    ast.Lt:    _op.lt,
    ast.LtE:   _op.le,
    ast.Gt:    _op.gt,
    ast.GtE:   _op.ge,
    ast.In:    lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_ALLOWED_UNARYOPS: dict[type, object] = {
    ast.USub: _op.neg,
    ast.UAdd: _op.pos,
    ast.Not:  _op.not_,
}

# YAML-style lowercase booleans / none supported for convenience.
_EXTRA_NAMES = {
    "true":  True,  "True":  True,
    "false": False, "False": False,
    "null":  None,  "None":  None,
}


def _coerce_for_compare(left, right):
    """If one side of a comparison is numeric-looking, coerce both to float."""
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left, right
    try:
        return float(left), float(right)
    except (TypeError, ValueError):
        return left, right


def _safe_eval(expr: str, context: dict) -> bool:
    """
    Evaluate a simple boolean expression string against the context dict.
    Returns False for any disallowed construct or runtime error.
    """
    if not isinstance(expr, str):
        return bool(expr)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        log.debug("Rule syntax error: %r", expr)
        return False

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)

        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            name = node.id
            if name in _EXTRA_NAMES:
                return _EXTRA_NAMES[name]
            if name in context:
                val = context[name]
                # Preserve the previous behaviour of treating missing
                # numeric values as 0 for comparisons.
                return 0 if val is None else val
            # Unknown identifier — surface as None; comparisons then fail.
            return None

        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
            return _ALLOWED_UNARYOPS[type(node.op)](_eval(node.operand))

        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result = True
                for child in node.values:
                    result = result and _eval(child)
                    if not result:
                        break
                return result
            if isinstance(node.op, ast.Or):
                result = False
                for child in node.values:
                    result = result or _eval(child)
                    if result:
                        break
                return result
            raise ValueError("boolean op not allowed")

        if isinstance(node, ast.Compare):
            left = _eval(node.left)
            for op_node, comparator in zip(node.ops, node.comparators):
                op_type = type(op_node)
                if op_type not in _ALLOWED_CMPOPS:
                    raise ValueError(f"comparison operator not allowed: {op_type.__name__}")
                right = _eval(comparator)
                fn = _ALLOWED_CMPOPS[op_type]
                try:
                    ok = fn(left, right)
                except TypeError:
                    # e.g. comparing str with int — coerce numerically if possible.
                    l2, r2 = _coerce_for_compare(left, right)
                    try:
                        ok = fn(l2, r2)
                    except TypeError:
                        return False
                if not ok:
                    return False
                left = right
            return True

        raise ValueError(f"disallowed expression node: {type(node).__name__}")

    try:
        return bool(_eval(tree))
    except Exception as exc:
        log.debug("Rule eval failed for %r: %s", expr, exc)
        return False


# ── Computed fields ───────────────────────────────────────────────────────────
def _compute_fields(doc_type: str, record: dict) -> dict:
    """Derive extra fields that rules may reference."""
    computed = {}

    today = datetime.today()

    # ── Date-based derivations ─────────────────────────────────────────────
    def _parse_date(v) -> datetime | None:
        if not v:
            return None
        if isinstance(v, (datetime, date_type)):
            return datetime.combine(v, datetime.min.time()) if isinstance(v, date_type) else v
        for fmt in ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(str(v), fmt)
            except ValueError:
                pass
        return None

    if doc_type == "insurance_policy":
        expiry = _parse_date(record.get("expiry_date"))
        if expiry:
            computed["days_to_expiry"] = (expiry - today).days
        computed["premium_due"] = record.get("premium_due", False)

    if doc_type == "telecom_bill":
        due = _parse_date(record.get("due_date"))
        if due:
            overdue = (today - due).days
            computed["days_overdue"] = max(0, overdue)
        else:
            computed["days_overdue"] = float(record.get("days_overdue") or 0)
        data_used  = float(record.get("data_used_gb") or 0)
        data_limit = float(record.get("data_limit_gb") or 1)
        computed["data_pct"] = round(data_used / data_limit * 100, 1) if data_limit else 0
        computed["autopay_enabled"] = record.get("autopay_enabled", False)

    if doc_type == "bank_statement":
        transactions = record.get("transactions", [])
        computed["any_transaction_above"] = max(
            (abs(float(t.get("amount", 0))) for t in transactions), default=0
        )

    if doc_type == "payroll_statement":
        computed["lwp_days"]          = float(record.get("lwp_days") or 0)
        computed["total_working_days"] = float(record.get("total_working_days") or 22)
        computed["increment_applied"]  = record.get("increment_applied", False)

    return computed


# ── Format message ────────────────────────────────────────────────────────────
def _format_message(template: str, context: dict) -> str:
    """Replace {field_name} in alert message with formatted values."""
    def _replace(match):
        key = match.group(1)
        val = context.get(key, match.group(0))
        if isinstance(val, float) and val >= 1:
            return f"${val:,.2f}"
        if isinstance(val, float):
            return f"{val:.1%}"
        return str(val)
    return re.sub(r"\{(\w+)\}", _replace, template)


# ── Field style resolver ──────────────────────────────────────────────────────
def _resolve_field_styles(doc_type: str, record: dict, rules: dict) -> dict[str, str]:
    """Return {field_name: css_style} for fields with conditional styling."""
    doc_rules = rules.get("documents", {}).get(doc_type, {})
    field_style_rules = doc_rules.get("field_styles", {})
    styles = {}

    for field_name, conditions in field_style_rules.items():
        value = record.get(field_name, 0)
        context = {**record, "value": value}
        for rule in conditions:
            if _safe_eval(rule["condition"], context):
                styles[field_name] = rule["style"]
                break

    return styles


def _resolve_row_styles(doc_type: str, rules: dict) -> list[dict]:
    """Return per-row style rules for transaction/charge tables."""
    doc_rules = rules.get("documents", {}).get(doc_type, {})
    return doc_rules.get("transaction_styles", [])


# ── Main public function ──────────────────────────────────────────────────────
def apply_rules(doc_type: str, record: dict) -> dict:
    """
    Evaluate all rules against the record.
    Returns enriched record with:
      - __alerts       : list of active alert dicts
      - __field_styles : dict of field_name → CSS
      - __row_styles   : list of row-level style rules (for table rows)
      - __global       : global settings (watermark, footer, etc.)
      + all computed fields merged into record
    """
    rules = load_rules()
    doc_rules = rules.get("documents", {}).get(doc_type, {})

    # Compute derived fields
    computed = _compute_fields(doc_type, record)
    full_ctx  = {**record, **computed}

    # Evaluate conditional blocks → active alerts
    alerts = []
    for block in doc_rules.get("conditional_blocks", []):
        expr = block.get("show_if", "false")
        if _safe_eval(expr, full_ctx):
            alerts.append({
                "id":       block["id"],
                "severity": block.get("severity", "info"),
                "message":  _format_message(block.get("message", ""), full_ctx),
            })

    # Resolve field styles
    field_styles = _resolve_field_styles(doc_type, full_ctx, rules)

    # Row-level styles (passed to template for table colouring)
    row_styles = _resolve_row_styles(doc_type, rules)

    # Global settings
    global_cfg = rules.get("global", {})

    # Merge everything back into record
    enriched = {
        **full_ctx,
        "__alerts":       alerts,
        "__field_styles": field_styles,
        "__row_styles":   row_styles,
        "__global":       global_cfg,
        "__formatting":   rules.get("formatting", {}),
    }
    return enriched

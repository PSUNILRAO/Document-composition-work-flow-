"""
app_web.py
───────────
Flask interactive web UI.
Serves the document generation studio at http://localhost:5000
"""

import io
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from flask import (Flask, render_template_string, request,
                   send_file, jsonify, redirect, url_for, flash,
                   abort)
from werkzeug.utils import secure_filename

from engine import (generate_one, generate_batch, get_preview_rows,
                    default_data_path, DOC_LABELS, BatchResult)
from docx_renderer import (UPLOAD_TEMPLATES_DIR, has_uploaded_template,
                           remove_uploaded_template, uploaded_template_path)

log = logging.getLogger(__name__)

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
_UPLOAD_DIR_RESOLVED = UPLOAD_DIR.resolve()

ALLOWED_UPLOAD_EXTS = frozenset({".xlsx", ".csv"})
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

# Use FLASK_SECRET_KEY from the environment in production. If unset, generate
# a random key at startup so the secret is never committed to source control.
# Sessions signed with a random key don't survive a restart — acceptable for
# this single-user generator app, and safer than a hard-coded secret.
_env_secret = os.environ.get("FLASK_SECRET_KEY")
if _env_secret:
    app.secret_key = _env_secret
else:
    app.secret_key = secrets.token_hex(32)
    log.warning(
        "FLASK_SECRET_KEY is not set; using an ephemeral random key. "
        "Set FLASK_SECRET_KEY in the environment for stable sessions."
    )


def _safe_upload_path(user_supplied_name: str) -> Path | None:
    """Resolve an uploads-relative filename to an absolute path within UPLOAD_DIR.

    Returns None if the name is empty, contains a path separator after
    basename stripping, or resolves outside of UPLOAD_DIR (path traversal).
    """
    if not user_supplied_name:
        return None
    # Only accept a basename — never honour directory components from the client.
    name = os.path.basename(user_supplied_name)
    if not name or name in (".", ".."):
        return None
    candidate = (UPLOAD_DIR / name).resolve()
    try:
        candidate.relative_to(_UPLOAD_DIR_RESOLVED)
    except ValueError:
        return None
    return candidate

# In-memory batch progress tracker
_batch_progress: dict = {}

# ─────────────────────────────────────────────────────────────────────────────
UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Document Generation Studio</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600;700&display=swap');

  :root {
    --ink:       #0D1117;
    --paper:     #F6F8FA;
    --primary:   #0969DA;
    --primary-d: #0550AE;
    --border:    #D0D7DE;
    --surface:   #FFFFFF;
    --muted:     #57606A;
    --success:   #1A7F37;
    --warn:      #9A6700;
    --danger:    #CF222E;
    --warn-bg:   #FFF8C5;
    --danger-bg: #FFEBE9;
    --success-bg:#DAFBE1;
    --info-bg:   #DDF4FF;
    --info:      #0550AE;
    --mono:      'IBM Plex Mono', monospace;
    --sans:      'IBM Plex Sans', system-ui, sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: var(--sans); background: var(--paper); color: var(--ink); font-size: 14px; }

  /* Nav */
  .topbar {
    background: var(--ink); color: #fff;
    padding: 0 28px; height: 52px;
    display: flex; align-items: center; gap: 16px;
    border-bottom: 1px solid #30363D;
  }
  .topbar .logo { font-weight: 700; font-size: 15px; letter-spacing: .3px; }
  .topbar .tag  { font-size: 11px; background: #21262D; color: #8B949E;
                  padding: 2px 8px; border-radius: 10px; font-family: var(--mono); }

  /* Layout */
  .layout { display: grid; grid-template-columns: 240px 1fr; height: calc(100vh - 52px); }
  .sidebar { background: var(--surface); border-right: 1px solid var(--border);
             padding: 20px 0; overflow-y: auto; }
  .main    { overflow-y: auto; padding: 28px 32px; }

  /* Sidebar nav */
  .nav-section { font-size: 11px; font-weight: 600; color: var(--muted);
                 letter-spacing: 1px; padding: 0 16px 8px; margin-top: 12px; }
  .nav-item {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 16px; cursor: pointer; font-size: 13px;
    color: var(--muted); text-decoration: none;
    border-left: 3px solid transparent;
    transition: all .15s;
  }
  .nav-item:hover  { background: var(--paper); color: var(--ink); }
  .nav-item.active { color: var(--primary); border-left-color: var(--primary);
                     background: var(--info-bg); font-weight: 600; }
  .nav-item .icon  { font-size: 16px; width: 20px; text-align: center; }

  /* Page heading */
  .page-title   { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
  .page-sub     { color: var(--muted); font-size: 13px; margin-bottom: 24px; }

  /* Cards */
  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: 8px; margin-bottom: 20px; overflow: hidden; }
  .card-head { padding: 12px 18px; border-bottom: 1px solid var(--border);
               font-weight: 600; font-size: 13px; display: flex; align-items: center;
               justify-content: space-between; background: var(--paper); }
  .card-body { padding: 18px; }

  /* Toolbar */
  .toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; align-items: center; }
  label { font-size: 12px; font-weight: 600; color: var(--muted);
          display: block; margin-bottom: 5px; }
  select, input[type=file], input[type=text] {
    border: 1px solid var(--border); border-radius: 6px;
    padding: 7px 10px; font-size: 13px; font-family: var(--sans);
    background: var(--surface); color: var(--ink);
    outline: none;
  }
  select:focus, input:focus { border-color: var(--primary);
                               box-shadow: 0 0 0 3px rgba(9,105,218,.15); }
  select { min-width: 220px; }

  /* Buttons */
  .btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 7px 14px; border-radius: 6px; font-size: 13px;
    font-weight: 600; font-family: var(--sans);
    cursor: pointer; border: 1px solid transparent;
    text-decoration: none; transition: .15s;
  }
  .btn:disabled { opacity: .5; cursor: not-allowed; }
  .btn-primary { background: var(--primary); color: #fff; border-color: var(--primary-d); }
  .btn-primary:hover { background: var(--primary-d); }
  .btn-outline { background: var(--surface); color: var(--ink); border-color: var(--border); }
  .btn-outline:hover { background: var(--paper); }
  .btn-success { background: var(--success); color: #fff; border-color: #156E30; }
  .btn-danger  { background: var(--danger);  color: #fff; border-color: #A40E26; }
  .btn-sm { padding: 4px 10px; font-size: 12px; }

  /* Table */
  .tbl-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { background: var(--paper); border-bottom: 2px solid var(--border);
       padding: 8px 12px; text-align: left; font-size: 11px;
       font-weight: 600; color: var(--muted); letter-spacing: .5px;
       white-space: nowrap; }
  td { padding: 8px 12px; border-bottom: 1px solid var(--border);
       vertical-align: middle; }
  tr:hover td { background: var(--paper); }
  tr:last-child td { border-bottom: none; }

  /* Badges */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
           font-size: 11px; font-weight: 600; font-family: var(--mono); }
  .badge-blue  { background: var(--info-bg);    color: var(--info); }
  .badge-green { background: var(--success-bg); color: var(--success); }
  .badge-warn  { background: var(--warn-bg);    color: var(--warn); }
  .badge-red   { background: var(--danger-bg);  color: var(--danger); }

  /* Alerts */
  .alert { padding: 10px 14px; border-radius: 6px; font-size: 13px;
           margin-bottom: 16px; display: flex; gap: 8px; align-items: flex-start; }
  .alert-success { background: var(--success-bg); color: var(--success); border: 1px solid #ACEEBB; }
  .alert-error   { background: var(--danger-bg);  color: var(--danger);  border: 1px solid #FFCECB; }
  .alert-info    { background: var(--info-bg);    color: var(--info);    border: 1px solid #B6E3FF; }

  /* Progress bar */
  .prog-wrap { background: var(--paper); border-radius: 6px; height: 8px;
               overflow: hidden; margin: 8px 0; }
  .prog-bar  { height: 100%; background: var(--primary);
               border-radius: 6px; transition: width .3s; }

  /* Stats grid */
  .stats { display: grid; grid-template-columns: repeat(4,1fr); gap: 12px; margin-bottom: 20px; }
  .stat-card { background: var(--surface); border: 1px solid var(--border);
               border-radius: 8px; padding: 14px 16px; }
  .stat-val  { font-size: 22px; font-weight: 700; font-family: var(--mono); }
  .stat-lbl  { font-size: 11px; color: var(--muted); margin-top: 2px; }

  .empty { text-align: center; padding: 48px; color: var(--muted); }
  code { font-family: var(--mono); font-size: 12px; background: var(--paper);
         padding: 1px 6px; border-radius: 4px; border: 1px solid var(--border); }
  .mono { font-family: var(--mono); }

  @media (max-width:768px) {
    .layout { grid-template-columns: 1fr; }
    .sidebar { display: none; }
    .stats  { grid-template-columns: repeat(2,1fr); }
  }
</style>
</head>
<body>

<div class="topbar">
  <div class="logo">📄 Document Generation Studio</div>
  <div class="tag">BRD → Template → Engine → PDF</div>
  <div style="margin-left:auto;font-size:12px;color:#8B949E;">No OpenText · No LLM · Works offline</div>
</div>

<div class="layout">

  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="nav-section">DOCUMENT TYPES</div>
    {% for k, v in doc_labels.items() %}
    <a href="/?type={{ k }}" class="nav-item {% if selected == k %}active{% endif %}">
      <span class="icon">{{ {'bank_statement':'🏦','insurance_policy':'🛡','telecom_bill':'📡','payroll_statement':'💼'}[k] }}</span>
      {{ v }}
    </a>
    {% endfor %}

    <div class="nav-section" style="margin-top:20px;">TOOLS</div>
    <a href="/batch-status" class="nav-item">
      <span class="icon">⚙</span> Batch Status
    </a>
    <a href="/audit-log" class="nav-item">
      <span class="icon">📋</span> Audit Log
    </a>
  </aside>

  <!-- Main content -->
  <main class="main">

    {% with messages = get_flashed_messages(with_categories=true) %}
    {% for cat, msg in messages %}
    <div class="alert alert-{{ cat }}">
      {{ '✅' if cat == 'success' else '⚠' }} {{ msg }}
    </div>
    {% endfor %}
    {% endwith %}

    {% if selected %}

    <div class="page-title">
      {{ {'bank_statement':'🏦','insurance_policy':'🛡','telecom_bill':'📡','payroll_statement':'💼'}[selected] }}
      {{ doc_labels[selected] }}
    </div>
    <div class="page-sub">
      Template: <code>templates/{{ schema.template }}</code> &nbsp;·&nbsp;
      Data: <code>{{ active_file or ('data/' + schema.excel if schema.excel else 'default') }}</code>
    </div>

    <!-- Stats row -->
    <div class="stats">
      <div class="stat-card">
        <div class="stat-val mono">{{ rows|length }}</div>
        <div class="stat-lbl">Records loaded</div>
      </div>
      <div class="stat-card">
        <div class="stat-val mono">{{ cols|length }}</div>
        <div class="stat-lbl">Fields in schema</div>
      </div>
      <div class="stat-card">
        <div class="stat-val mono">PDF</div>
        <div class="stat-lbl">Output format</div>
      </div>
      <div class="stat-card">
        <div class="stat-val mono">Jinja2</div>
        <div class="stat-lbl">Template engine</div>
      </div>
    </div>

    <!-- Upload toolbar -->
    <div class="card">
      <div class="card-head">① Data Source</div>
      <div class="card-body">
        <form method="POST" enctype="multipart/form-data"
              action="/upload?type={{ selected }}" style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;">
          <div>
            <label>Upload Excel / CSV</label>
            <input type="file" name="datafile" accept=".xlsx,.csv">
          </div>
          <button type="submit" class="btn btn-outline">⬆ Upload</button>
          <a href="/?type={{ selected }}" class="btn btn-outline" style="color:var(--muted)">↺ Use default</a>
          {% if rows %}
          <a href="/generate-all?type={{ selected }}{% if active_file %}&file={{ active_file }}{% endif %}"
             class="btn btn-success" style="margin-left:auto"
             onclick="return confirm('Generate {{ rows|length }} PDFs?')">
             ⬇ Generate All ({{ rows|length }})
          </a>
          {% endif %}
        </form>
      </div>
    </div>

    <!-- DOCX template toolbar -->
    <div class="card">
      <div class="card-head">
        ①½ Document Template
        <span style="font-weight:400;color:var(--muted);font-size:12px;">
          {% if docx_template %}
            Using uploaded DOCX: <code>{{ docx_template }}</code>
          {% else %}
            Using built-in HTML template: <code>templates/{{ schema.template }}</code>
          {% endif %}
        </span>
      </div>
      <div class="card-body">
        <form method="POST" enctype="multipart/form-data"
              action="/upload-template?type={{ selected }}" style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;">
          <div>
            <label>Upload DOCX Template</label>
            <input type="file" name="docxfile" accept=".docx">
          </div>
          <button type="submit" class="btn btn-outline">⬆ Upload DOCX</button>
          {% if docx_template %}
          <a href="/reset-template?type={{ selected }}{% if active_file %}&file={{ active_file }}{% endif %}"
             class="btn btn-outline" style="color:var(--muted)"
             onclick="return confirm('Remove the uploaded DOCX template and revert to the built-in HTML template?')">
             🗑 Remove DOCX
          </a>
          {% endif %}
          <span style="margin-left:auto;color:var(--muted);font-size:12px;">
            Placeholders: <code>{{ '{{ field_name }}' }}</code> — e.g. <code>{{ '{{ account_holder }}' }}</code>
          </span>
        </form>
      </div>
    </div>

    <!-- Records table -->
    <div class="card">
      <div class="card-head">
        ② Records
        <span style="font-weight:400;color:var(--muted);font-size:12px;">
          {{ rows|length }} row(s) · click ⬇ to generate individual PDF
        </span>
      </div>
      {% if rows %}
      <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>#</th>
          {% for c in cols[:8] %}<th>{{ c }}</th>{% endfor %}
          {% if cols|length > 8 %}<th>+{{ cols|length - 8 }} cols</th>{% endif %}
          <th>ACTION</th>
        </tr></thead>
        <tbody>
        {% for i, row in rows|enumerate %}
        <tr>
          <td><span class="badge badge-blue mono">{{ i+1 }}</span></td>
          {% for c in cols[:8] %}
          <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
              title="{{ row.get(c,'') }}">{{ row.get(c,'')|string|truncate(28) }}</td>
          {% endfor %}
          {% if cols|length > 8 %}<td style="color:var(--muted)">…</td>{% endif %}
          <td>
            <a href="/generate?type={{ selected }}&row={{ i }}{% if active_file %}&file={{ active_file }}{% endif %}"
               class="btn btn-primary btn-sm">⬇ PDF</a>
          </td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
      </div>
      {% else %}
      <div class="empty">No records found. Upload an Excel / CSV file.</div>
      {% endif %}
    </div>

    {% else %}
    <!-- Landing state -->
    <div class="page-title">Welcome to Document Generation Studio</div>
    <div class="page-sub">Select a document type from the sidebar to get started.</div>
    <div class="card"><div class="card-body">
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:16px;">
        {% for k, v in doc_labels.items() %}
        <a href="/?type={{ k }}" class="btn btn-outline" style="padding:16px;font-size:14px;justify-content:flex-start;gap:12px;">
          <span style="font-size:22px;">{{ {'bank_statement':'🏦','insurance_policy':'🛡','telecom_bill':'📡','payroll_statement':'💼'}[k] }}</span>
          {{ v }}
        </a>
        {% endfor %}
      </div>
    </div></div>
    {% endif %}

  </main>
</div>

<script>
// Auto-dismiss alerts after 4s
setTimeout(() => {
  document.querySelectorAll('.alert').forEach(el => {
    el.style.transition = 'opacity .4s';
    el.style.opacity = 0;
    setTimeout(() => el.remove(), 400);
  });
}, 4000);
</script>
</body></html>"""

# ── Jinja helpers ─────────────────────────────────────────────────────────────
import builtins as _b

@app.template_filter("truncate")
def _truncate(s, n=28):
    s = str(s)
    return s if len(s) <= n else s[:n-1] + "…"

app.jinja_env.globals["enumerate"] = _b.enumerate
# The UI template uses ``rows|enumerate`` as a filter, so expose ``enumerate``
# as a Jinja filter too. Without this the records table renders a
# ``TemplateRuntimeError: No filter named 'enumerate' found``.
app.jinja_env.filters["enumerate"] = _b.enumerate


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    from data_loader import get_doc_schema, load_schema
    selected    = request.args.get("type", "")
    active_file = request.args.get("file", "")
    rows, cols, schema = [], [], {}
    docx_template = ""

    if selected and selected in DOC_LABELS:
        dp = str(UPLOAD_DIR / active_file) if active_file else None
        rows, cols = get_preview_rows(selected, dp)
        schema = get_doc_schema(selected)
        schema["excel"] = schema.get("data_sheet", selected)
        if has_uploaded_template(selected):
            docx_template = uploaded_template_path(selected).name

    return render_template_string(
        UI,
        doc_labels=DOC_LABELS, selected=selected,
        active_file=active_file, rows=rows, cols=cols, schema=schema,
        docx_template=docx_template,
    )


@app.route("/upload", methods=["POST"])
def upload():
    doc_type = request.args.get("type", "")
    if doc_type and doc_type not in DOC_LABELS:
        flash("Unknown document type.", "error")
        return redirect("/")

    f = request.files.get("datafile")
    if not f or not f.filename:
        flash("No file chosen.", "error")
        return redirect(f"/?type={doc_type}")

    # secure_filename strips directory traversal and dangerous characters.
    safe_name = secure_filename(f.filename)
    if not safe_name:
        flash("Invalid filename.", "error")
        return redirect(f"/?type={doc_type}")

    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        flash("Only .xlsx and .csv files accepted.", "error")
        return redirect(f"/?type={doc_type}")

    save_p = _safe_upload_path(safe_name)
    if save_p is None:
        flash("Invalid filename.", "error")
        return redirect(f"/?type={doc_type}")

    f.save(str(save_p))
    flash(f"Uploaded '{safe_name}'.", "success")
    return redirect(f"/?type={doc_type}&file={safe_name}")


def _safe_active_file(active_file: str) -> str:
    """Return ``active_file`` verbatim if it resolves inside ``UPLOAD_DIR``;
    otherwise return ``""``. Used when threading the ``&file=`` query param
    through redirects so an attacker cannot inject arbitrary values into the
    ``Location`` header by crafting the parameter.
    """
    if not active_file:
        return ""
    return active_file if _safe_upload_path(active_file) is not None else ""


@app.route("/upload-template", methods=["POST"])
def upload_template():
    """Accept a .docx template for the selected doc_type and store it under
    uploads/templates/<doc_type>.docx. Subsequent /generate calls will use this
    DOCX template (with Jinja2 placeholder substitution) instead of the
    built-in HTML template."""
    doc_type    = request.args.get("type", "")
    active_file = _safe_active_file(request.args.get("file", ""))
    if doc_type not in DOC_LABELS:
        flash(f"Unknown document type '{doc_type}'.", "error")
        return redirect("/")
    f = request.files.get("docxfile")
    if not f or not f.filename:
        flash("No DOCX file chosen.", "error")
        return redirect(f"/?type={doc_type}&file={active_file}")

    # Use a sanitised display name for flashes/logging so user-controlled
    # characters (path separators, HTML, etc.) can't surface in UI chrome.
    display_name = secure_filename(f.filename) or "uploaded.docx"
    if not f.filename.lower().endswith(".docx"):
        flash("Only .docx files accepted for templates.", "error")
        return redirect(f"/?type={doc_type}&file={active_file}")
    dest = uploaded_template_path(doc_type)
    dest.parent.mkdir(parents=True, exist_ok=True)
    f.save(str(dest))
    flash(
        f"Uploaded DOCX template '{display_name}' for {DOC_LABELS[doc_type]}.",
        "success",
    )
    return redirect(f"/?type={doc_type}&file={active_file}")


@app.route("/reset-template")
def reset_template():
    doc_type    = request.args.get("type", "")
    active_file = _safe_active_file(request.args.get("file", ""))
    if doc_type not in DOC_LABELS:
        flash(f"Unknown document type '{doc_type}'.", "error")
        return redirect("/")
    if remove_uploaded_template(doc_type):
        flash("Reverted to built-in HTML template.", "success")
    else:
        flash("No uploaded DOCX template to remove.", "info")
    return redirect(f"/?type={doc_type}&file={active_file}")


@app.route("/generate")
def generate():
    doc_type    = request.args.get("type", "")
    if doc_type not in DOC_LABELS:
        abort(400, "Unknown document type.")
    try:
        row_index = int(request.args.get("row", 0))
    except (TypeError, ValueError):
        abort(400, "Invalid row index.")
    if row_index < 0:
        abort(400, "Invalid row index.")

    active_file = request.args.get("file", "")
    dp: str | None = None
    if active_file:
        safe_path = _safe_upload_path(active_file)
        if safe_path is None or not safe_path.is_file():
            abort(400, "Invalid or unknown data file.")
        dp = str(safe_path)

    result = generate_one(doc_type, row_index, dp, save=True)
    if result.success:
        return send_file(
            io.BytesIO(result.pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=result.filename,
        )
    flash(f"Error: {result.errors[0] if result.errors else 'Unknown'}", "error")
    return redirect(f"/?type={doc_type}&file={active_file}")


@app.route("/generate-all")
def generate_all():
    doc_type    = request.args.get("type", "")
    if doc_type not in DOC_LABELS:
        abort(400, "Unknown document type.")

    active_file = request.args.get("file", "")
    dp: str | None = None
    if active_file:
        safe_path = _safe_upload_path(active_file)
        if safe_path is None or not safe_path.is_file():
            abort(400, "Invalid or unknown data file.")
        dp = str(safe_path)

    job_id = f"{doc_type}_{int(time.time())}"
    _batch_progress[job_id] = {"done": 0, "total": 0, "status": "running"}

    def _run():
        import time as _t
        def _cb(done, total):
            _batch_progress[job_id].update(done=done, total=total)
        result = generate_batch(doc_type, dp, progress_cb=_cb)
        _batch_progress[job_id]["status"] = "done"
        _batch_progress[job_id]["result"] = {
            "succeeded": result.succeeded,
            "failed": result.failed,
        }

    threading.Thread(target=_run, daemon=True).start()
    flash(f"Batch started (job {job_id}). Check Batch Status for progress.", "info")
    return redirect(f"/?type={doc_type}&file={active_file}")


import time

@app.route("/batch-status")
def batch_status():
    items = json.dumps(_batch_progress, indent=2, default=str)
    return f"<pre style='font-family:monospace;padding:24px'>{items}</pre>"


@app.route("/audit-log")
def audit_log():
    log_dir = Path(__file__).parent / "logs"
    logs = sorted(log_dir.glob("audit_*.json"), reverse=True)[:20]
    rows = []
    for lp in logs:
        try:
            with open(lp) as f:
                rows.append(json.load(f))
        except Exception:
            pass
    return render_template_string(
        """<html><head><title>Audit Log</title>
        <style>body{font-family:monospace;padding:24px;background:#F6F8FA}
        table{border-collapse:collapse;width:100%}
        th,td{padding:8px 12px;border:1px solid #D0D7DE;text-align:left;font-size:13px}
        th{background:#0D1117;color:white}</style></head>
        <body><h2 style="margin-bottom:16px">Audit Log (last 20 runs)</h2>
        <table><thead><tr>
          <th>Run ID</th><th>Doc Type</th><th>Total</th>
          <th>Succeeded</th><th>Failed</th><th>Rate</th><th>Duration</th>
        </tr></thead><tbody>
        {% for r in rows %}
        <tr>
          <td>{{ r.run_id }}</td><td>{{ r.doc_type }}</td>
          <td>{{ r.total }}</td><td>{{ r.succeeded }}</td>
          <td style="color:{{'red' if r.failed else 'green'}}">{{ r.failed }}</td>
          <td>{{ r.success_rate }}</td><td>{{ r.duration_s }}s</td>
        </tr>
        {% endfor %}
        </tbody></table></body></html>""",
        rows=rows
    )


if __name__ == "__main__":
    print("\n" + "="*58)
    print("  📄  Document Generation Studio — Web UI")
    print("="*58)
    print("  Open: http://localhost:5000")
    print("  Ctrl+C to stop")
    print("="*58 + "\n")
    # Debug mode enables the Werkzeug interactive debugger (RCE if reachable).
    # Keep it off by default; opt in explicitly via FLASK_DEBUG=1.
    debug_mode = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(host=host, port=port, debug=debug_mode, threaded=True)

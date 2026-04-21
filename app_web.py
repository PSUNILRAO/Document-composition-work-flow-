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
from template_studio import (bindings_exist, extract_placeholders,
                             load_bindings, normalise_manifest,
                             remove_bindings, save_bindings)
import template_versions as tv

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
    <a href="/studio{% if selected %}?type={{ selected }}{% endif %}" class="nav-item">
      <span class="icon">🎨</span> Template Studio
    </a>
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
          <a href="/studio?type={{ selected }}{% if active_file %}&file={{ active_file }}{% endif %}"
             class="btn btn-primary">
             🎨 Open Template Studio →
          </a>
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
    """Accept a .docx template for the selected doc_type and persist it via
    the template-versioning layer.

    - First-ever upload for a doc_type auto-publishes as v1 (so rendering
      works out of the box).
    - Subsequent uploads land as draft ``v<N+1>`` and do NOT affect the
      currently-published template until explicitly published via the
      Template Studio.
    """
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

    data = f.read()
    try:
        entry = tv.add_version_from_bytes(
            doc_type,
            data,
            template_name=display_name,
            uploaded_by="web",
            notes="",
        )
    except tv.VersionError as exc:
        flash(f"Could not store template: {exc}", "error")
        return redirect(f"/?type={doc_type}&file={active_file}")

    if entry["status"] == tv.STATE_PUBLISHED:
        flash(
            f"Uploaded DOCX '{display_name}' — published as v{entry['version']} "
            f"for {DOC_LABELS[doc_type]}.",
            "success",
        )
    else:
        flash(
            f"Uploaded DOCX '{display_name}' as draft v{entry['version']} for "
            f"{DOC_LABELS[doc_type]}. Open the Template Studio to review and "
            f"publish.",
            "info",
        )
    return redirect(f"/?type={doc_type}&file={active_file}")


@app.route("/reset-template")
def reset_template():
    doc_type    = request.args.get("type", "")
    active_file = _safe_active_file(request.args.get("file", ""))
    if doc_type not in DOC_LABELS:
        flash(f"Unknown document type '{doc_type}'.", "error")
        return redirect("/")

    removed_flat = remove_uploaded_template(doc_type)

    # Wipe the versioning directory too so the next upload bootstraps cleanly
    # as v1/published. This avoids a confusing mid-state where versions.json
    # references DOCX files the flat runtime no longer has.
    removed_versions = False
    try:
        import shutil
        v_dir = tv.versions_dir(doc_type)
        if v_dir.exists():
            shutil.rmtree(v_dir)
            removed_versions = True
    except tv.VersionError:
        pass

    if removed_flat or removed_versions:
        flash("Reverted to built-in HTML template (versions cleared).", "success")
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

    channel = request.args.get("channel", "pdf").lower()
    from engine import CHANNELS
    if channel not in CHANNELS:
        abort(400, f"Unsupported channel: {channel}")

    active_file = request.args.get("file", "")
    dp: str | None = None
    if active_file:
        safe_path = _safe_upload_path(active_file)
        if safe_path is None or not safe_path.is_file():
            abort(400, "Invalid or unknown data file.")
        dp = str(safe_path)

    # "pdf" keeps the DocResult path so it still populates output/ on disk.
    if channel == "pdf":
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

    from engine import generate_channel
    try:
        payload, filename, mimetype, extra = generate_channel(
            doc_type, row_index, channel, dp)
    except Exception as exc:
        flash(f"Error: {exc}", "error")
        return redirect(f"/?type={doc_type}&file={active_file}")

    # For HTML/SMS, default to inline preview in-browser; DOCX is an
    # attachment. A ?download=1 override forces attachment in every case.
    as_attachment = (channel == "docx") or (request.args.get("download") == "1")
    return send_file(
        io.BytesIO(payload),
        mimetype=mimetype,
        as_attachment=as_attachment,
        download_name=filename,
    )


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

STUDIO_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Template Studio · {{ doc_label }}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600;700&display=swap');
  :root {
    --ink:#0D1117; --paper:#F6F8FA; --primary:#0969DA; --primary-d:#0550AE;
    --border:#D0D7DE; --surface:#FFFFFF; --muted:#57606A;
    --success:#1A7F37; --warn:#9A6700; --danger:#CF222E;
    --info-bg:#DDF4FF; --info:#0550AE; --success-bg:#DAFBE1;
    --warn-bg:#FFF8C5; --danger-bg:#FFEBE9;
    --mono:'IBM Plex Mono',monospace; --sans:'IBM Plex Sans',system-ui,sans-serif;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:var(--sans); background:var(--paper); color:var(--ink); font-size:14px; }
  .topbar { background:var(--ink); color:#fff; padding:0 28px; height:52px;
            display:flex; align-items:center; gap:16px; border-bottom:1px solid #30363D; }
  .topbar .logo { font-weight:700; font-size:15px; letter-spacing:.3px; }
  .topbar .tag { font-size:11px; background:#21262D; color:#8B949E;
                 padding:2px 8px; border-radius:10px; font-family:var(--mono); }
  .topbar a { color:#8B949E; font-size:12px; text-decoration:none; }
  .topbar a:hover { color:#fff; }

  .wrap { padding:24px 32px; max-width:1400px; }
  .page-title { font-size:20px; font-weight:700; margin-bottom:4px; }
  .page-sub { color:var(--muted); font-size:13px; margin-bottom:20px; }

  .card { background:var(--surface); border:1px solid var(--border);
          border-radius:8px; margin-bottom:20px; overflow:hidden; }
  .card-head { padding:12px 18px; border-bottom:1px solid var(--border);
               font-weight:600; font-size:13px; background:var(--paper);
               display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .card-body { padding:18px; }

  .btn { display:inline-flex; align-items:center; gap:6px;
         padding:7px 14px; border-radius:6px; font-size:13px;
         font-weight:600; cursor:pointer; border:1px solid transparent;
         text-decoration:none; font-family:var(--sans); }
  .btn:disabled { opacity:.5; cursor:not-allowed; }
  .btn-primary { background:var(--primary); color:#fff; border-color:var(--primary-d); }
  .btn-primary:hover { background:var(--primary-d); }
  .btn-outline { background:var(--surface); color:var(--ink); border-color:var(--border); }
  .btn-outline:hover { background:var(--paper); }
  .btn-danger { background:var(--danger); color:#fff; border-color:#A40E26; }
  .btn-sm { padding:4px 10px; font-size:12px; }

  .alert { padding:10px 14px; border-radius:6px; font-size:13px;
           margin-bottom:16px; }
  .alert-success { background:var(--success-bg); color:var(--success); border:1px solid #ACEEBB; }
  .alert-error   { background:var(--danger-bg);  color:var(--danger);  border:1px solid #FFCECB; }
  .alert-info    { background:var(--info-bg);    color:var(--info);    border:1px solid #B6E3FF; }
  .alert-warn    { background:var(--warn-bg);    color:var(--warn);    border:1px solid #F1E05A; }

  code, .mono { font-family:var(--mono); font-size:12px; }
  .muted { color:var(--muted); }

  /* Mapping grid */
  .map-grid { display:grid; grid-template-columns: 1fr 1fr; gap:20px; align-items:start; }
  .panel { background:var(--surface); border:1px solid var(--border);
           border-radius:8px; padding:0; }
  .panel-head { padding:10px 14px; border-bottom:1px solid var(--border);
                font-weight:600; font-size:12px; letter-spacing:.4px;
                color:var(--muted); text-transform:uppercase;
                background:var(--paper); display:flex; justify-content:space-between; }
  .panel-body { padding:10px; min-height:80px; }

  .field, .ph {
    display:flex; justify-content:space-between; align-items:center;
    padding:8px 10px; margin-bottom:6px; border-radius:6px;
    border:1px solid var(--border); background:var(--surface);
    font-family:var(--mono); font-size:12px;
  }
  .field { cursor:grab; }
  .field:active { cursor:grabbing; }
  .field[draggable="true"]:hover { background:var(--info-bg); border-color:var(--primary); }
  .field .ftype { font-size:10px; color:var(--muted); background:var(--paper);
                  padding:1px 6px; border-radius:10px; }

  .ph { flex-wrap:wrap; }
  .ph .name { flex:0 0 auto; font-weight:600; color:var(--ink); }
  .ph .binding {
    flex:1 1 auto; margin-left:10px; padding:4px 8px;
    border:1px dashed var(--border); border-radius:4px;
    min-height:26px; display:flex; align-items:center; justify-content:space-between;
    background:var(--paper); color:var(--muted);
  }
  .ph .binding.bound   { background:var(--success-bg); color:var(--success);
                         border:1px solid #ACEEBB; border-style:solid; font-weight:600; }
  .ph .binding.drop-ok { background:var(--info-bg); border-color:var(--primary); color:var(--info); }
  .ph .clear-btn { background:none; border:none; color:var(--muted);
                   cursor:pointer; font-size:16px; padding:0 0 0 6px; line-height:1; }
  .ph .clear-btn:hover { color:var(--danger); }

  .repeating { border:1px solid var(--border); border-radius:8px;
               padding:14px; margin-bottom:14px; background:var(--surface); }
  .repeating h4 { font-size:13px; margin-bottom:8px; }
  .repeating .source-row { display:flex; align-items:center; gap:8px;
                            margin-bottom:10px; flex-wrap:wrap; }
  .repeating select { border:1px solid var(--border); border-radius:6px;
                      padding:5px 8px; font-size:13px; font-family:var(--sans);
                      background:var(--surface); min-width:220px; }
  .repeating .inner-grid { display:grid;
                           grid-template-columns:auto 1fr; gap:6px 10px;
                           align-items:center; font-family:var(--mono); font-size:12px; }
  .repeating .inner-grid select { font-family:var(--mono); font-size:12px; min-width:180px; }

  .preview-row { display:flex; align-items:center; gap:10px; }
  .preview-row select, .preview-row input {
    border:1px solid var(--border); border-radius:6px;
    padding:6px 10px; font-size:13px; font-family:var(--sans);
    background:var(--surface);
  }

  .status-dot { display:inline-block; width:8px; height:8px; border-radius:50%;
                background:var(--muted); margin-right:6px; }
  .status-dot.saved { background:var(--success); }
  .status-dot.dirty { background:var(--warn); }

  .empty { padding:24px; text-align:center; color:var(--muted); font-size:13px; }

  /* Version picker + status */
  .ver-bar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .ver-bar select { border:1px solid var(--border); border-radius:6px;
                    padding:5px 10px; font-size:13px; background:var(--surface);
                    font-family:var(--sans); min-width:180px; }
  .badge { display:inline-block; padding:3px 10px; font-size:11px;
           font-weight:700; letter-spacing:.3px; text-transform:uppercase;
           border-radius:12px; border:1px solid transparent; font-family:var(--sans); }
  .badge.draft     { background:#EFF6FF; color:#0550AE; border-color:#B6E3FF; }
  .badge.in_review { background:var(--warn-bg); color:var(--warn); border-color:#F1E05A; }
  .badge.approved  { background:#F0F9EC; color:#2A6F2A; border-color:#ACEEBB; }
  .badge.published { background:var(--success-bg); color:var(--success); border-color:#ACEEBB; }
  .badge.rejected  { background:var(--danger-bg); color:var(--danger); border-color:#FFCECB; }
  .badge.archived  { background:#F4F4F5; color:var(--muted); border-color:var(--border); }

  .diff-table { width:100%; border-collapse:collapse; font-size:12px;
                font-family:var(--mono); margin-top:8px; }
  .diff-table th, .diff-table td { padding:6px 10px; border:1px solid var(--border);
                                   text-align:left; vertical-align:top; }
  .diff-table th { background:var(--paper); font-weight:600; }
  .diff-added   { color:var(--success); }
  .diff-removed { color:var(--danger); }
  .diff-common  { color:var(--muted); }
</style>
</head>
<body>

<div class="topbar">
  <div class="logo">🎨 Template Studio</div>
  <div class="tag">DOCX · PLACEHOLDERS · BINDINGS</div>
  <a href="/{% if selected %}?type={{ selected }}{% if active_file %}&file={{ active_file }}{% endif %}{% endif %}"
     style="margin-left:auto;">← Back to Studio</a>
</div>

<div class="wrap">

  {% with messages = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in messages %}
  <div class="alert alert-{{ cat }}">{{ msg }}</div>
  {% endfor %}
  {% endwith %}

  <div class="page-title">{{ doc_label }}</div>
  <div class="page-sub">
    Template: <code>{% if docx_template %}uploads/templates/{{ docx_template }}{% else %}— no DOCX uploaded —{% endif %}</code>
    &nbsp;·&nbsp; Data: <code>{{ active_file or 'default' }}</code>
  </div>

  {% if not selected or selected not in doc_labels %}
    <div class="alert alert-info">
      Pick a document type to open the Studio.
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;">
        {% for k, v in doc_labels.items() %}
        <a href="/studio?type={{ k }}" class="btn btn-outline btn-sm">{{ v }}</a>
        {% endfor %}
      </div>
    </div>
  {% elif not docx_template %}
    <div class="card">
      <div class="card-head">Upload a DOCX mock-up to begin</div>
      <div class="card-body">
        <form method="POST" enctype="multipart/form-data"
              action="/upload-template?type={{ selected }}{% if active_file %}&file={{ active_file }}{% endif %}"
              style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
          <input type="file" name="docxfile" accept=".docx" required>
          <button type="submit" class="btn btn-primary">⬆ Upload DOCX</button>
          <span class="muted" style="font-size:12px;">
            Word placeholders use <code>{{ '{{ field_name }}' }}</code> and
            <code>{{ '{% for row in transactions %}...{% endfor %}' }}</code>.
          </span>
        </form>
      </div>
    </div>
  {% else %}

    <!-- Version management -->
    <div class="card">
      <div class="card-head">
        <span>TEMPLATE VERSIONS</span>
        <span class="muted" style="font-size:12px;">
          Draft → In Review → Approved → Published · one published version per doc type
        </span>
      </div>
      <div class="card-body">
        <div class="ver-bar" id="ver-bar">
          <label class="muted" for="ver-select" style="font-size:12px;">Viewing:</label>
          <select id="ver-select"></select>
          <span id="ver-badge" class="badge draft">—</span>
          <span class="muted" id="ver-meta" style="font-size:12px;"></span>
          <span style="flex:1 1 auto;"></span>
          <form method="POST" enctype="multipart/form-data" id="upload-new-form"
                action="/upload-template?type={{ selected }}{% if active_file %}&file={{ active_file }}{% endif %}"
                style="display:flex;gap:6px;align-items:center;">
            <label class="btn btn-outline btn-sm" for="docxfile-new" style="cursor:pointer;">
              ⬆ Upload new version
              <input type="file" name="docxfile" id="docxfile-new"
                     accept=".docx" style="display:none;" required>
            </label>
          </form>
        </div>
        <div class="ver-bar" id="ver-actions" style="margin-top:12px;">
          <button id="act-submit"   class="btn btn-outline btn-sm" disabled>Submit for review</button>
          <button id="act-approve"  class="btn btn-outline btn-sm" disabled>Approve</button>
          <button id="act-reject"   class="btn btn-outline btn-sm" disabled>Reject</button>
          <button id="act-publish"  class="btn btn-primary btn-sm" disabled>Publish</button>
          <button id="act-draft"    class="btn btn-outline btn-sm" disabled title="Move back to draft">↶ Back to draft</button>
          <button id="act-rollback" class="btn btn-outline btn-sm" disabled title="Re-publish previous version">↺ Rollback</button>
          <span style="flex:1 1 auto;"></span>
          <label class="muted" for="diff-against" style="font-size:12px;">Compare with:</label>
          <select id="diff-against"></select>
          <button id="diff-btn" class="btn btn-outline btn-sm">Compare</button>
        </div>
        <div id="diff-panel" style="display:none; margin-top:14px;"></div>
      </div>
    </div>

    <!-- Preview + actions bar -->
    <div class="card">
      <div class="card-head">
        <span><span id="status-dot" class="status-dot"></span>
              <span id="status-label">Loading…</span></span>
        <span style="display:flex;gap:8px;">
          <button id="save-btn" class="btn btn-primary" disabled>💾 Save bindings</button>
          <button id="reset-btn" class="btn btn-outline btn-sm" title="Discard unsaved changes">↺ Reset</button>
          <button id="clear-all-btn" class="btn btn-outline btn-sm" title="Remove the saved manifest">🗑 Clear saved</button>
        </span>
      </div>
      <div class="card-body preview-row">
        <label class="muted" for="preview-row">Preview row:</label>
        <select id="preview-row">
          {% for i in range(rows_count) %}
          <option value="{{ i }}">Row {{ i + 1 }}</option>
          {% endfor %}
          {% if rows_count == 0 %}<option value="0">No data loaded</option>{% endif %}
        </select>
        <label class="muted" for="preview-channel" style="margin-left:12px;">Channel:</label>
        <select id="preview-channel">
          <option value="pdf">PDF (Archive / Print / Fax)</option>
          <option value="email">HTML Email (Secure Inbox)</option>
          <option value="sms">SMS (plain text)</option>
          <option value="docx">DOCX (File Exchange)</option>
        </select>
        <a id="preview-link" class="btn btn-primary btn-sm" target="_blank" rel="noopener"
           href="/generate?type={{ selected }}&row=0&channel=pdf{% if active_file %}&file={{ active_file }}{% endif %}">
           ⬇ Preview
        </a>
        <span class="muted" style="font-size:12px;">
          Tip: Save bindings first, then preview. Each channel renders from the
          same record + bindings but uses a channel-specific template.
        </span>
      </div>
    </div>

    <!-- Mapping grid -->
    <div class="map-grid">

      <!-- Left: data fields palette -->
      <div class="panel">
        <div class="panel-head">
          <span>DATA FIELDS</span>
          <span class="muted" id="fields-count">—</span>
        </div>
        <div class="panel-body" id="fields-panel">
          <div class="empty">Loading fields…</div>
        </div>
      </div>

      <!-- Right: placeholders -->
      <div class="panel">
        <div class="panel-head">
          <span>TEMPLATE PLACEHOLDERS</span>
          <span class="muted" id="ph-count">—</span>
        </div>
        <div class="panel-body" id="ph-panel">
          <div class="empty">Loading placeholders…</div>
        </div>
      </div>

    </div>

    <!-- Repeating sections -->
    <div class="card" style="margin-top:20px;">
      <div class="card-head">
        <span>REPEATING SECTIONS</span>
        <span class="muted" style="font-size:12px;">
          One entry per <code>{{ '{% for x in … %}' }}</code> block in the DOCX.
        </span>
      </div>
      <div class="card-body" id="repeating-panel">
        <div class="empty">Loading…</div>
      </div>
    </div>

  {% endif %}
</div>

<script>
(function() {
  const docType = {{ selected|tojson }};
  const hasTemplate = {{ 'true' if docx_template else 'false' }};
  if (!docType || !hasTemplate) return;

  const state = {
    placeholders: { scalar_placeholders: [], repeating_sections: [], parse_error: null },
    fields: { scalar_fields: [], list_fields: [] },
    bindings: { scalars: {}, repeating: {} },
    saved: JSON.stringify({ scalars: {}, repeating: {} }),
    versions: { versions: [], published: null, latest: 0 },
    currentVersion: null,  // null until /api/studio/versions resolves
  };

  const $ = (sel) => document.querySelector(sel);
  const fieldsPanel = $("#fields-panel");
  const phPanel = $("#ph-panel");
  const repPanel = $("#repeating-panel");
  const statusLabel = $("#status-label");
  const statusDot = $("#status-dot");
  const saveBtn = $("#save-btn");
  const resetBtn = $("#reset-btn");
  const clearAllBtn = $("#clear-all-btn");
  const verSelect = $("#ver-select");
  const verBadge  = $("#ver-badge");
  const verMeta   = $("#ver-meta");
  const diffAgainst = $("#diff-against");
  const diffPanel = $("#diff-panel");
  const uploadForm = $("#upload-new-form");
  const uploadInput = $("#docxfile-new");

  function versionQS() {
    let qs = "?type=" + encodeURIComponent(docType);
    if (state.currentVersion) qs += "&version=" + encodeURIComponent(state.currentVersion);
    return qs;
  }
  function currentVersionEntry() {
    return (state.versions.versions || []).find(v => v.version === state.currentVersion) || null;
  }

  function setStatus(kind, label) {
    statusDot.className = "status-dot " + (kind || "");
    statusLabel.textContent = label;
  }
  function isDirty() {
    return JSON.stringify(sanitise(state.bindings)) !== state.saved;
  }
  function updateDirty() {
    if (isDirty()) { setStatus("dirty", "Unsaved changes"); saveBtn.disabled = false; }
    else           { setStatus("saved", "All changes saved"); saveBtn.disabled = true; }
  }

  function sanitise(b) {
    // Strip empty entries so the saved snapshot is stable.
    const scalars = {};
    Object.keys(b.scalars || {}).forEach(k => {
      if (b.scalars[k]) scalars[k] = b.scalars[k];
    });
    const repeating = {};
    Object.keys(b.repeating || {}).forEach(k => {
      const v = b.repeating[k] || {};
      if (!v.source) return;
      const fm = {};
      Object.keys(v.field_map || {}).forEach(ik => {
        if (v.field_map[ik]) fm[ik] = v.field_map[ik];
      });
      repeating[k] = { source: v.source, field_map: fm };
    });
    return { scalars, repeating };
  }

  async function load() {
    const baseQS = "?type=" + encodeURIComponent(docType);
    const verQS  = versionQS();
    const fileParam = {{ active_file|tojson }};
    const fqs = fileParam ? ("&file=" + encodeURIComponent(fileParam)) : "";
    try {
      const [ph, fi, bi] = await Promise.all([
        fetch("/api/studio/placeholders" + verQS).then(r => r.json()),
        fetch("/api/studio/fields"       + baseQS + fqs).then(r => r.json()),
        fetch("/api/studio/bindings"     + verQS).then(r => r.json()),
      ]);
      state.placeholders = ph;
      state.fields = fi;
      state.bindings = { scalars: bi.scalars || {}, repeating: bi.repeating || {} };
      state.saved = JSON.stringify(sanitise(state.bindings));
      renderAll();
      updateDirty();
    } catch (e) {
      setStatus("dirty", "Error: " + e.message);
    }
  }

  async function loadVersions(preferVersion) {
    const qs = "?type=" + encodeURIComponent(docType);
    try {
      const idx = await fetch("/api/studio/versions" + qs).then(r => r.json());
      state.versions = idx;
      // Pick the version to show: caller override → published → latest.
      const list = idx.versions || [];
      let pick = null;
      if (preferVersion && list.some(v => v.version === preferVersion)) pick = preferVersion;
      else if (idx.published) pick = idx.published;
      else if (list.length) pick = list[list.length - 1].version;
      state.currentVersion = pick;
      renderVersions();
      return idx;
    } catch (e) {
      setStatus("dirty", "Could not load versions: " + e.message);
      return null;
    }
  }

  function renderVersions() {
    const list = state.versions.versions || [];
    // Build the main select.
    verSelect.innerHTML = list.slice().reverse().map(v => {
      const label = "v" + v.version + " · " + v.status
                  + (v.version === state.versions.published ? " ★" : "");
      const sel = (v.version === state.currentVersion) ? "selected" : "";
      return '<option value="' + v.version + '" ' + sel + '>'
           + escapeHtml(label) + '</option>';
    }).join("");
    diffAgainst.innerHTML = list.slice().reverse().map(v => {
      const sel = (v.version !== state.currentVersion && v.version === state.versions.published) ? "selected" : "";
      return '<option value="' + v.version + '" ' + sel + '>v' + v.version + ' · ' + escapeHtml(v.status) + '</option>';
    }).join("");

    const cur = currentVersionEntry();
    if (cur) {
      verBadge.className = "badge " + cur.status;
      verBadge.textContent = cur.status.replace("_", " ");
      const who = cur.uploaded_by ? (" · by " + cur.uploaded_by) : "";
      verMeta.textContent = "created " + cur.created_at + who;
    } else {
      verBadge.className = "badge draft";
      verBadge.textContent = "—";
      verMeta.textContent = "";
    }
    updateActionButtons();
  }

  function updateActionButtons() {
    const cur = currentVersionEntry();
    const st  = cur ? cur.status : null;
    const isPub = !!state.versions.published;
    // Transitions
    $("#act-submit").disabled    = (st !== "draft");
    $("#act-approve").disabled   = (st !== "in_review");
    $("#act-reject").disabled    = (st !== "draft" && st !== "in_review");
    $("#act-publish").disabled   = (st !== "approved");
    $("#act-draft").disabled     = !(st === "in_review" || st === "approved" || st === "rejected");
    // Rollback — always based on overall index state, not the viewed version.
    const archivedPrev = (state.versions.versions || []).some(v =>
      v.status === "archived" &&
      (v.history || []).some(h => h.to === "published"));
    $("#act-rollback").disabled = !(isPub && archivedPrev);
  }

  async function doTransition(to) {
    if (!state.currentVersion) return;
    const res = await fetch("/api/studio/versions/" + state.currentVersion
                 + "/transition?type=" + encodeURIComponent(docType), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to })
    });
    if (!res.ok) { alert(await res.text()); return; }
    await loadVersions(state.currentVersion);
    await load();
  }

  async function doPublish() {
    if (!state.currentVersion) return;
    const res = await fetch("/api/studio/versions/" + state.currentVersion
                 + "/publish?type=" + encodeURIComponent(docType), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}"
    });
    if (!res.ok) { alert(await res.text()); return; }
    await loadVersions(state.currentVersion);
    await load();
  }

  async function doRollback() {
    const res = await fetch("/api/studio/versions/rollback?type="
                 + encodeURIComponent(docType), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}"
    });
    if (!res.ok) { alert(await res.text()); return; }
    // Switch view to the now-published version so the user sees the change.
    const idx = await loadVersions();
    if (idx && idx.published) {
      state.currentVersion = idx.published;
      renderVersions();
    }
    await load();
  }

  async function doDiff() {
    const against = parseInt(diffAgainst.value, 10);
    if (!state.currentVersion || !against) return;
    const res = await fetch("/api/studio/versions/" + state.currentVersion
                 + "/diff?type=" + encodeURIComponent(docType)
                 + "&against=" + against);
    if (!res.ok) { alert(await res.text()); return; }
    const d = await res.json();
    renderDiff(d);
  }

  function renderDiff(d) {
    const section = (title, payload) => {
      const parts = [];
      if (payload.added && payload.added.length)
        parts.push('<span class="diff-added">+ ' + payload.added.map(escapeHtml).join(", ") + '</span>');
      if (payload.removed && payload.removed.length)
        parts.push('<span class="diff-removed">− ' + payload.removed.map(escapeHtml).join(", ") + '</span>');
      if (payload.changed && payload.changed.length)
        parts.push('<span class="diff-added">~ ' + payload.changed.map(escapeHtml).join(", ") + '</span>');
      if (payload.common && payload.common.length)
        parts.push('<span class="diff-common">= ' + payload.common.map(escapeHtml).join(", ") + '</span>');
      return '<tr><th>' + escapeHtml(title) + '</th><td>'
           + (parts.join("<br>") || '<span class="diff-common">(no change)</span>')
           + '</td></tr>';
    };
    diffPanel.innerHTML =
      '<table class="diff-table"><thead><tr>'
      + '<th style="width:220px;">Comparing v' + d.a + ' → v' + d.b + '</th>'
      + '<th>Differences (+ added, − removed, ~ changed, = common)</th></tr></thead><tbody>'
      + section("Scalar placeholders",    d.scalar_placeholders)
      + section("Repeating sections",     d.repeating_sections)
      + section("Bindings (scalars)",     d.bindings_scalars)
      + section("Bindings (repeating)",   d.bindings_repeating)
      + '</tbody></table>';
    diffPanel.style.display = "block";
  }

  function renderAll() {
    renderFields();
    renderPlaceholders();
    renderRepeating();
    $("#fields-count").textContent = (state.fields.scalar_fields || []).length + " scalar, "
                                   + (state.fields.list_fields || []).length + " list";
    $("#ph-count").textContent = (state.placeholders.scalar_placeholders || []).length + " scalar, "
                               + (state.placeholders.repeating_sections || []).length + " repeating";
  }

  function renderFields() {
    const scalars = state.fields.scalar_fields || [];
    if (!scalars.length) {
      fieldsPanel.innerHTML = '<div class="empty">No data fields — upload an Excel / CSV on the main page first.</div>';
      return;
    }
    fieldsPanel.innerHTML = scalars.map(f =>
      `<div class="field" draggable="true" data-field="${escapeAttr(f.name)}">
         <span>${escapeHtml(f.name)}</span>
         <span class="ftype">${escapeHtml(f.type || '')}</span>
       </div>`
    ).join("");
    fieldsPanel.querySelectorAll('.field').forEach(el => {
      el.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/x-field', el.dataset.field);
        e.dataTransfer.effectAllowed = 'copy';
      });
    });
  }

  function renderPlaceholders() {
    const pls = state.placeholders.scalar_placeholders || [];
    if (state.placeholders.parse_error) {
      phPanel.innerHTML = '<div class="alert alert-warn">'
        + escapeHtml(state.placeholders.parse_error) + '</div>';
    } else if (!pls.length) {
      phPanel.innerHTML = '<div class="empty">No scalar placeholders found in the DOCX.</div>';
      return;
    } else {
      phPanel.innerHTML = "";
    }
    pls.forEach(name => {
      const bound = state.bindings.scalars[name] || "";
      const row = document.createElement('div');
      row.className = 'ph';
      row.innerHTML =
        `<span class="name">{{ '{{' }} ${escapeHtml(name)} {{ '}}' }}</span>
         <span class="binding ${bound ? 'bound' : ''}" data-placeholder="${escapeAttr(name)}">
           <span class="label">${bound ? escapeHtml(bound) : 'drop a field here…'}</span>
           ${bound ? `<button class="clear-btn" title="Remove binding">×</button>` : ''}
         </span>`;
      phPanel.appendChild(row);

      const bindingEl = row.querySelector('.binding');
      bindingEl.addEventListener('dragover', (e) => { e.preventDefault(); bindingEl.classList.add('drop-ok'); });
      bindingEl.addEventListener('dragleave', () => bindingEl.classList.remove('drop-ok'));
      bindingEl.addEventListener('drop', (e) => {
        e.preventDefault();
        bindingEl.classList.remove('drop-ok');
        const field = e.dataTransfer.getData('text/x-field');
        if (!field) return;
        state.bindings.scalars[name] = field;
        renderPlaceholders();
        updateDirty();
      });
      const clearBtn = row.querySelector('.clear-btn');
      if (clearBtn) clearBtn.addEventListener('click', () => {
        delete state.bindings.scalars[name];
        renderPlaceholders(); updateDirty();
      });
    });
  }

  function renderRepeating() {
    const reps = state.placeholders.repeating_sections || [];
    const listFields = state.fields.list_fields || [];
    if (!reps.length) {
      repPanel.innerHTML = '<div class="empty">No <code>{{ "{% for %}" }}</code> blocks in the DOCX.</div>';
      return;
    }
    repPanel.innerHTML = "";
    reps.forEach(rs => {
      const current = state.bindings.repeating[rs.iter_source] || { source: "", field_map: {} };
      const block = document.createElement('div');
      block.className = 'repeating';
      const innerFieldsOptions = (src) => {
        const match = listFields.find(lf => lf.name === src);
        const opts = (match && match.item_keys) ? match.item_keys : [];
        return opts.map(k => `<option value="${escapeAttr(k)}">${escapeHtml(k)}</option>`).join("");
      };
      block.innerHTML = `
        <h4><code>{{ '{% for' }} ${escapeHtml(rs.loop_var || 'row')} in ${escapeHtml(rs.iter_source)} {{ '%}' }}</code></h4>
        <div class="source-row">
          <span class="muted">Source list:</span>
          <select class="src-sel">
            <option value="">— pick a list —</option>
            ${listFields.map(lf => {
              const sel = (lf.name === current.source) ? 'selected' : '';
              return `<option value="${escapeAttr(lf.name)}" ${sel}>${escapeHtml(lf.name)} (${lf.sample_count} rows)</option>`;
            }).join("")}
          </select>
        </div>
        <div class="inner-grid">
          ${(rs.inner_fields || []).length === 0 ? '<div class="muted" style="grid-column:1/-1;">No inner fields referenced.</div>' : ''}
          ${(rs.inner_fields || []).map(inner => {
            const bound = (current.field_map || {})[inner] || "";
            return `
              <div>${escapeHtml(rs.loop_var || 'row')}.${escapeHtml(inner)}</div>
              <select data-inner="${escapeAttr(inner)}" class="inner-sel">
                <option value="">— unbound —</option>
                ${innerFieldsOptions(current.source).replace(
                   new RegExp('value="' + escapeAttr(bound) + '"'),
                   'value="' + escapeAttr(bound) + '" selected')}
              </select>
            `;
          }).join("")}
        </div>
      `;
      repPanel.appendChild(block);

      const srcSel = block.querySelector('.src-sel');
      srcSel.addEventListener('change', () => {
        const entry = state.bindings.repeating[rs.iter_source] || { source: "", field_map: {} };
        entry.source = srcSel.value;
        state.bindings.repeating[rs.iter_source] = entry;
        updateDirty();
        renderRepeating();  // refresh inner dropdown options
      });
      block.querySelectorAll('.inner-sel').forEach(sel => {
        sel.addEventListener('change', () => {
          const entry = state.bindings.repeating[rs.iter_source] || { source: "", field_map: {} };
          const inner = sel.dataset.inner;
          if (sel.value) entry.field_map[inner] = sel.value;
          else delete entry.field_map[inner];
          state.bindings.repeating[rs.iter_source] = entry;
          updateDirty();
        });
      });
    });
  }

  async function save() {
    const cur = currentVersionEntry();
    const latest = state.versions.latest || 0;
    const editable = cur && (cur.status === "draft"
                      || (cur.status === "published" && cur.version === latest));
    if (cur && !editable) {
      alert("Bindings are read-only on v" + cur.version + " (status '"
            + cur.status + "'). Move it back to draft or upload a new version.");
      return;
    }
    setStatus("", "Saving…"); saveBtn.disabled = true;
    try {
      const res = await fetch("/api/studio/bindings" + versionQS(), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sanitise(state.bindings)),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const saved = await res.json();
      state.bindings = { scalars: saved.scalars || {}, repeating: saved.repeating || {} };
      state.saved = JSON.stringify(sanitise(state.bindings));
      renderAll(); updateDirty();
    } catch (e) {
      setStatus("dirty", "Save failed: " + e.message);
    }
  }

  async function clearSaved() {
    if (!confirm("Delete the saved binding manifest?")) return;
    await fetch("/api/studio/bindings?type=" + encodeURIComponent(docType), { method: "DELETE" });
    state.bindings = { scalars: {}, repeating: {} };
    state.saved = JSON.stringify(sanitise(state.bindings));
    renderAll(); updateDirty();
  }

  saveBtn.addEventListener('click', save);
  resetBtn.addEventListener('click', load);
  clearAllBtn.addEventListener('click', clearSaved);

  // ── Version UI event wiring ───────────────────────────────────────────────
  verSelect.addEventListener('change', async () => {
    state.currentVersion = parseInt(verSelect.value, 10);
    renderVersions();
    await load();
  });
  uploadInput.addEventListener('change', () => {
    if (uploadInput.files && uploadInput.files.length) uploadForm.submit();
  });
  $("#act-submit")  .addEventListener('click', () => doTransition("in_review"));
  $("#act-approve") .addEventListener('click', () => doTransition("approved"));
  $("#act-reject")  .addEventListener('click', () => doTransition("rejected"));
  $("#act-draft")   .addEventListener('click', () => doTransition("draft"));
  $("#act-publish") .addEventListener('click', doPublish);
  $("#act-rollback").addEventListener('click', async () => {
    if (!confirm("Rollback: archive the currently-published version and "
               + "re-publish the previous one?")) return;
    await doRollback();
  });
  $("#diff-btn")    .addEventListener('click', doDiff);

  // Preview link keeps the selected row + channel.
  const rowSel = $("#preview-row"), link = $("#preview-link");
  const chanSel = $("#preview-channel");
  const fileParam = {{ active_file|tojson }};
  const CHANNEL_LABELS = {
    pdf: '⬇ Preview PDF',
    email: '✉ Preview Email',
    sms: '💬 Preview SMS',
    docx: '⬇ Download DOCX',
  };
  function updatePreviewLink() {
    const r = rowSel.value || "0";
    const c = chanSel.value || "pdf";
    let href = "/generate?type=" + encodeURIComponent(docType)
             + "&row=" + encodeURIComponent(r)
             + "&channel=" + encodeURIComponent(c);
    if (fileParam) href += "&file=" + encodeURIComponent(fileParam);
    link.href = href;
    link.textContent = CHANNEL_LABELS[c] || '⬇ Preview';
  }
  rowSel.addEventListener('change', updatePreviewLink);
  chanSel.addEventListener('change', updatePreviewLink);
  updatePreviewLink();

  function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function escapeAttr(s) { return escapeHtml(s); }

  (async () => {
    await loadVersions();
    await load();
  })();
})();
</script>
</body></html>"""


# ── Template Studio routes ────────────────────────────────────────────────────
@app.route("/studio")
def studio():
    from data_loader import get_doc_schema
    selected = request.args.get("type", "")
    active_file = _safe_active_file(request.args.get("file", ""))
    rows_count = 0
    docx_template = ""
    doc_label = "Template Studio"

    if selected and selected in DOC_LABELS:
        doc_label = DOC_LABELS[selected]
        if has_uploaded_template(selected):
            docx_template = uploaded_template_path(selected).name
        dp = str(UPLOAD_DIR / active_file) if active_file else None
        try:
            rows, _ = get_preview_rows(selected, dp)
            rows_count = len(rows)
        except Exception:
            rows_count = 0

    return render_template_string(
        STUDIO_UI,
        doc_labels=DOC_LABELS, selected=selected,
        active_file=active_file, docx_template=docx_template,
        doc_label=doc_label, rows_count=rows_count,
    )


def _require_doc_type() -> str:
    doc_type = request.args.get("type", "")
    if doc_type not in DOC_LABELS:
        abort(400, description="Unknown document type.")
    return doc_type


def _optional_version_arg() -> int | None:
    """Parse an optional ``?version=<int>`` param. Returns None if absent."""
    raw = request.args.get("version")
    if raw is None or raw == "":
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        abort(400, description="Invalid version number.")
    if val < 1:
        abort(400, description="Version must be >= 1.")
    return val


@app.route("/api/studio/placeholders")
def api_studio_placeholders():
    doc_type = _require_doc_type()
    version  = _optional_version_arg()

    if version is not None:
        docx_path = tv.version_docx_path(doc_type, version)
        if not docx_path.is_file():
            return jsonify({
                "template_name": "",
                "scalar_placeholders": [],
                "repeating_sections": [],
                "parse_error": f"No DOCX stored for v{version}.",
            })
        try:
            return jsonify(extract_placeholders(doc_type, docx_path=docx_path))
        except FileNotFoundError as exc:
            return jsonify({
                "template_name": "", "scalar_placeholders": [],
                "repeating_sections": [], "parse_error": str(exc),
            })

    if not has_uploaded_template(doc_type):
        return jsonify({
            "template_name": "",
            "scalar_placeholders": [],
            "repeating_sections": [],
            "parse_error": "No DOCX uploaded for this document type.",
        })
    try:
        return jsonify(extract_placeholders(doc_type))
    except FileNotFoundError as exc:
        return jsonify({
            "template_name": "", "scalar_placeholders": [],
            "repeating_sections": [], "parse_error": str(exc),
        })


@app.route("/api/studio/fields")
def api_studio_fields():
    from data_loader import get_doc_schema
    doc_type = _require_doc_type()
    active_file = _safe_active_file(request.args.get("file", ""))

    schema = get_doc_schema(doc_type)
    schema_fields: dict = schema.get("fields", {})

    scalar_fields: list[dict] = []
    list_fields_map: dict[str, dict] = {}

    for name, fdef in schema_fields.items():
        ftype = fdef.get("type", "string")
        if ftype == "list":
            list_fields_map[name] = {
                "name": name, "type": "list",
                "item_keys": [], "sample_count": 0,
            }
        else:
            scalar_fields.append({"name": name, "type": ftype})

    # Extra data-specific fields: look at the first loaded record for any keys
    # not declared in the schema, and for any list-valued fields introspect the
    # item keys so the Studio can offer them in inner-field dropdowns.
    dp = str(UPLOAD_DIR / active_file) if active_file else None
    try:
        rows, _cols = get_preview_rows(doc_type, dp)
    except Exception:
        rows = []

    if rows:
        first = rows[0]
        known = {f["name"] for f in scalar_fields} | set(list_fields_map.keys())
        for k, v in first.items():
            if k in known:
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    # Surface the actual item keys — may be a superset of the schema.
                    lf = list_fields_map.setdefault(
                        k, {"name": k, "type": "list",
                            "item_keys": [], "sample_count": 0})
                    lf["item_keys"] = list(v[0].keys())
                    lf["sample_count"] = len(v)
                continue
            if isinstance(v, list):
                if v and isinstance(v[0], dict):
                    list_fields_map[k] = {
                        "name": k, "type": "list",
                        "item_keys": list(v[0].keys()),
                        "sample_count": len(v),
                    }
                else:
                    list_fields_map[k] = {
                        "name": k, "type": "list",
                        "item_keys": ["value"], "sample_count": len(v),
                    }
            else:
                scalar_fields.append({"name": k, "type": "extra"})

    # Stable ordering
    scalar_fields.sort(key=lambda f: f["name"].lower())
    list_fields = sorted(list_fields_map.values(), key=lambda f: f["name"].lower())
    return jsonify({
        "scalar_fields": scalar_fields,
        "list_fields":   list_fields,
    })


@app.route("/api/studio/bindings", methods=["GET"])
def api_studio_bindings_get():
    doc_type = _require_doc_type()
    version  = _optional_version_arg()
    if version is not None:
        return jsonify(tv.read_version_bindings(doc_type, version))
    return jsonify(load_bindings(doc_type))


@app.route("/api/studio/bindings", methods=["POST"])
def api_studio_bindings_post():
    doc_type = _require_doc_type()
    version  = _optional_version_arg()
    payload  = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Body must be a JSON object.")

    if version is not None:
        # Validate + normalise first using the same machinery as the flat
        # path (save_bindings does validation + normalisation), but without
        # touching the flat file — we only want to mirror when this version
        # is the currently-published one.
        try:
            normalised = normalise_manifest(payload)
            tv.set_version_bindings(doc_type, version, normalised)
        except tv.VersionError as exc:
            abort(400, description=str(exc))
        except ValueError as exc:
            abort(400, description=str(exc))
        idx = tv.list_versions(doc_type)
        if idx["published"] == version:
            # Keep the flat mirror in sync so /generate picks up the changes.
            save_bindings(doc_type, normalised)
        return jsonify(normalised)

    try:
        saved = save_bindings(doc_type, payload)
    except ValueError as exc:
        abort(400, description=str(exc))
    return jsonify(saved)


@app.route("/api/studio/bindings", methods=["DELETE"])
def api_studio_bindings_delete():
    doc_type = _require_doc_type()
    removed = remove_bindings(doc_type)
    return jsonify({"removed": removed})


# ── Version-management APIs ───────────────────────────────────────────────────
@app.route("/api/studio/versions", methods=["GET"])
def api_studio_versions_list():
    doc_type = _require_doc_type()
    return jsonify(tv.list_versions(doc_type))


@app.route("/api/studio/versions/<int:version>/transition", methods=["POST"])
def api_studio_version_transition(version: int):
    doc_type = _require_doc_type()
    payload  = request.get_json(silent=True) or {}
    target   = str(payload.get("to") or "").strip()
    by       = str(payload.get("by") or "web")
    if not target:
        abort(400, description="Missing 'to' in body.")
    try:
        entry = tv.transition_status(doc_type, version, target, by=by)
    except tv.VersionError as exc:
        abort(400, description=str(exc))
    return jsonify(entry)


@app.route("/api/studio/versions/<int:version>/publish", methods=["POST"])
def api_studio_version_publish(version: int):
    doc_type = _require_doc_type()
    payload  = request.get_json(silent=True) or {}
    by       = str(payload.get("by") or "web")
    try:
        entry = tv.publish_version(doc_type, version, by=by)
    except tv.VersionError as exc:
        abort(400, description=str(exc))
    return jsonify(entry)


@app.route("/api/studio/versions/rollback", methods=["POST"])
def api_studio_version_rollback():
    doc_type = _require_doc_type()
    payload  = request.get_json(silent=True) or {}
    by       = str(payload.get("by") or "web")
    try:
        entry = tv.rollback_published(doc_type, by=by)
    except tv.VersionError as exc:
        abort(400, description=str(exc))
    return jsonify(entry)


@app.route("/api/studio/versions/<int:version>/diff", methods=["GET"])
def api_studio_version_diff(version: int):
    doc_type = _require_doc_type()
    raw_against = request.args.get("against", "")
    try:
        against = int(raw_against)
    except (TypeError, ValueError):
        abort(400, description="Missing or invalid 'against' query parameter.")
    if against < 1 or version < 1:
        abort(400, description="Version numbers must be >= 1.")
    return jsonify(tv.diff_versions(doc_type, version, against))


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

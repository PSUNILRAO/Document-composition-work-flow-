# Security Audit — Findings & Remediations

Scope: the Python sources in this repository (`app_web.py`, `engine.py`,
`data_loader.py`, `renderer.py`, `rules_engine.py`, `batch_runner.py`,
`validator.py`, `setup_and_run.py`, `create_sample_data.py`, `create_templates.py`)
and `requirements.txt`.

The app is a Flask-based document-generation studio that reads data from
Excel / CSV files, applies Jinja2 templates, and emits PDFs. There is **no
database and no network egress**, so several common classes of
vulnerability (SQL injection, server-side request forgery) do not apply.

## Summary of findings

| # | Severity | Issue | File | Fixed |
|---|----------|-------|------|-------|
| 1 | **Critical** | Path traversal via `file` query param and uploaded filename in `/upload`, `/generate`, `/generate-all` | `app_web.py` | Yes |
| 2 | **High** | Hard-coded Flask `secret_key` committed in source | `app_web.py` | Yes |
| 3 | **High** | `app.run(debug=True)` exposes the Werkzeug debugger (remote code execution if reachable) | `app_web.py` | Yes |
| 4 | **High** | `eval()` used on a string assembled from rule expressions in the "safe" evaluator | `rules_engine.py` | Yes |
| 5 | Medium | No upload size limit (DoS by large file upload) | `app_web.py` | Yes |
| 6 | Medium | File-type check uses `str.endswith` on the untrusted filename (can be spoofed, case-sensitive) | `app_web.py` | Yes |
| 7 | Low | No authentication / authorization on any route (all endpoints public) | `app_web.py` | Not fixed — see notes |
| 8 | Informational | No CORS headers set (browser same-origin policy protects cross-origin reads; only an issue if the user adds `flask-cors` with `*`) | `app_web.py` | N/A |
| 9 | Informational | No SQL used anywhere in the project → no SQL-injection surface | — | N/A |
| 10 | Informational | Dependency pins use `>=` rather than exact versions; consider `pip-tools` / `pip-audit` in CI | `requirements.txt` | Not changed |

## Details

### 1. Path traversal in file-upload / file-selection routes — CRITICAL

```python
# app_web.py (before)
@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("datafile")
    ...
    save_p = UPLOAD_DIR / f.filename          # <-- user-controlled
    f.save(str(save_p))

@app.route("/generate")
def generate():
    active_file = request.args.get("file", "")
    dp = str(UPLOAD_DIR / active_file) if active_file else None   # <-- user-controlled
```

An attacker could:
- Upload a file whose multipart `filename` is `../../etc/cron.d/evil` (or any
  path escape) and the server would happily write to it. Browsers normally
  strip path components, but the HTTP layer does not — a crafted client can
  send anything.
- Pass `?file=../engine.py` to `/generate` and make the server attempt to
  read arbitrary files as data (and leak error messages / partial content
  via the flash message).

**Remediation (applied):**
- `werkzeug.utils.secure_filename` is now used to normalise the upload name.
- The `file` query parameter is restricted to the basename (`os.path.basename`)
  and the resolved absolute path is required to be contained within
  `UPLOAD_DIR` using `Path.resolve().is_relative_to(UPLOAD_DIR.resolve())`.
- Requests containing obvious traversal sequences are rejected outright.

### 2. Hard-coded Flask `secret_key` — HIGH

```python
app.secret_key = "docgen-2026-secret"
```

The secret is used to sign Flask session cookies and flash messages. Anyone
who can read this repository (or the deployed bytecode) can forge sessions.
**Remediation (applied):** the key is now read from the `FLASK_SECRET_KEY`
environment variable. If the variable is unset, a cryptographically random
key is generated at start-up (with a warning), which means sessions don't
survive a restart — acceptable for a generator app with no real login.

### 3. Werkzeug debugger enabled — HIGH

```python
app.run(debug=True, port=5000, threaded=True)
```

Flask's debug mode enables the Werkzeug interactive debugger. If the port
is reachable from outside `localhost` (or if the app is proxied), anyone who
triggers an exception gets a remote Python shell on the server. This is
one of the most common causes of real-world Flask RCE incidents.

**Remediation (applied):** debug is now gated on the `FLASK_DEBUG`
environment variable and defaults to `False`. The bind host is also gated
on `FLASK_HOST` so the default stays `127.0.0.1`.

### 4. `eval()` in the rules engine — HIGH

```python
# rules_engine.py (before)
def _safe_eval(expr, context):
    ...
    allowed_names = {"True": True, "False": False, "__builtins__": {}}
    return bool(eval(expr_sub, allowed_names))   # noqa: S307
```

Despite the name and the comment, the function calls `eval()` on a string
that is partly derived from the YAML rules file. Two problems:

1. `rules.yaml` is treated as configuration, but a malicious rules file is
   effectively arbitrary code — `"__import__('os').system('...')"`
   would still work because `__builtins__ = {}` is a well-known
   incomplete sandbox (attackers can reach builtins through
   `().__class__.__mro__[-1].__subclasses__()` etc.).
2. Any future feature that sourced a rule from user input would instantly
   become an RCE primitive.

**Remediation (applied):** `_safe_eval` is rewritten to parse the
expression with `ast.parse(..., mode="eval")` and walk the tree,
accepting **only** constants, names (resolved against the supplied
context), unary ops (`not`, unary `+`/`-`), boolean ops (`and`, `or`),
and the numeric/identity comparison operators (`==`, `!=`, `<`, `<=`,
`>`, `>=`, `in`, `not in`). Anything else (function calls, attribute
access, subscripts, imports, etc.) is rejected and the rule evaluates to
`False`. All existing `rules.yaml` expressions continue to work.

### 5 & 6. Missing upload size limit / weak extension check

`f.filename.endswith((".xlsx", ".csv"))` is case-sensitive and checks the
*client-supplied* filename, not the actual content. It does not bound the
upload size, so a large file can fill the disk.

**Remediation (applied):** added `MAX_CONTENT_LENGTH = 10 MB`, switched
to a case-insensitive `os.path.splitext` check, and reject unknown
extensions before writing to disk.

### 7. Missing authentication — Low (documented, not fixed)

All routes (`/`, `/upload`, `/generate`, `/generate-all`, `/batch-status`,
`/audit-log`) are unauthenticated. In the README the app is described as
offline / single-user on `localhost:5000`, so this is consistent with the
design. Before deploying on a shared network, add authentication (e.g.
`flask-login` or an auth proxy). This PR intentionally leaves the design
untouched.

### 8. CORS

No CORS middleware is installed; the default same-origin browser policy
protects the endpoints. No change needed; if a future change adds
`flask-cors`, restrict it to an explicit allow-list rather than `*`.

### 9. SQL injection

The app does not use a database; all data is loaded via `openpyxl` /
`csv.DictReader`. No SQL-injection surface exists.

### 10. Dependencies

`requirements.txt` uses `>=` version pins. This is not in itself a
vulnerability but it makes builds non-reproducible. Recommend adopting
`pip-tools` (`pip-compile`) or Dependabot + `pip-audit` in CI. No version
changes are included in this PR to keep the scope tight.

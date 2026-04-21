# Testing the Document Generation Studio

How to verify the document-generation flow (data source + template → PDF merge)
without relying on a browser. Chrome is often unavailable on Devin VMs, and
`pypdf` can't always text-extract from WeasyPrint output, so this SKILL
documents the workarounds that actually work.

## Start the app

```bash
python3 app_web.py    # Flask on http://localhost:5000, threaded=True
```

The server keeps running even if the shell that launched it is killed — check
with `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5000/`.

## Routes exercised by the UI (use these for HTTP-driven tests)

| Route | Method | Purpose |
|---|---|---|
| `/upload?type=<doc_type>` | POST | Save uploaded Excel/CSV to `uploads/<filename>`. Field name: `datafile`. |
| `/upload-template?type=<doc_type>` | POST | Save uploaded DOCX to `uploads/templates/<doc_type>.docx`. Field name: `docxfile`. Must be `.docx`. |
| `/reset-template?type=<doc_type>` | GET | Delete the uploaded DOCX template for that doc type. |
| `/generate?type=<doc_type>&row=<i>&file=<filename>&channel=<c>` | GET | Returns PDF/HTML/TXT/DOCX. `channel` in `pdf\|email\|sms\|docx` (default `pdf`). Uses uploaded DOCX for pdf+docx only. |
| `/?type=<doc_type>&file=<filename>` | GET | Index HTML — shows the records table and the "①½ Document Template" card labeling which template is active. |
| `/studio?type=<doc_type>` | GET | Template Studio: placeholder/binding editor + Channel dropdown (Slice #4). |

Supported `doc_type` values: `bank_statement`, `insurance_policy`, `telecom_bill`, `payroll_statement`.

## Adversarial merge test (works without a browser)

Build a test XLSX with **unique sentinel values** for each field, upload a
DOCX template containing `{{ field_name }}` placeholders, generate, and assert
the sentinels appear in the PDF text. Use `pypdf.PdfReader` to extract text —
**this works on DOCX-rendered PDFs** (ReportLab standard fonts) but NOT on
HTML-rendered PDFs on VMs where WeasyPrint isn't installed (ReportLab
fallback's Type3 fonts aren't pypdf-extractable).

For the HTML path, assert on byte size and the records-table HTML instead.

### Minimal driver skeleton

```python
import requests
from io import BytesIO
from pypdf import PdfReader

BASE = "http://localhost:5000"
s = requests.Session()

# Upload the data source
with open("test_upload.xlsx", "rb") as fh:
    s.post(f"{BASE}/upload?type=bank_statement", files={"datafile": fh})

# Upload the DOCX template (this is the PR #3 feature)
with open("sample_template.docx", "rb") as fh:
    s.post(f"{BASE}/upload-template?type=bank_statement", files={"docxfile": fh})

# Verify UI reflects the uploaded template
html = s.get(f"{BASE}/?type=bank_statement&file=test_upload.xlsx").text
assert "Using uploaded DOCX: <code>bank_statement.docx</code>" in html

# Generate and merge
pdf_bytes = s.get(f"{BASE}/generate?type=bank_statement&row=0&file=test_upload.xlsx").content
text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf_bytes)).pages)
assert "ZZTESTHOLDER_DEVIN_XYZ999" in text   # sentinel
assert "{{ " not in text                     # no unsubstituted placeholders

# Revert
s.get(f"{BASE}/reset-template?type=bank_statement")
```

## Canonical IR path (Slice #3) — IR vs legacy email/SMS

`bank_statement` email + SMS route through the Canonical IR
(`document_ir.Document` → `ir_renderers.html/text`) when
`config/schema.json` has `document_types.<doc_type>.use_ir = true` AND an
IR builder is registered for that doc type (`ir_builders.has_builder(dt)`).
Either alone is not sufficient (`engine._use_ir` requires both). PDF and
DOCX channels are never IR-routed.

### DOM sentinels that distinguish IR-rendered email from legacy-template email

| Marker | IR email (`ir_renderers/html.py`) | Legacy email (`templates/email/_base.html`) |
|---|---|---|
| Preheader style | `display:none;visibility:hidden;opacity:0;color:transparent` | `display:none !important;…mso-hide:all;` |
| `mso-hide:all` | **absent** | present |
| Skip link `aria-label` | **absent** (plain `<a>`) | `aria-label="Skip to main content"` present |
| `role="banner"` / `role="contentinfo"` | **absent** | both present |
| `extra["renderer"]` in `generate_channel` | `"ir"` | `"template"` |

Assert the **absence** set — a broken IR path would fall through to the
legacy template and visibly reintroduce the legacy markers.

### Flipping `use_ir` requires a Flask restart

`data_loader.load_schema()` caches `schema.json` in a module-level
`_schema_cache`, so editing `config/schema.json` mid-session does NOT take
effect until the `app_web.py` process is restarted. The dev server's
reloader does NOT watch JSON files either.

```bash
pkill -f "python3 app_web.py"; sleep 1
python3 app_web.py > /tmp/flask.log 2>&1 &
sleep 3
```

Use SHA-256 of the `/generate?channel=email` response to prove the flip
actually changed the code path (IR baseline bytes ≈ 9162; legacy ≈ 9503
for bank_statement row 0 — different byte-counts alone are a quick smoke
test). Restoring `use_ir:true` must yield a **byte-identical** response
to the original IR baseline (A5-style assertion — catches hidden state).

### Compact SMS is NOT single-segment for bank_statement

The compact flavour (`ir_renderers.text.render_text(doc, flavor="compact")`)
emits one line per top-level block (alert, heading, paragraphs,
key-value grids). For `bank_statement` row 0 this is ~664 chars → **5**
GSM-7 segments, not 1. Don't predict single-segment in test plans.

The real guarantees that matter for a regression test:

```python
from sms_renderer import encoding_of, segment_body
body = ir_renderers.text.render_text(doc, flavor="compact")
assert encoding_of(body) == "GSM-7"               # Devin Review #4 fix
parts = segment_body(body)["parts"]
assert all(len(p) <= 153 for p in parts)          # Devin Review #2 fix
for ch in "\u2013\u2014\u2018\u2019\u201c\u201d": # en/em-dash, smart quotes
    assert ch not in body
```

### Reproducing a `/generate` context in-process

The canonical way to get the same context `/generate` would pass to the
IR builder (for ad-hoc unit-style assertions without hitting HTTP):

```python
from engine import load_record          # NOT `_load_record`
from rules_engine import apply_rules
import ir_builders

rec = load_record("bank_statement", 0)
ctx = apply_rules("bank_statement", rec)
doc = ir_builders.build("bank_statement", ctx)    # frozen Document
```

Note `load_record` + `apply_rules` are the public names in `engine.py` /
`rules_engine.py` — there's no `_load_record` or `_build_context`.

## Gotchas

- **`config/schema.json` and `config/rules.yaml`** — `data_loader.py` and
  `rules_engine.py` read from `config/`. If they're at the repo root the UI
  500s on every doc-type click.
- **`enumerate` Jinja filter** — `app_web.py`'s index template uses
  `rows|enumerate`. Must be registered in `app.jinja_env.filters` (not just
  `globals`) or the records page raises `TemplateRuntimeError`.
- **`transactions` column** — if the test XLSX has a `transactions` string
  column, `rules_engine._compute_fields` iterates over it as if it were a
  list and throws `'str' object has no attribute 'get'`. Omit the column
  entirely for simple tests; `record.get("transactions", [])` returns `[]`.
- **DOCX fidelity** — the ReportLab-based DOCX renderer preserves only bold
  / italic / underline, Heading 1-3, and basic tables. Headers, footers,
  images, lists, and custom styles are dropped. Don't expect pixel-perfect
  fidelity.
- **Chrome on the VM** — often exits with code 7 and no CDP listener on
  `:29229`. Don't try to script the UI; drive the Flask routes directly
  with `requests` as shown above. Same handlers, same proof.
- **PDF → PNG for visual evidence** — `convert` (ImageMagick) needs
  `ghostscript` (NOT pre-installed) AND a loosened PDF policy in
  `/etc/ImageMagick-6/policy.xml` (rewrite `rights="none"` → `rights="read|write"`
  for the PDF coder). Offline VMs can't `apt install ghostscript`; in that
  case attach the PDFs themselves rather than PNGs.
- **Schema cache** — `data_loader._schema_cache` is a module-level dict
  populated on first `load_schema()`. Live edits to `config/schema.json`
  require a Flask restart to be picked up (see IR-flag-flipping section).
- **Studio bindings are pdf/docx only** — `engine.generate_channel`
  applies Template Studio bindings ONLY when `channel in ("pdf", "docx")`.
  Asserting that an email/SMS responds to a Studio binding is wrong — they
  never see the bindings (intentional: bindings map DOCX placeholder names
  which would collide with rule-computed context keys).

## Test accounts / auth

No auth. Flask runs with `debug=True` on a single process, accepts uploads
from anyone on localhost.

## Devin Secrets Needed

None — the app is fully local and has no external dependencies.

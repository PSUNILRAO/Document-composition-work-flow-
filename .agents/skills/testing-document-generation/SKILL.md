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
| `/generate?type=<doc_type>&row=<i>&file=<filename>` | GET | Returns `application/pdf`. Uses uploaded DOCX if present, else HTML template. |
| `/?type=<doc_type>&file=<filename>` | GET | Index HTML — shows the records table and the "①½ Document Template" card labeling which template is active. |

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

## Test accounts / auth

No auth. Flask runs with `debug=True` on a single process, accepts uploads
from anyone on localhost.

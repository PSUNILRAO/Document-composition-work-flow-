"""create_templates.py — Creates all 4 Jinja2 HTML templates."""
import os
os.makedirs("templates", exist_ok=True)

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
       font-size: 10.5pt; color: #0D1117; background: white; line-height: 1.55; }
.page { width: 210mm; min-height: 297mm; padding: 16mm 18mm 16mm 18mm; }
.hbar { border-bottom: 3px solid #0969DA; padding-bottom: 10px; margin-bottom: 16px;
        display: flex; justify-content: space-between; align-items: flex-end; }
.co-name { font-size: 16pt; font-weight: 700; color: #0D1117; letter-spacing: -.3px; }
.co-sub   { font-size: 8pt; color: #57606A; margin-top: 2px; }
.doc-badge { background: #0969DA; color: white; padding: 4px 12px;
             border-radius: 4px; font-size: 10pt; font-weight: 700;
             letter-spacing: .5px; }
.section-title { font-size: 9pt; font-weight: 700; color: #57606A;
                 letter-spacing: 1.5px; text-transform: uppercase;
                 margin: 14px 0 6px; border-bottom: 1px solid #E5E7EB; padding-bottom: 3px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.three-col { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
.kv { margin: 4px 0; }
.kv .k { font-size: 8.5pt; color: #57606A; }
.kv .v { font-weight: 600; font-size: 10pt; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 9.5pt; }
th { background: #0D1117; color: white; padding: 7px 10px; text-align: left;
     font-size: 9pt; font-weight: 600; }
td { padding: 6px 10px; border-bottom: 1px solid #E5E7EB; }
tr:nth-child(even) td { background: #F6F8FA; }
.amount { text-align: right; font-variant-numeric: tabular-nums; }
.total-row td { font-weight: 700; border-top: 2px solid #0969DA;
                background: #DDF4FF !important; font-size: 10.5pt; }
.summary-box { background: #0D1117; color: white; border-radius: 6px;
               padding: 14px 18px; margin: 12px 0;
               display: flex; justify-content: space-between; align-items: center; }
.summary-box .big { font-size: 20pt; font-weight: 700; }
.summary-box .lbl { font-size: 8.5pt; opacity: .7; margin-bottom: 3px; }
.alert { padding: 8px 12px; border-radius: 4px; margin: 6px 0;
         font-size: 9.5pt; display: flex; gap: 8px; }
.alert-warning  { background: #FFF8C5; color: #9A6700; border-left: 3px solid #D4A017; }
.alert-critical { background: #FFEBE9; color: #CF222E; border-left: 3px solid #CF222E; font-weight: 600; }
.alert-info     { background: #DDF4FF; color: #0550AE; border-left: 3px solid #0969DA; }
.highlight-box { background: #F6F8FA; border: 1px solid #E5E7EB;
                 border-radius: 4px; padding: 12px 14px; margin: 10px 0; }
.stat-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; margin: 10px 0; }
.stat { border: 1px solid #E5E7EB; border-radius: 5px; padding: 10px 12px; }
.stat .val { font-size: 14pt; font-weight: 700; color: #0969DA;
             font-variant-numeric: tabular-nums; }
.stat .lbl { font-size: 8pt; color: #57606A; margin-top: 2px; }
.footer { border-top: 1px solid #E5E7EB; margin-top: 20px; padding-top: 10px;
          font-size: 8pt; color: #57606A; display: flex;
          justify-content: space-between; }
.sig-line { border-bottom: 1px solid #9CA3AF; width: 200px;
            margin: 28px 0 5px; }
.badge { display:inline-block; padding:2px 9px; border-radius:10px; font-size:9pt; font-weight:600; }
.badge-green { background:#DAFBE1; color:#1A7F37; }
.badge-red   { background:#FFEBE9; color:#CF222E; }
.badge-blue  { background:#DDF4FF; color:#0550AE; }
.badge-warn  { background:#FFF8C5; color:#9A6700; }
"""

# ── 1. Bank Statement ──────────────────────────────────────────────────────────
BANK = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>{CSS}</style></head><body><div class="page">

<div class="hbar">
  <div>
    <div class="co-name">SecureBank</div>
    <div class="co-sub">Member FDIC · securebank.com · 1-800-555-0100</div>
  </div>
  <div class="doc-badge">ACCOUNT STATEMENT</div>
</div>

{{%- for alert in __alerts %}}
<div class="alert alert-{{{{ alert.severity }}}}">
  {{{{ '⚠' if alert.severity == 'warning' else ('🚨' if alert.severity == 'critical' else 'ℹ') }}}}
  {{{{ alert.message }}}}
</div>
{{%- endfor %}}

<div class="two-col" style="margin-top:12px">
  <div>
    <div class="section-title">Account Holder</div>
    <div class="kv"><div class="v">{{{{ account_holder }}}}</div></div>
    <div class="kv"><div class="k">Account Number</div><div class="v">{{{{ account_number }}}}</div></div>
    <div class="kv"><div class="k">Account Type</div><div class="v">{{{{ account_type }}}}</div></div>
    {{%- if branch_name %}}
    <div class="kv"><div class="k">Branch</div><div class="v">{{{{ branch_name }}}}</div></div>
    {{%- endif %}}
  </div>
  <div>
    <div class="section-title">Statement Period</div>
    <div class="kv"><div class="k">Statement Date</div><div class="v">{{{{ statement_date }}}}</div></div>
    <div class="kv"><div class="k">Period</div><div class="v">{{{{ period_from }}}} – {{{{ period_to }}}}</div></div>
    {{%- if interest_rate %}}
    <div class="kv"><div class="k">Interest Rate</div>
      <div class="v">{{{{ interest_rate | percent }}}}</div></div>
    {{%- endif %}}
  </div>
</div>

<div class="stat-grid" style="margin-top:14px">
  <div class="stat">
    <div class="lbl">OPENING BALANCE</div>
    <div class="val">{{{{ opening_balance | currency }}}}</div>
  </div>
  <div class="stat">
    <div class="lbl">TOTAL CREDITS</div>
    <div class="val" style="color:#1A7F37">{{{{ total_credits | currency }}}}</div>
  </div>
  <div class="stat">
    <div class="lbl">TOTAL DEBITS</div>
    <div class="val" style="color:#CF222E">{{{{ total_debits | currency }}}}</div>
  </div>
</div>

<div class="summary-box">
  <div>
    <div class="lbl">CLOSING BALANCE</div>
    <div class="big" style="{{{{ __field_styles.get('closing_balance','') }}}}">
      {{{{ closing_balance | currency }}}}
    </div>
  </div>
  <div style="text-align:right">
    <div class="lbl">As at {{{{ statement_date }}}}</div>
    {{%- if minimum_balance %}}
    <div style="font-size:9pt;opacity:.8">Min required: {{{{ minimum_balance | currency }}}}</div>
    {{%- endif %}}
  </div>
</div>

<div class="section-title">Transaction History</div>
<table>
  <thead><tr>
    <th>Date</th><th>Description</th>
    <th class="amount">Credits</th>
    <th class="amount">Debits</th>
    <th class="amount">Balance</th>
  </tr></thead>
  <tbody>
  {{%- for tx in transactions %}}
  {{%- set amt = tx.amount | float %}}
  <tr style="{{{{ tx | row_style(__row_styles) }}}}">
    <td>{{{{ tx.date }}}}</td>
    <td>{{{{ tx.description }}}}</td>
    <td class="amount" style="color:#1A7F37">
      {{%- if amt > 0 %}}{{{{ amt | currency }}}}{{%- endif %}}
    </td>
    <td class="amount" style="color:#CF222E">
      {{%- if amt < 0 %}}{{{{ (amt * -1) | currency }}}}{{%- endif %}}
    </td>
    <td class="amount">{{{{ tx.balance }}}}</td>
  </tr>
  {{%- endfor %}}
  </tbody>
</table>

<div class="footer">
  <span>SecureBank · {{{{ branch_name or 'Main Branch' }}}}</span>
  <span>Statement generated: {{{{ statement_date }}}}</span>
  <span>{{{{ __global.footer_disclaimer.text if __global.footer_disclaimer.enabled else '' }}}}</span>
</div>
</div></body></html>"""

# ── 2. Insurance Policy ────────────────────────────────────────────────────────
INSURANCE = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>{CSS}</style></head><body><div class="page">

<div class="hbar">
  <div>
    <div class="co-name">Assurance Life & General</div>
    <div class="co-sub">Licensed Insurer · assurancegroup.com · 1-800-555-0200</div>
  </div>
  <div class="doc-badge">POLICY DOCUMENT</div>
</div>

{{%- for alert in __alerts %}}
<div class="alert alert-{{{{ alert.severity }}}}">
  {{{{ '⚠' if alert.severity == 'warning' else ('🚨' if alert.severity == 'critical' else 'ℹ') }}}}
  {{{{ alert.message }}}}
</div>
{{%- endfor %}}

<div class="two-col" style="margin-top:12px">
  <div>
    <div class="section-title">Policy Details</div>
    <div class="kv"><div class="k">Policy Number</div><div class="v">{{{{ policy_number }}}}</div></div>
    <div class="kv"><div class="k">Policy Type</div>
      <div class="v"><span class="badge badge-blue">{{{{ policy_type }}}}</span></div></div>
    <div class="kv"><div class="k">Effective Date</div><div class="v">{{{{ effective_date }}}}</div></div>
    <div class="kv"><div class="k">Expiry Date</div><div class="v">{{{{ expiry_date }}}}</div></div>
    {{%- if days_to_expiry is defined %}}
    <div class="kv"><div class="k">Status</div>
      <div class="v">
        {{%- if days_to_expiry > 90 %}}
          <span class="badge badge-green">Active</span>
        {{%- elif days_to_expiry > 0 %}}
          <span class="badge badge-warn">Expiring Soon</span>
        {{%- else %}}
          <span class="badge badge-red">Expired</span>
        {{%- endif %}}
      </div></div>
    {{%- endif %}}
  </div>
  <div>
    <div class="section-title">Insured</div>
    <div class="kv"><div class="v">{{{{ insured_name }}}}</div></div>
    <div class="kv"><div class="k">Date of Birth</div><div class="v">{{{{ insured_dob }}}}</div></div>
    <div class="kv"><div class="k">Address</div><div class="v">{{{{ insured_address }}}}</div></div>
    {{%- if agent_name %}}
    <div class="kv"><div class="k">Agent</div><div class="v">{{{{ agent_name }}}} ({{{{ agent_code }}}})</div></div>
    {{%- endif %}}
  </div>
</div>

<div class="summary-box">
  <div>
    <div class="lbl">SUM ASSURED</div>
    <div class="big" style="{{{{ __field_styles.get('sum_assured','') }}}}">
      {{{{ sum_assured | currency }}}}
    </div>
  </div>
  <div style="text-align:right">
    <div class="lbl">PREMIUM</div>
    <div style="font-size:14pt;font-weight:700">{{{{ premium_amount | currency }}}}</div>
    <div style="font-size:9pt;opacity:.8">{{{{ premium_frequency }}}}</div>
  </div>
  {{%- if deductible %}}
  <div style="text-align:right">
    <div class="lbl">DEDUCTIBLE</div>
    <div style="font-size:14pt;font-weight:700">{{{{ deductible | currency }}}}</div>
  </div>
  {{%- endif %}}
</div>

<div class="section-title">Coverage Details</div>
<table>
  <thead><tr><th>Coverage</th><th>Limit</th><th>Notes</th></tr></thead>
  <tbody>
  {{%- for cov in coverages %}}
  <tr>
    <td>{{{{ cov.coverage_name }}}}</td>
    <td class="amount">{{{{ cov.coverage_limit }}}}</td>
    <td>{{{{ cov.notes }}}}</td>
  </tr>
  {{%- endfor %}}
  </tbody>
</table>

{{%- if exclusions %}}
<div class="section-title">Key Exclusions</div>
<ul style="font-size:9.5pt;padding-left:18px;color:#57606A">
  {{%- for ex in exclusions %}}<li>{{{{ ex }}}}</li>{{%- endfor %}}
</ul>
{{%- endif %}}

<div class="section-title">Declaration & Signatures</div>
<div class="two-col">
  <div>
    <p style="font-size:9pt;color:#57606A">
      This policy is issued subject to the terms and conditions of the policy document.
      The insurer confirms coverage from {{{{ effective_date }}}} to {{{{ expiry_date }}}}.
    </p>
    <div class="sig-line"></div>
    <div style="font-size:9pt">Authorised Signatory, Assurance Life &amp; General</div>
  </div>
  <div>
    <div class="sig-line"></div>
    <div style="font-size:9pt">{{{{ insured_name }}}} (Policyholder)</div>
  </div>
</div>

<div class="footer">
  <span>Policy No: {{{{ policy_number }}}}</span>
  <span>Assurance Life &amp; General · Licensed Insurer</span>
  <span>{{{{ __global.footer_disclaimer.text if __global.footer_disclaimer.enabled else '' }}}}</span>
</div>
</div></body></html>"""

# ── 3. Telecom Bill ────────────────────────────────────────────────────────────
TELECOM = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>{CSS}
.usage-bar-wrap {{ background:#E5E7EB; border-radius:4px; height:8px; margin:6px 0; overflow:hidden; }}
.usage-bar {{ height:100%; border-radius:4px;
             background: linear-gradient(90deg, #0969DA, #2DBA4E); }}
.usage-bar.warn {{ background: linear-gradient(90deg, #D4A017, #F97316); }}
.usage-bar.crit {{ background: linear-gradient(90deg, #CF222E, #9A1E1E); }}
</style></head><body><div class="page">

<div class="hbar">
  <div>
    <div class="co-name">ConnectTel</div>
    <div class="co-sub">connecttel.com · 1-800-555-0300 · support@connecttel.com</div>
  </div>
  <div class="doc-badge">BILL STATEMENT</div>
</div>

{{%- for alert in __alerts %}}
<div class="alert alert-{{{{ alert.severity }}}}">
  {{{{ '⚠' if alert.severity == 'warning' else ('🚨' if alert.severity == 'critical' else 'ℹ') }}}}
  {{{{ alert.message }}}}
</div>
{{%- endfor %}}

<div class="two-col" style="margin-top:12px">
  <div>
    <div class="section-title">Account</div>
    <div class="kv"><div class="v">{{{{ customer_name }}}}</div></div>
    <div class="kv"><div class="k">Account No</div><div class="v">{{{{ account_number }}}}</div></div>
    <div class="kv"><div class="k">Address</div><div class="v">{{{{ customer_address }}}}</div></div>
    <div class="kv"><div class="k">Plan</div><div class="v"><span class="badge badge-blue">{{{{ plan_name }}}}</span></div></div>
  </div>
  <div>
    <div class="section-title">Bill Details</div>
    <div class="kv"><div class="k">Bill Number</div><div class="v">{{{{ bill_number }}}}</div></div>
    <div class="kv"><div class="k">Bill Date</div><div class="v">{{{{ bill_date }}}}</div></div>
    <div class="kv"><div class="k">Due Date</div><div class="v">{{{{ due_date }}}}</div></div>
    <div class="kv"><div class="k">Billing Period</div><div class="v">{{{{ billing_period }}}}</div></div>
  </div>
</div>

{{%- if data_used_gb and data_limit_gb %}}
<div class="section-title">Usage Summary</div>
<div class="three-col">
  <div>
    <div class="kv"><div class="k">Data Used</div>
      <div class="v">{{{{ data_used_gb | number(1) }}}} / {{{{ data_limit_gb | number(0) }}}} GB</div></div>
    {{%- set dpct = (data_used_gb / data_limit_gb * 100) | round(0) | int %}}
    <div class="usage-bar-wrap">
      <div class="usage-bar {{{{ 'crit' if dpct >= 100 else ('warn' if dpct >= 80 else '') }}}}"
           style="width:{{{{ [dpct,100]|min }}}}%"></div>
    </div>
    <div style="font-size:8.5pt;color:#57606A">{{{{ dpct }}}}% used</div>
  </div>
  <div>
    <div class="kv"><div class="k">Minutes Used</div>
      <div class="v">{{{{ calls_minutes | number(0) }}}}</div></div>
  </div>
  <div>
    <div class="kv"><div class="k">SMS Sent</div>
      <div class="v">{{{{ sms_count | number(0) }}}}</div></div>
  </div>
</div>
{{%- endif %}}

<div class="summary-box">
  <div>
    <div class="lbl">TOTAL AMOUNT DUE</div>
    <div class="big" style="{{{{ __field_styles.get('total_due','') }}}}">
      {{{{ total_due | currency }}}}
    </div>
  </div>
  <div style="text-align:right">
    <div class="lbl">DUE DATE</div>
    <div style="font-size:13pt;font-weight:700">{{{{ due_date }}}}</div>
    {{%- if days_overdue and days_overdue > 0 %}}
    <div class="badge badge-red" style="margin-top:4px">
      {{{{ days_overdue | number(0) }}}} days overdue
    </div>
    {{%- endif %}}
  </div>
</div>

<div class="section-title">Charge Breakdown</div>
<table>
  <thead><tr><th>Description</th><th class="amount">Amount</th></tr></thead>
  <tbody>
  {{%- for ch in charges %}}
  <tr><td>{{{{ ch.charge_description }}}}</td>
      <td class="amount">{{{{ ch.amount }}}}</td></tr>
  {{%- endfor %}}
  <tr class="total-row">
    <td>TOTAL DUE</td>
    <td class="amount">{{{{ total_due | currency }}}}</td>
  </tr>
  </tbody>
</table>

<div class="footer">
  <span>{{{{ bill_number }}}}</span>
  <span>ConnectTel · connecttel.com</span>
  <span>{{{{ __global.footer_disclaimer.text if __global.footer_disclaimer.enabled else '' }}}}</span>
</div>
</div></body></html>"""

# ── 4. Payroll Statement ───────────────────────────────────────────────────────
PAYROLL = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>{CSS}</style></head><body><div class="page">

<div class="hbar">
  <div>
    <div class="co-name">GlobalTech Inc.</div>
    <div class="co-sub">HR Department · hr@globaltech.com · 1-800-555-0400</div>
  </div>
  <div class="doc-badge">PAY SLIP</div>
</div>

{{%- for alert in __alerts %}}
<div class="alert alert-{{{{ alert.severity }}}}">
  {{{{ 'ℹ' if alert.severity == 'info' else ('⚠' if alert.severity == 'warning' else '🚨') }}}}
  {{{{ alert.message }}}}
</div>
{{%- endfor %}}

<div class="two-col" style="margin-top:12px">
  <div>
    <div class="section-title">Employee</div>
    <div class="kv"><div class="v">{{{{ employee_name }}}}</div></div>
    <div class="kv"><div class="k">Employee ID</div><div class="v">{{{{ employee_id }}}}</div></div>
    <div class="kv"><div class="k">Designation</div><div class="v">{{{{ designation }}}}</div></div>
    <div class="kv"><div class="k">Department</div><div class="v">{{{{ department }}}}</div></div>
  </div>
  <div>
    <div class="section-title">Payment Details</div>
    <div class="kv"><div class="k">Pay Period</div><div class="v">{{{{ pay_period }}}}</div></div>
    <div class="kv"><div class="k">Pay Date</div><div class="v">{{{{ pay_date }}}}</div></div>
    <div class="kv"><div class="k">Bank Account</div><div class="v">{{{{ bank_account }}}}</div></div>
    {{%- if pan_number %}}
    <div class="kv"><div class="k">PAN</div><div class="v">{{{{ pan_number }}}}</div></div>
    {{%- endif %}}
    {{%- if days_worked %}}
    <div class="kv"><div class="k">Days Worked</div>
      <div class="v">{{{{ days_worked | number(0) }}}} / {{{{ total_working_days | number(0) }}}}</div></div>
    {{%- endif %}}
  </div>
</div>

<div class="two-col" style="margin-top:14px">
  <div>
    <div class="section-title">Earnings</div>
    <table>
      <thead><tr><th>Component</th><th class="amount">Amount</th></tr></thead>
      <tbody>
      {{%- for e in earnings %}}
      <tr><td>{{{{ e.description }}}}</td>
          <td class="amount" style="color:#1A7F37">{{{{ e.amount }}}}</td></tr>
      {{%- endfor %}}
      <tr class="total-row">
        <td>GROSS EARNINGS</td>
        <td class="amount">{{{{ gross_earnings | currency }}}}</td>
      </tr>
      </tbody>
    </table>
  </div>
  <div>
    <div class="section-title">Deductions</div>
    <table>
      <thead><tr><th>Component</th><th class="amount">Amount</th></tr></thead>
      <tbody>
      {{%- for d in deductions %}}
      <tr><td>{{{{ d.description }}}}</td>
          <td class="amount" style="color:#CF222E">{{{{ d.amount }}}}</td></tr>
      {{%- endfor %}}
      <tr class="total-row">
        <td>TOTAL DEDUCTIONS</td>
        <td class="amount">{{{{ total_deductions | currency }}}}</td>
      </tr>
      </tbody>
    </table>
  </div>
</div>

<div class="summary-box" style="margin-top:14px">
  <div>
    <div class="lbl">NET PAY (TAKE-HOME)</div>
    <div class="big" style="{{{{ __field_styles.get('net_pay','') }}}}">
      {{{{ net_pay | currency }}}}
    </div>
  </div>
  <div style="text-align:right">
    <div class="lbl">Credited to {{{{ bank_account }}}}</div>
    <div style="font-size:11pt;opacity:.8">{{{{ pay_date }}}}</div>
  </div>
</div>

<div class="footer">
  <span>{{{{ employee_id }}}} · {{{{ employee_name }}}}</span>
  <span>GlobalTech Inc. — {{{{ pay_period }}}}</span>
  <span>{{{{ __global.footer_disclaimer.text if __global.footer_disclaimer.enabled else '' }}}}</span>
</div>
</div></body></html>"""

templates = {
    "templates/bank_statement.html":    BANK,
    "templates/insurance_policy.html":  INSURANCE,
    "templates/telecom_bill.html":       TELECOM,
    "templates/payroll_statement.html":  PAYROLL,
}
for path, content in templates.items():
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅  {path}")

print("\n✅  All templates created. Edit them any time — no restart needed.")

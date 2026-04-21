"""
ir_builders.bank_statement
──────────────────────────
Build a channel-neutral ``Document`` IR for one bank_statement record.

The builder reads the *rules-enriched* context (i.e. the dict returned by
``rules_engine.apply_rules``) — not the raw Excel row. That way the same
alerts / computed fields / masking that the existing HTML template sees
are also available to every IR renderer.
"""

from __future__ import annotations

from document_ir import (
    BulletList,
    Callout,
    Document,
    Heading,
    KeyValue,
    KeyValueGrid,
    Paragraph,
    Section,
    Separator,
    Severity,
    Strong,
    Table,
    TableCell,
    Text,
    para,
)
from . import register


def _currency(value, symbol: str = "$") -> str:
    try:
        return f"{symbol}{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value) if value is not None else ""


def _text(value) -> str:
    return "" if value is None else str(value)


def _alerts(context: dict) -> tuple[Callout, ...]:
    out: list[Callout] = []
    for alert in context.get("__alerts", []) or ():
        sev = alert.get("severity", "info")
        if sev not in ("info", "warning", "critical"):
            sev = "info"
        out.append(Callout(severity=sev, text=_text(alert.get("message", ""))))
    return tuple(out)


def build(context: dict) -> Document:
    """Translate a bank_statement rules-enriched context into a ``Document``."""
    holder = _text(context.get("account_holder"))
    number = _text(context.get("account_number"))
    acct_type = _text(context.get("account_type") or "Account")
    period_from = _text(context.get("period_from"))
    period_to = _text(context.get("period_to"))

    header = Section(
        role="header",
        heading=Heading(level=1, text=f"{acct_type} Statement"),
        blocks=(
            para(f"{period_from} – {period_to}"),
        ),
    )

    account_info = KeyValueGrid(
        caption="Account",
        items=(
            KeyValue(label="Account holder", value=holder),
            KeyValue(label="Account number", value=number),
            KeyValue(label="Account type", value=acct_type),
            *(
                (KeyValue(label="Branch", value=_text(context["branch_name"])),)
                if context.get("branch_name")
                else ()
            ),
        ),
    )

    highlights = KeyValueGrid(
        caption="Highlights",
        items=(
            KeyValue(label="Opening balance",
                     value=_currency(context.get("opening_balance"))),
            KeyValue(label="Total credits",
                     value=_currency(context.get("total_credits"))),
            KeyValue(label="Total debits",
                     value=_currency(context.get("total_debits"))),
            KeyValue(label="Closing balance",
                     value=_currency(context.get("closing_balance"))),
        ),
    )

    transactions = context.get("transactions") or []
    tx_table: tuple = ()
    if transactions:
        headers = (
            TableCell("Date", align="left"),
            TableCell("Description", align="left"),
            TableCell("Amount", align="right"),
        )
        rows: list[tuple[TableCell, ...]] = []
        for t in transactions:
            rows.append((
                TableCell(_text(t.get("date")), align="left"),
                TableCell(_text(t.get("description")), align="left"),
                TableCell(_currency(t.get("amount")), align="right"),
            ))
        tx_table = (Table(
            caption="Recent transactions",
            headers=headers,
            rows=tuple(rows),
        ),)

    salutation = Paragraph((Text("Dear "), Strong(holder or ""), Text(",")))

    greeting = Paragraph((
        Text("Your statement for account "),
        Strong(number or ""),
        Text(" is now available. Highlights for this period:"),
    ))

    main_blocks: list = [
        salutation,
        greeting,
        account_info,
        highlights,
    ]
    main_blocks.extend(tx_table)
    main_blocks.append(
        para(
            "View the full PDF statement in your online banking portal or "
            f"contact us at {context.get('branch_name') or 'your branch'} "
            "for assistance."
        )
    )

    main = Section(role="main", blocks=tuple(main_blocks))

    footer = Section(
        role="footer",
        blocks=(
            Separator(),
            para(
                "This is an automatically generated statement. "
                "For questions about your account, contact SecureBank at "
                "1-800-555-0100."
            ),
        ),
    )

    return Document(
        doc_type="bank_statement",
        title=f"{acct_type} Statement {number}".strip(),
        language="en",
        metadata={
            "account_number": number,
            "period_from": period_from,
            "period_to": period_to,
            "closing_balance": _currency(context.get("closing_balance")),
        },
        alerts=_alerts(context),
        blocks=(header, main, footer),
    )


register("bank_statement", build)

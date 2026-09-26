"""
Treasury & Trade POC -- builds a pictorial "journey" diagram for one or more
shipments: shipment -> LC -> remittance/payment -> financing/EEFC -> FX
hedge -> MTM, rendered as a Mermaid flowchart.

Deliberately does NOT go through Claude/NL-to-SQL. The data pull here is
hand-written, direct DuckDB queries (one simple filter per table), so a
journey diagram is always structurally correct regardless of whatever the
chat's own SQL generation does that day -- this sidesteps the whole class of
SQL-generation bugs (ambiguous columns, dropped join keys, token limits)
this project has been fixing elsewhere. Mermaid renders client-side in the
browser via a CDN script (see render_mermaid_html), so this adds no new
Python package and no new system-level install.
"""

import duckdb
import pandas as pd

JOURNEY_KEYWORDS = (
    "journey", "trace", "flow", "diagram", "visuali", "picture", "pictorial",
    "graphically", "map out", "walk me through", "full picture",
    "complete picture", "end to end", "end-to-end",
)

MAX_AUTO_DIAGRAM_SHIPMENTS = 12

MERMAID_CDN_URL = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"


def question_wants_diagram(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in JOURNEY_KEYWORDS)


def _fetch_df(con: duckdb.DuckDBPyConnection, sql: str, params: list) -> pd.DataFrame:
    return con.execute(sql, params).fetchdf()


def _val(row, col):
    """Safe accessor: returns None for NULL/NaN/NaT/blank-string, the real
    value otherwise. pd.NaT is truthy in this pandas version, so every
    optional field must go through pd.isna() explicitly rather than a plain
    `if value:` check."""
    if col not in row or pd.isna(row[col]):
        return None
    v = row[col]
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def get_shipment_journey(con: duckdb.DuckDBPyConnection, shipment_id: str):
    """Pulls every record linked to one shipment via direct, explicit
    queries. Returns None if the shipment_id doesn't exist."""
    ship_df = _fetch_df(con, "SELECT * FROM shipments WHERE shipment_id = ?", [shipment_id])
    if ship_df.empty:
        return None
    ship = ship_df.iloc[0]

    cp_df = _fetch_df(con, "SELECT * FROM counterparties WHERE counterparty_id = ?", [ship["counterparty_id"]])
    cp = cp_df.iloc[0] if not cp_df.empty else None

    lc = _fetch_df(con, "SELECT * FROM letters_of_credit WHERE shipment_id = ?", [shipment_id])
    rmt = _fetch_df(con, "SELECT * FROM remittances WHERE shipment_id = ?", [shipment_id])
    pay = _fetch_df(con, "SELECT * FROM payments WHERE shipment_id = ?", [shipment_id])
    fac = _fetch_df(con, "SELECT * FROM trade_finance_facilities WHERE linked_shipment_id = ?", [shipment_id])
    fx = _fetch_df(con, "SELECT * FROM fx_bookings WHERE linked_exposure_id = ?", [shipment_id])
    rollovers = _fetch_df(con, "SELECT * FROM fx_rollovers WHERE shipment_id = ?", [shipment_id])
    util = _fetch_df(con, "SELECT * FROM fund_utilization WHERE shipment_id = ?", [shipment_id])

    fx_deal_ids = [x for x in fx["fx_deal_id"].tolist() if x and not pd.isna(x)]
    if fx_deal_ids:
        placeholders = ",".join(["?"] * len(fx_deal_ids))
        mtm = _fetch_df(con, f"SELECT * FROM mtm_report WHERE fx_deal_id IN ({placeholders})", fx_deal_ids)
    else:
        mtm = pd.DataFrame()

    eefc_txn_ids = [_val(r, "linked_eefc_txn_id") for _, r in util.iterrows()]
    eefc_txn_ids = [x for x in eefc_txn_ids if x]
    if eefc_txn_ids:
        placeholders = ",".join(["?"] * len(eefc_txn_ids))
        eefc = _fetch_df(con, f"SELECT * FROM eefc_transactions WHERE eefc_txn_id IN ({placeholders})", eefc_txn_ids)
    else:
        eefc = pd.DataFrame()

    return {
        "shipment": ship, "counterparty": cp, "lc": lc, "remittance": rmt,
        "payment": pay, "facility": fac, "fx": fx, "rollovers": rollovers, "mtm": mtm,
        "utilization": util, "eefc": eefc,
    }


def _esc(text) -> str:
    """Make a value safe to embed inside a double-quoted Mermaid node label.
    Mermaid labels can't contain a raw double quote or newline."""
    if text is None:
        return ""
    return str(text).replace('"', "'").replace("\n", " ").strip()


def _date(row, col):
    """Like _val, but formats a DATE column as YYYY-MM-DD. DuckDB DATE
    columns come back through fetchdf() as pandas Timestamps, which str()
    would otherwise render with a spurious trailing 00:00:00 time-of-day."""
    v = _val(row, col)
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v)


def _money(amount, currency) -> str:
    if amount is None or currency is None:
        return ""
    try:
        return f"{float(amount):,.2f} {currency}"
    except (TypeError, ValueError):
        return f"{amount} {currency}"


def _node(node_id: str, lines: list) -> str:
    label = "<br/>".join(_esc(l) for l in lines if l)
    return f'{node_id}["{label}"]'


def shipment_mermaid_block(journey: dict, block_index: int):
    """Returns the Mermaid subgraph text for one shipment's full journey."""
    ship = journey["shipment"]
    cp = journey["counterparty"]
    sid = ship["shipment_id"]
    px = f"S{block_index}_"
    out = []

    ship_node = f"{px}ship"
    cp_line = f'Counterparty: {cp["name"]} ({cp["country"]})' if cp is not None else ""
    out.append(_node(ship_node, [
        f'\U0001F4E6 {sid}', _val(ship, "product"),
        f'{_val(ship, "quantity_mt")} MT | {_val(ship, "incoterm")}',
        f'{str(_val(ship, "direction") or "").upper()} | {_val(ship, "status")}',
        f'ETD {_date(ship, "etd")} → ETA {_date(ship, "eta")}',
        _money(_val(ship, "invoice_value"), _val(ship, "currency")),
        cp_line,
    ]))

    if not journey["lc"].empty:
        lc = journey["lc"].iloc[0]
        lc_node = f"{px}lc"
        util_amt = _val(lc, "utilized_amount")
        out.append(_node(lc_node, [
            f'\U0001F4C4 {lc["lc_id"]}', f'Status: {_val(lc, "status")}',
            _money(_val(lc, "amount"), _val(lc, "currency")),
            f'Utilized: {_money(util_amt, _val(lc, "currency"))}' if util_amt else "Utilized: 0",
            f'Issued {_date(lc, "issue_date")} → Expiry {_date(lc, "expiry_date")}',
            "⚠ Discrepancy flagged" if bool(lc["discrepancy_flag"]) else "",
        ]))
        out.append(f'{lc_node} -.->|LC-backed| {ship_node}')

    settlement_node = None
    if not journey["remittance"].empty:
        r = journey["remittance"].iloc[0]
        settlement_node = f"{px}rmt"
        days_overdue = _val(r, "days_overdue")
        out.append(_node(settlement_node, [
            f'\U0001F4B0 {r["remittance_id"]}', f'Mode: {_val(r, "mode")}',
            _money(_val(r, "amount"), _val(r, "currency")), f'Status: {_val(r, "status")}',
            f'Expected {_date(r, "expected_date")}',
            f'Actual {_date(r, "actual_date")}' if _date(r, "actual_date") else "",
            f'{int(days_overdue)} days overdue' if days_overdue else "",
        ]))
        out.append(f'{ship_node} --> {settlement_node}')
    elif not journey["payment"].empty:
        p = journey["payment"].iloc[0]
        settlement_node = f"{px}pay"
        out.append(_node(settlement_node, [
            f'\U0001F4B8 {p["payment_id"]}', f'Mode: {_val(p, "mode")}',
            _money(_val(p, "amount"), _val(p, "currency")), f'Status: {_val(p, "status")}',
            f'Payment date: {_date(p, "payment_date")}' if _date(p, "payment_date") else "Not yet paid",
        ]))
        out.append(f'{ship_node} --> {settlement_node}')

    if not journey["facility"].empty:
        f = journey["facility"].iloc[0]
        fac_node = f"{px}fac"
        repayment_date = _date(f, "repayment_date")
        out.append(_node(fac_node, [
            f'\U0001F3E6 {f["facility_id"]}', f'{_val(f, "facility_type")} ({_val(f, "direction")})',
            _money(_val(f, "principal_amount"), _val(f, "currency")),
            f'Status: {_val(f, "status")}',
            f'Disbursed {_date(f, "disbursement_date")} → Due {_date(f, "due_date")}',
            f'Repaid {repayment_date} ({_val(f, "repayment_source")})' if repayment_date else "",
        ]))
        if _val(f, "direction") == "export":
            out.append(f'{fac_node} -.->|funds production, before shipment| {ship_node}')
            if settlement_node:
                out.append(f'{settlement_node} -->|repays| {fac_node}')
        elif settlement_node:
            out.append(f'{fac_node} -->|funds payment| {settlement_node}')

    for _, e in journey["eefc"].iterrows():
        eefc_node = f'{px}eefc{str(e["eefc_txn_id"]).split("-")[-1]}'
        ccy = str(e["eefc_account_id"]).split("-")[-1]
        out.append(_node(eefc_node, [
            f'\U0001F3DB {e["eefc_txn_id"]}', e["eefc_account_id"],
            str(e["txn_type"]).replace("_", " "),
            _money(abs(e["amount"]), ccy),
            f'Balance after: {e["running_balance"]:,.2f}',
        ]))
        if settlement_node:
            verb = "retained in" if e["txn_type"] == "retention_credit" else "drawn from"
            out.append(f'{settlement_node} -->|{verb}| {eefc_node}')

    # FX hedge(s). Usually just one, but a rolled-over hedge produces a
    # second fx_bookings row for the extended contract -- both share this
    # shipment's linked_exposure_id, so handle N rows, not just one, and
    # chain them together via the fx_rollovers table rather than drawing a
    # separate "hedged by" edge from the shipment for the follow-on deal.
    rolled_from = {r["new_fx_deal_id"]: r for _, r in journey["rollovers"].iterrows()}
    fx_node_by_deal_id = {}
    for _, fx in journey["fx"].iterrows():
        deal_id = fx["fx_deal_id"]
        fx_node = f'{px}fx{str(deal_id).split("-")[-1]}'
        fx_node_by_deal_id[deal_id] = fx_node
        out.append(_node(fx_node, [
            f'\U0001F4B1 {deal_id}', f'{_val(fx, "deal_type")} | {_val(fx, "status")}',
            f'{_val(fx, "sell_currency")} → {_val(fx, "buy_currency")}',
            f'Booked rate: {_val(fx, "booked_rate")}',
            f'Deal {_date(fx, "deal_date")} → Value {_date(fx, "value_date")}',
        ]))
        if deal_id not in rolled_from:
            # Only the original (root) hedge gets a direct edge from the
            # shipment -- a follow-on deal from a rollover is connected via
            # the rollover node instead, drawn below.
            out.append(f'{ship_node} -.->|hedged by| {fx_node}')

        mtm_rows = journey["mtm"][journey["mtm"]["fx_deal_id"] == deal_id]
        if not mtm_rows.empty:
            m = mtm_rows.iloc[0]
            mtm_node = f'{px}mtm{str(deal_id).split("-")[-1]}'
            gain_loss = m["mtm_gain_loss_inr"]
            sign = "+" if gain_loss >= 0 else ""
            out.append(_node(mtm_node, [
                f'\U0001F4CA MTM as of {_date(m, "valuation_date")}',
                f'Market rate: {_val(m, "market_rate")}',
                f'{sign}{gain_loss:,.2f} INR',
            ]))
            out.append(f'{fx_node} --> {mtm_node}')

    # Rollover events: original hedge -> rollover node (swap points / bank
    # fee / net P&L) -> the new, extended hedge.
    for _, r in journey["rollovers"].iterrows():
        roll_node = f'{px}roll{str(r["rollover_id"]).split("-")[-1]}'
        net = r["net_pnl_inr"]
        sign = "+" if net >= 0 else ""
        out.append(_node(roll_node, [
            f'\U0001F504 {r["rollover_id"]}',
            f'{_date(r, "old_value_date")} → {_date(r, "new_value_date")}',
            f'Swap points: {"+" if r["swap_points_pnl_inr"] >= 0 else ""}{r["swap_points_pnl_inr"]:,.2f} INR',
            f'Bank fee: -{r["bank_fee_inr"]:,.2f} INR',
            f'Net: {sign}{net:,.2f} INR',
        ]))
        orig_node = fx_node_by_deal_id.get(r["original_fx_deal_id"])
        new_node = fx_node_by_deal_id.get(r["new_fx_deal_id"])
        if orig_node:
            out.append(f'{orig_node} -->|rolled over| {roll_node}')
        if new_node:
            out.append(f'{roll_node} --> {new_node}')

    body = "\n    ".join(out)
    return f'subgraph SG{block_index} ["{_esc(sid)}"]\n    {body}\n  end'


def mermaid_for_shipments(con: duckdb.DuckDBPyConnection, shipment_ids: list):
    """Builds one combined flowchart covering all given shipment IDs (each
    as its own subgraph). Returns (mermaid_text, shipments_found,
    shipments_not_found)."""
    lines = ["flowchart LR"]
    found, missing = [], []
    for i, sid in enumerate(shipment_ids):
        journey = get_shipment_journey(con, sid)
        if journey is None:
            missing.append(sid)
            continue
        lines.append("  " + shipment_mermaid_block(journey, i))
        found.append(sid)
    return "\n".join(lines), found, missing


def shipment_ids_for_counterparty(con: duckdb.DuckDBPyConnection, counterparty_name: str) -> list:
    df = _fetch_df(
        con,
        "SELECT s.shipment_id FROM shipments s "
        "JOIN counterparties c ON c.counterparty_id = s.counterparty_id "
        "WHERE c.name = ? ORDER BY s.shipment_id",
        [counterparty_name],
    )
    return df["shipment_id"].tolist()


def render_mermaid_html(mermaid_definition: str, height: int = 500) -> str:
    """Wraps a Mermaid diagram definition in a minimal self-contained HTML
    page for st.components.v1.html. Mermaid itself loads from a CDN and
    renders client-side in the browser -- no new Python package or system
    install needed for this feature."""
    return f"""
<div class="mermaid">
{mermaid_definition}
</div>
<script src="{MERMAID_CDN_URL}"></script>
<script>
  mermaid.initialize({{ startOnLoad: true, theme: 'default', securityLevel: 'loose',
                         flowchart: {{ useMaxWidth: false, htmlLabels: true }} }});
</script>
<style>
  body {{ margin: 0; }}
  .mermaid {{ overflow-x: auto; }}
</style>
"""

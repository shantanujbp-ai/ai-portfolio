"""
Treasury & Trade POC -- a chat window over the synthetic dataset.

Phase 1: text/table answers only -- no data-driven charts yet (e.g. FX
exposure by currency, pendency aging). One exception, added later: a
pictorial "journey" diagram for one or more shipments (shipment -> LC ->
remittance/payment -> financing/EEFC -> FX hedge -> MTM), via
scripts/journey.py. That diagram is built from direct Python/DuckDB queries,
not Claude-generated SQL, so it's reliable regardless of NL-to-SQL quality.

Same proven pattern as AI-Agent-Project's app.py: a thin UI layer that reuses
scripts/query_data.py's (and scripts/journey.py's) logic rather than
duplicating it. Voice input/output (scripts/voice.py, ported from
AI-Agent-Project's Phase 8) follows the same pattern -- a mic widget below
the chat box transcribes to text offline, and answers can be read back
aloud, both independent of the SQL/summarize logic above.

Run it with:
    streamlit run app.py

Make sure you've generated the synthetic data at least once first:
    python scripts/generate_synthetic_data.py
"""

import sys
import hashlib
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import streamlit as st
import streamlit.components.v1 as components
import anthropic
import query_data
import journey
import charts
import counterparties as cp
import conversion
import pendency_review
import document_extraction as doc_extraction
import voice

st.set_page_config(page_title="Treasury & Trade POC", page_icon="📄", layout="centered")

SAMPLE_DOCS_DIR = Path(__file__).resolve().parent / "sample_documents"


@st.cache_resource(show_spinner="Loading the synthetic data warehouse...")
def get_warehouse():
    con = query_data.build_warehouse()
    # Create the newer feature tables (not CSV-backed) before describe_schema
    # runs, so they're visible to the chat's NL-to-SQL schema context and the
    # sidebar's "What this covers" list from the very first page load, rather
    # than only after the relevant feature happens to be touched once.
    doc_extraction.ensure_schema(con)
    schema = query_data.describe_schema(con)
    return con, schema


@st.cache_resource(show_spinner=False)
def get_client():
    return anthropic.Anthropic(api_key=query_data.ANTHROPIC_API_KEY)


@st.cache_resource(show_spinner="Loading the speech-to-text model (first load only)...")
def get_whisper_model():
    # Loaded once and cached for the life of this app process, same
    # reasoning as get_warehouse() above -- first run ever downloads it
    # (~140MB, needs internet); after that it's cached locally.
    return voice.load_whisper_model()


def _render_html(html: str, height: int):
    # st.iframe is the current API for embedding raw HTML; st.components.v1.html
    # is its predecessor, deprecated (removed in some newer Streamlit releases).
    # Since this project only pins a lower bound on streamlit (see
    # requirements.txt) and different installs may land on either side of that
    # change, try the current API first and fall back rather than assuming.
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:
        components.html(html, height=height, scrolling=True)


def render_journey_diagram(shipment_ids, label):
    mermaid_text, found, missing = journey.mermaid_for_shipments(con, shipment_ids)
    if missing:
        st.caption(f"No data found for: {', '.join(missing)}")
    if not found:
        st.info("No shipments to diagram.")
        return
    height = min(1400, 380 + 230 * len(found))
    _render_html(journey.render_mermaid_html(mermaid_text, height=height), height=height)


def render_dashboard_charts():
    html = charts.render_dashboard_html(con)
    _render_html(html, height=charts.estimate_height(None))


def render_chart_group(group, label=None):
    html = charts.render_group_html(con, group)
    if not html:
        st.info("No chart available for that.")
        return
    _render_html(html, height=charts.estimate_height(group))


# ---------------------------------------------------------------------------
# FX Conversion Facility (HLRD FR-6) -- an inline "Convert" action wherever
# the user is shown a foreign-currency balance/position they hold. Native
# Streamlit widgets, not part of the chart's own HTML/SVG (that content is a
# static render inside an iframe -- see _render_html -- so it can't host an
# interactive control itself). Exposed both from the sidebar Dashboard and
# the in-chat chart view (FR-6.7), each call site passing a distinct
# key_prefix so the same widgets can appear more than once on a page without
# a Streamlit duplicate-key error.
# ---------------------------------------------------------------------------
def render_conversion_panel(scope: str, key_prefix: str):
    show_eefc = scope in ("dashboard", "treasury_extras")
    show_fx = scope in ("dashboard", "fx_exposure")
    if not (show_eefc or show_fx):
        return
    supported = conversion.supported_currencies(con)

    if show_eefc:
        with st.expander("💱 Convert an EEFC balance"):
            balances = [b for b in conversion.eefc_balances(con) if b["available"] > 0.005]
            if not balances:
                st.caption("No EEFC balance currently available to convert.")
            else:
                ccy_options = [b["currency"] for b in balances]
                sel_ccy = st.selectbox("From (EEFC balance)", ccy_options, key=f"{key_prefix}_eefc_ccy")
                avail = next(b["available"] for b in balances if b["currency"] == sel_ccy)
                st.caption(f"Available: {avail:,.2f} {sel_ccy}")
                amount = st.number_input(
                    "Amount to convert", min_value=0.01, max_value=float(avail), value=float(avail),
                    step=round(max(0.01, avail / 100), 2), key=f"{key_prefix}_eefc_amt",
                )
                target = st.selectbox(
                    "To", [c for c in supported if c != sel_ccy], key=f"{key_prefix}_eefc_target",
                )
                rate = conversion.get_rate(con, sel_ccy, target)
                st.markdown(
                    f"**Preview:** {amount:,.2f} {sel_ccy} → **{amount * rate:,.2f} {target}**  "
                    f"·  rate used: 1 {sel_ccy} = {rate:,.4f} {target} (from `fx_rate_history`)"
                )
                if st.button("Confirm conversion", key=f"{key_prefix}_eefc_confirm"):
                    result = conversion.record_conversion(
                        con, source_type="eefc_balance", source_ref=f"EEFC-{sel_ccy}",
                        source_currency=sel_ccy, source_amount=amount, target_currency=target,
                        note="Converted via EEFC balance panel",
                    )
                    st.success(
                        f"{result['conversion_id']}: converted {amount:,.2f} {sel_ccy} → "
                        f"{result['target_amount']:,.2f} {target}. The EEFC balance now reflects this."
                    )
                    st.rerun()

    if show_fx:
        with st.expander("💱 Convert FX exposure to another currency (value lookup)"):
            positions = [p for p in conversion.fx_exposure_positions(con) if p["available"] > 0.005]
            if not positions:
                st.caption("No open FX exposure currently.")
            else:
                ccy_options = [p["currency"] for p in positions]
                sel_ccy = st.selectbox("Open exposure in", ccy_options, key=f"{key_prefix}_fx_ccy")
                avail = next(p["available"] for p in positions if p["currency"] == sel_ccy)
                st.caption(f"Open notional: {avail:,.2f} {sel_ccy}")
                amount = st.number_input(
                    "Amount to view converted", min_value=0.01, max_value=float(avail), value=float(avail),
                    step=round(max(0.01, avail / 100), 2), key=f"{key_prefix}_fx_amt",
                )
                target = st.selectbox(
                    "To", [c for c in supported if c != sel_ccy], key=f"{key_prefix}_fx_target",
                )
                rate = conversion.get_rate(con, sel_ccy, target)
                st.markdown(
                    f"**Preview:** {amount:,.2f} {sel_ccy} → **{amount * rate:,.2f} {target}**  "
                    f"·  rate used: 1 {sel_ccy} = {rate:,.4f} {target} (from `fx_rate_history`)"
                )
                st.caption(
                    "This converts the *view* of the position only -- it does not unwind or "
                    "reduce the open hedge itself, which is a separate treasury action."
                )
                if st.button("Record this lookup", key=f"{key_prefix}_fx_confirm"):
                    result = conversion.record_conversion(
                        con, source_type="fx_exposure", source_ref=sel_ccy,
                        source_currency=sel_ccy, source_amount=amount, target_currency=target,
                        note="Value lookup via FX exposure panel",
                    )
                    st.success(
                        f"{result['conversion_id']}: {amount:,.2f} {sel_ccy} = "
                        f"{result['target_amount']:,.2f} {target} — recorded for reference."
                    )
                    st.rerun()


def _dataframe(df):
    # st.dataframe's `width="stretch"` is the current API replacing the
    # deprecated `use_container_width` (same "don't assume a specific
    # installed version" reasoning as _render_html's st.iframe fallback).
    try:
        st.dataframe(df, width="stretch", hide_index=True)
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True)


def _link_button(label, key):
    # type="tertiary" renders as a plain link-styled button (no box, just
    # colored text) in current Streamlit -- older installs (this project
    # only pins a lower bound, see requirements.txt) may not recognize the
    # value, so fall back to an ordinary button rather than assuming.
    try:
        return st.button(label, key=key, type="tertiary")
    except Exception:
        return st.button(label, key=key)


TABLE_LABELS = {
    "bank_guarantees": "Bank Guarantees",
    "counterparties": "Counterparties",
    "eefc_accounts": "EEFC Accounts",
    "eefc_transactions": "EEFC Transactions",
    "fund_utilization": "Fund Utilization",
    "fx_bookings": "FX Bookings",
    "fx_conversions": "FX Conversions",
    "fx_rate_history": "FX Rate History",
    "fx_rollovers": "FX Rollovers",
    "letters_of_credit": "Letters of Credit",
    "mtm_report": "MTM Report",
    "payments": "Payments",
    "pendencies": "Pendencies",
    "pendency_audit_log": "Pendency Audit Log",
    "remittances": "Remittances",
    "shipments": "Shipments",
    "trade_finance_facilities": "Trade Finance Facilities",
    "uploaded_letters_of_credit": "Uploaded Letters of Credit",
    "uploaded_bank_guarantees": "Uploaded Bank Guarantees",
}


def render_table_schema(table_name):
    row_count = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    st.caption(f"{row_count} rows")
    cols_df = con.execute(f'DESCRIBE "{table_name}"').fetchdf()[["column_name", "column_type"]]
    cols_df.columns = ["Column", "Type"]
    _dataframe(cols_df)


def render_counterparties_view(view, detail_name=None):
    if detail_name:
        df = cp.get_shipment_details(con, view, detail_name)
        if df.empty:
            st.info(f"No shipments found for {detail_name}.")
            return
        _dataframe(df)
        return
    df = cp.get_summary(con, view)
    if df.empty:
        st.info("No data for that view.")
        return
    _dataframe(df)


# ---------------------------------------------------------------------------
# Pendency Review Queue + Audit Log (HLRD-style human sign-off, ported from
# Fair Lend POC's decisioning pattern -- see scripts/pendency_review.py).
# ---------------------------------------------------------------------------
_SEVERITY_BADGE = {"high": "🔴", "medium": "🟠", "low": "🟡"}


def render_pendency_queue():
    counts = pendency_review.summary_counts(con)
    st.caption(f"{counts['open']} open of {counts['total']} total -- most severe first.")
    queue = pendency_review.open_queue(con)
    if not queue:
        st.success("Nothing open -- every pendency has a recorded decision.")
        return

    for item in queue:
        badge = _SEVERITY_BADGE.get(item["severity"], "⚪")
        cat_label = pendency_review.CATEGORY_LABELS.get(item["category"], item["category"])
        with st.expander(f"{badge} {item['pendency_id']} — {cat_label} — {item['description']}"):
            ctx = item["context"]
            if ctx:
                for k, v in ctx.items():
                    if k == "id":
                        continue
                    st.markdown(f"**{k.replace('_', ' ').title()}:** {v}")
            st.markdown("---")
            with st.form(key=f"pnd_form_{item['pendency_id']}"):
                reviewer = st.text_input("Reviewer name", key=f"pnd_reviewer_{item['pendency_id']}")
                action = st.radio(
                    "Decision", pendency_review.ACTIONS[item["category"]],
                    key=f"pnd_action_{item['pendency_id']}",
                )
                note = st.text_area("Note (optional)", key=f"pnd_note_{item['pendency_id']}")
                submitted = st.form_submit_button("Record decision")
                if submitted:
                    if not reviewer.strip():
                        st.error("A reviewer name is required.")
                    else:
                        log_id = pendency_review.record_decision(
                            con, pendency_id=item["pendency_id"], category=item["category"],
                            reference_id=item["reference_id"], reviewer_name=reviewer,
                            action_taken=action, note=note,
                        )
                        st.success(f"Recorded {log_id}: {action}, by {reviewer.strip()}.")
                        st.rerun()


def render_pendency_audit_log():
    st.caption(
        "Every recorded pendency decision, append-only -- who decided what, and when. "
        "Nothing here can be edited or deleted."
    )
    df = pendency_review.audit_trail(con)
    if df.empty:
        st.info("No decisions recorded yet.")
        return
    _dataframe(df)


# ---------------------------------------------------------------------------
# Document upload/extraction for LC and BG documents. Claude proposes field
# values from the document text (scripts/document_extraction.py) and is
# explicitly told to leave a field null rather than guess -- those come back
# flagged in the review form below, and record_extraction() (the only write
# path, plain and deterministic) refuses to save until every required field
# is actually filled in and a named reviewer is attached. The LLM never
# writes to the database here either: it only ever populates a form a human
# still has to review and confirm.
# ---------------------------------------------------------------------------
def _sample_doc_options():
    if not SAMPLE_DOCS_DIR.exists():
        return []
    return sorted(p.name for p in SAMPLE_DOCS_DIR.glob("*.txt"))


def render_document_upload():
    st.caption(
        "Upload a plain-text LC or BG issuance document and Claude extracts the "
        "structured fields. Anything not actually stated in the document is left "
        "blank and flagged below for you to fill in -- never guessed."
    )
    doc_type = st.radio("Document type", list(doc_extraction.DOC_TYPES), key="doc_type_pick", horizontal=True)

    source_label, raw_text = None, None
    uploaded = st.file_uploader("Upload a .txt document", type=["txt"], key="doc_uploader")
    if uploaded is not None:
        raw_text = uploaded.getvalue().decode("utf-8", errors="replace")
        source_label = uploaded.name
    else:
        sample_options = ["(none)"] + _sample_doc_options()
        picked_sample = st.selectbox(
            "...or try a bundled sample document", sample_options, key="doc_sample_pick"
        )
        if picked_sample != "(none)":
            raw_text = (SAMPLE_DOCS_DIR / picked_sample).read_text()
            source_label = picked_sample

    if raw_text:
        with st.expander(f"Document text — {source_label}"):
            st.text(raw_text)
        if st.button("Extract fields", key="doc_extract_btn"):
            with st.spinner("Extracting fields with Claude..."):
                try:
                    extracted = doc_extraction.extract_fields(client, doc_type, raw_text)
                    missing = doc_extraction.missing_required_fields(doc_type, extracted)
                    st.session_state.doc_extraction_result = {
                        "doc_type": doc_type, "source_filename": source_label,
                        "fields": extracted, "missing": missing,
                    }
                except Exception as e:
                    st.session_state.doc_extraction_result = None
                    st.error(f"Extraction failed: {e}")
            st.rerun()

    extraction = st.session_state.get("doc_extraction_result")
    if extraction and extraction["doc_type"] == doc_type and extraction["source_filename"] == source_label:
        st.markdown("---")
        st.markdown(f"**Review extracted fields** — from `{extraction['source_filename']}`")
        if extraction["missing"]:
            st.warning(
                "⚠️ Not found in the document -- please verify/enter manually: "
                + ", ".join(doc_extraction.FIELD_LABELS.get(f, f) for f in extraction["missing"])
            )
        else:
            st.success("All required fields were found in the document. Review before saving.")

        fields = extraction["fields"]
        all_field_names = doc_extraction.FIELD_SPECS[doc_type]["all"]
        with st.form(key=f"doc_review_form_{doc_type}"):
            raw_inputs = {}
            for f in all_field_names:
                label = doc_extraction.FIELD_LABELS.get(f, f)
                flagged = f in extraction["missing"]
                display_label = f"⚠️ {label} (missing -- please fill in)" if flagged else label
                value = fields.get(f)
                raw_inputs[f] = st.text_input(
                    display_label, value="" if value is None else str(value),
                    key=f"doc_field_{doc_type}_{f}",
                )
            reviewer = st.text_input("Reviewer name", key=f"doc_reviewer_{doc_type}")
            submitted = st.form_submit_button("Confirm & save")
            if submitted:
                coerced, coercion_errors = doc_extraction.coerce_form_fields(doc_type, raw_inputs)
                still_missing = doc_extraction.missing_required_fields(doc_type, coerced)
                if coercion_errors:
                    for err in coercion_errors:
                        st.error(err)
                elif still_missing:
                    st.error(
                        "Required fields are still blank: "
                        + ", ".join(doc_extraction.FIELD_LABELS.get(f, f) for f in still_missing)
                    )
                elif not reviewer.strip():
                    st.error("A named reviewer is required to save this document.")
                else:
                    doc_id = doc_extraction.record_extraction(
                        con, doc_type=doc_type, fields=coerced,
                        source_filename=extraction["source_filename"], reviewer_name=reviewer,
                        fields_flagged_missing=extraction["missing"],
                    )
                    st.success(f"Saved {doc_id}, reviewed by {reviewer.strip()}.")
                    st.session_state.doc_extraction_result = None
                    st.rerun()


def render_uploaded_documents_list():
    st.caption(
        "Every document that has been extracted and confirmed by a named reviewer -- "
        "kept separate from the synthetic letters_of_credit/bank_guarantees tables "
        "so a warehouse rebuild can never lose one."
    )
    df = doc_extraction.list_uploads(con)
    if df.empty:
        st.info("No documents uploaded yet.")
        return
    _dataframe(df)


if not query_data.ANTHROPIC_API_KEY:
    st.error(
        "No ANTHROPIC_API_KEY found. Copy .env.example to .env in this project folder, "
        "add your real key, then restart this app."
    )
    st.stop()

client = get_client()
con, schema = get_warehouse()

if not schema:
    st.error(
        "No synthetic data found yet. Run `python scripts/generate_synthetic_data.py` "
        "in a terminal, then restart this app."
    )
    st.stop()

st.title("📄 Treasury & Trade POC")
st.caption(
    "Ask about shipments, LCs, bank guarantees, remittances, payments, FX/MTM, and how "
    "proceeds/payments are funded (EEFC retention, packing credit, import financing) in "
    "plain English. Ask for a shipment's 'journey' (or say 'diagram'/'visualize') to get "
    "a pictorial flow alongside the answer, or ask for a 'chart' of FX exposure, LC "
    "utilization, pendency aging, or EEFC/rollover P&L -- or open the Dashboard in the "
    "sidebar to see all of them at once. **All data below is synthetic and fictional "
    "-- generated for this proof of concept, not a real company's real financial data.**"
)

if "diagram_shipment_ids" not in st.session_state:
    st.session_state.diagram_shipment_ids = None
    st.session_state.diagram_label = None
if "show_dashboard" not in st.session_state:
    st.session_state.show_dashboard = False
if "counterparties_view" not in st.session_state:
    st.session_state.counterparties_view = None
    st.session_state.counterparties_detail_name = None
if "schema_table_view" not in st.session_state:
    st.session_state.schema_table_view = None
if "pendency_queue_view" not in st.session_state:
    st.session_state.pendency_queue_view = False
if "pendency_audit_view" not in st.session_state:
    st.session_state.pendency_audit_view = False
if "doc_upload_view" not in st.session_state:
    st.session_state.doc_upload_view = False
if "doc_upload_list_view" not in st.session_state:
    st.session_state.doc_upload_list_view = False
if "audio_widget_key" not in st.session_state:
    st.session_state.audio_widget_key = 0
if "speak_answers" not in st.session_state:
    st.session_state.speak_answers = True  # on by default -- that's the whole point of voice
if "last_audio_hash" not in st.session_state:
    st.session_state.last_audio_hash = None


def _clear_all_views():
    # Every sidebar reference view is mutually exclusive with every other
    # one -- centralized here so adding a new view can't forget to clear
    # an older one (the failure mode of repeating this list at each button).
    st.session_state.show_dashboard = False
    st.session_state.counterparties_view = None
    st.session_state.counterparties_detail_name = None
    st.session_state.diagram_shipment_ids = None
    st.session_state.diagram_label = None
    st.session_state.schema_table_view = None
    st.session_state.pendency_queue_view = False
    st.session_state.pendency_audit_view = False
    st.session_state.doc_upload_view = False
    st.session_state.doc_upload_list_view = False


with st.sidebar:
    st.subheader("📊 Dashboard")
    st.caption("FX exposure, LC utilization, pendency aging, EEFC balances, and FX rollover P&L.")
    if st.button("Show dashboard charts", use_container_width=True):
        _clear_all_views()
        st.session_state.show_dashboard = True
        st.rerun()
    if st.session_state.show_dashboard and st.button("Clear dashboard", use_container_width=True):
        st.session_state.show_dashboard = False
        st.rerun()
    st.divider()

    st.subheader("🤝 Counterparties")
    st.caption("Who Meridian actually trades with -- exporters (buyers), importers (suppliers), and banks.")
    cp_view = st.radio(
        "View", list(cp.VIEWS), format_func=lambda v: cp.VIEW_LABELS[v], key="cp_view_radio",
    )
    ALL_SENTINEL = "(all -- summary table)"
    picked_detail_name = None
    if cp_view in ("exporters", "importers"):
        detail_options = [ALL_SENTINEL] + cp.counterparty_names(con, cp_view)
        picked_detail_name = st.selectbox(
            "Drill into one (optional)", detail_options, key="cp_detail_pick",
        )
        if picked_detail_name == ALL_SENTINEL:
            picked_detail_name = None
    if st.button("Show counterparties table", use_container_width=True):
        _clear_all_views()
        st.session_state.counterparties_view = cp_view
        st.session_state.counterparties_detail_name = picked_detail_name
        st.rerun()
    if st.session_state.counterparties_view and st.button("Clear counterparties table", use_container_width=True):
        st.session_state.counterparties_view = None
        st.session_state.counterparties_detail_name = None
        st.rerun()
    st.divider()

    st.subheader("🗺️ Journey diagram")
    cp_names = [r[0] for r in con.execute(
        "SELECT DISTINCT name FROM counterparties WHERE type = 'trade_counterparty' ORDER BY name"
    ).fetchall()]
    picked_cp = st.selectbox("Counterparty", ["(choose one)"] + cp_names, key="journey_cp")
    if picked_cp != "(choose one)":
        cp_shipment_ids = journey.shipment_ids_for_counterparty(con, picked_cp)
        scope = st.radio(
            "Scope", ["All shipments for this counterparty", "One specific shipment"],
            key="journey_scope",
        )
        if scope == "One specific shipment":
            picked_shipment = st.selectbox("Shipment", cp_shipment_ids, key="journey_shipment")
            target_ids, target_label = [picked_shipment], f"{picked_cp} — {picked_shipment}"
        else:
            target_ids = cp_shipment_ids
            target_label = f"{picked_cp} — all {len(cp_shipment_ids)} shipments"

        if st.button("Show journey diagram", use_container_width=True):
            _clear_all_views()
            st.session_state.diagram_shipment_ids = target_ids
            st.session_state.diagram_label = target_label
            st.rerun()

    if st.session_state.diagram_shipment_ids and st.button("Clear diagram", use_container_width=True):
        st.session_state.diagram_shipment_ids = None
        st.session_state.diagram_label = None
        st.rerun()
    st.divider()

    st.subheader("📋 Pendency Review")
    pend_counts = pendency_review.summary_counts(con)
    st.caption(
        f"{pend_counts['open']} open of {pend_counts['total']} pendencies -- LC discrepancies, "
        "overdue remittances, BGs expiring soon. Nothing is resolved until a named reviewer "
        "records a decision."
    )
    if st.button("Show review queue", use_container_width=True):
        _clear_all_views()
        st.session_state.pendency_queue_view = True
        st.rerun()
    if st.button("Show audit log", use_container_width=True):
        _clear_all_views()
        st.session_state.pendency_audit_view = True
        st.rerun()
    if (st.session_state.pendency_queue_view or st.session_state.pendency_audit_view) and st.button(
        "Clear pendency view", use_container_width=True
    ):
        st.session_state.pendency_queue_view = False
        st.session_state.pendency_audit_view = False
        st.rerun()
    st.divider()

    st.subheader("📄 Document Upload")
    st.caption(
        "Upload an LC or BG document (or try a bundled sample) and have Claude "
        "extract the structured fields -- anything not found in the text is "
        "flagged for you to fill in, never guessed."
    )
    if st.button("Show document upload", use_container_width=True):
        _clear_all_views()
        st.session_state.doc_upload_view = True
        st.rerun()
    if st.button("Show uploaded documents", use_container_width=True):
        _clear_all_views()
        st.session_state.doc_upload_list_view = True
        st.rerun()
    if (st.session_state.doc_upload_view or st.session_state.doc_upload_list_view) and st.button(
        "Clear document view", use_container_width=True
    ):
        st.session_state.doc_upload_view = False
        st.session_state.doc_upload_list_view = False
        st.rerun()
    st.divider()

    st.subheader("🎙️ Voice")
    st.session_state.speak_answers = st.checkbox(
        "🔊 Read answers aloud", value=st.session_state.speak_answers
    )
    st.caption(
        "Ask by voice using the microphone below the chat box. Both directions "
        "are fully offline -- your recording never reaches the Claude API, only "
        "the transcribed text does."
    )
    st.divider()

    if st.button("Clear conversation", use_container_width=True):
        st.session_state.display_messages = []
        st.session_state.last_audio_hash = None
        st.session_state.audio_widget_key += 1  # forces a fresh, empty mic widget
        st.rerun()

    st.divider()
    with st.expander("What this covers"):
        table_names = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        for t in table_names:
            if _link_button(TABLE_LABELS.get(t, t), key=f"schema_link_{t}"):
                _clear_all_views()
                st.session_state.schema_table_view = t
                st.rerun()
        if st.session_state.schema_table_view and st.button("Clear table view", use_container_width=True):
            st.session_state.schema_table_view = None
            st.rerun()

if st.session_state.schema_table_view:
    t = st.session_state.schema_table_view
    st.subheader(f"📋 {TABLE_LABELS.get(t, t)}")
    render_table_schema(t)
    st.divider()

if st.session_state.show_dashboard:
    st.subheader("📊 Dashboard")
    render_dashboard_charts()
    render_conversion_panel("dashboard", "dash")
    st.divider()

if st.session_state.counterparties_view:
    detail_name = st.session_state.counterparties_detail_name
    heading = detail_name or cp.VIEW_LABELS[st.session_state.counterparties_view]
    st.subheader(f"🤝 {heading}")
    render_counterparties_view(st.session_state.counterparties_view, detail_name)
    st.divider()

if st.session_state.diagram_shipment_ids:
    st.subheader(f"Journey diagram — {st.session_state.diagram_label}")
    render_journey_diagram(st.session_state.diagram_shipment_ids, st.session_state.diagram_label)
    st.divider()

if st.session_state.pendency_queue_view:
    st.subheader("📋 Pendency Review Queue")
    render_pendency_queue()
    st.divider()

if st.session_state.pendency_audit_view:
    st.subheader("🧾 Pendency Audit Log")
    render_pendency_audit_log()
    st.divider()

if st.session_state.doc_upload_view:
    st.subheader("📄 Upload & Extract LC/BG Document")
    render_document_upload()
    st.divider()

if st.session_state.doc_upload_list_view:
    st.subheader("📄 Uploaded Documents")
    render_uploaded_documents_list()
    st.divider()

if "display_messages" not in st.session_state:
    st.session_state.display_messages = []

for msg_idx, msg in enumerate(st.session_state.display_messages):
    with st.chat_message(msg["role"]):
        if msg.get("sql"):
            with st.expander("Generated SQL"):
                st.code(msg["sql"], language="sql")
        st.markdown(msg["content"])
        if msg.get("diagram_shipment_ids"):
            render_journey_diagram(msg["diagram_shipment_ids"], "this answer")
        if msg.get("chart_group"):
            render_chart_group(msg["chart_group"])
            if msg["chart_group"] in ("treasury_extras", "fx_exposure"):
                render_conversion_panel(msg["chart_group"], f"hist{msg_idx}")
        if msg.get("audio_bytes"):
            # No autoplay here -- this just lets you replay an earlier
            # answer on demand, not re-announcing the whole conversation
            # history every time the app reruns.
            st.audio(msg["audio_bytes"], format="audio/wav")

typed_question = st.chat_input(
    "Ask about shipments, LCs, BGs, remittances, payments, FX/MTM, or a journey..."
)
audio_value = st.audio_input(
    "🎤 Or ask by voice", key=f"mic_{st.session_state.audio_widget_key}"
)

question = None
if typed_question:
    question = typed_question
elif audio_value is not None:
    # Streamlit reruns this whole script on every interaction, and the mic
    # widget keeps returning the same recording until you record a new one
    # or the widget is reset -- so without this dedup check, the same
    # question would get re-asked on every unrelated rerun (e.g. clicking
    # something else in the sidebar).
    audio_bytes_in = audio_value.getvalue()
    audio_hash = hashlib.sha256(audio_bytes_in).hexdigest()
    if audio_hash != st.session_state.last_audio_hash:
        st.session_state.last_audio_hash = audio_hash
        with st.spinner("Transcribing..."):
            try:
                whisper_model = get_whisper_model()
                question = voice.transcribe_audio(whisper_model, audio_bytes_in)
            except Exception as e:
                st.error(f"Couldn't transcribe that recording: {e}")
        if question is not None and not question.strip():
            st.warning("Didn't catch any speech in that recording -- try again.")
            question = None

if question:
    with st.chat_message("user"):
        st.markdown(question)
    st.session_state.display_messages.append({"role": "user", "content": question})

    # Like the journey diagram, a chat-triggered chart is built directly from
    # the data (scripts/charts.py), not from Claude-generated SQL -- so it's
    # keyed off the question's own wording, independent of whatever the SQL
    # answer below turns out to be.
    chart_group = charts.question_wants_chart(question)

    with st.chat_message("assistant"):
        sql = None
        diagram_shipment_ids = None
        with st.spinner("Thinking..."):
            try:
                sql = query_data.ask_claude_for_sql(client, question, schema)
                query_data.validate_sql(sql)
                result = con.execute(sql)
                rows = result.fetchall()
                columns = [d[0] for d in result.description]
                answer = query_data.summarize_answer(client, question, sql, rows, columns)

                # A pictorial journey diagram is built directly from the
                # data (scripts/journey.py), not from this SQL result --
                # only the list of shipment_id values found is taken from
                # the answer above, so a diagram is never wrong even if the
                # chat's own SQL happened to be imperfect for the question.
                if "shipment_id" in columns and journey.question_wants_diagram(question):
                    idx = columns.index("shipment_id")
                    seen = []
                    for row in rows:
                        sid = row[idx]
                        if sid and sid not in seen:
                            seen.append(sid)
                    if 0 < len(seen) <= journey.MAX_AUTO_DIAGRAM_SHIPMENTS:
                        diagram_shipment_ids = seen
                    elif len(seen) > journey.MAX_AUTO_DIAGRAM_SHIPMENTS:
                        answer += (
                            f"\n\n*(Diagram skipped — {len(seen)} shipments matched, more than "
                            f"the {journey.MAX_AUTO_DIAGRAM_SHIPMENTS}-shipment auto-diagram limit. "
                            "Use the Journey Diagram picker in the sidebar, or narrow the question, "
                            "to see one.)*"
                        )
            except query_data.UnrecognizedQueryError as e:
                # Not a safety block -- the model didn't return a usable
                # query, most often because a named entity in the question
                # (frequently a voice-mis-transcribed name) didn't closely
                # match the data. Friendlier framing than the genuine
                # "blocked unsafe query" case below.
                answer = f"Couldn't answer that: {e}"
            except ValueError as e:
                answer = f"Blocked unsafe query: {e}"
            except Exception as e:
                answer = f"Error answering that question: {e}"

        if sql:
            with st.expander("Generated SQL"):
                st.code(sql, language="sql")
        st.markdown(answer)
        if diagram_shipment_ids:
            render_journey_diagram(diagram_shipment_ids, "this answer")
        if chart_group:
            render_chart_group(chart_group)
            if chart_group in ("treasury_extras", "fx_exposure"):
                render_conversion_panel(chart_group, f"live{len(st.session_state.display_messages)}")

        answer_audio_bytes = None
        if st.session_state.speak_answers:
            with st.spinner("Generating speech..."):
                try:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                        tmp_path = Path(tmp.name)
                    voice.speak_text(answer, tmp_path)
                    answer_audio_bytes = tmp_path.read_bytes()
                    tmp_path.unlink(missing_ok=True)
                except Exception as e:
                    st.warning(f"Couldn't generate speech for this answer: {e}")
            if answer_audio_bytes:
                st.audio(answer_audio_bytes, format="audio/wav", autoplay=True)

    st.session_state.display_messages.append({
        "role": "assistant", "content": answer, "sql": sql,
        "diagram_shipment_ids": diagram_shipment_ids,
        "chart_group": chart_group,
        "audio_bytes": answer_audio_bytes,
    })

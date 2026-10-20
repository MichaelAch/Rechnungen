from __future__ import annotations

import base64
import hashlib
import html

import streamlit as st

from app.chat import handle_message
from app.config import MODEL, load_api_key
from app.corrections import CorrectionStore
from app.documents import demo_pdf, render_page
from app.match import ORIGIN_LABEL
from app.pipeline import analyze
from app.sap import get_sap_client

st.set_page_config(page_title="Lieferantenrechnungen", layout="wide")

ENGINE_LABEL = {
    "regeln": "lokale Regeln",
    "grok": "Grok",
    "korrektur": "manuelle Korrektur",
}


def main() -> None:
    store = CorrectionStore()
    sap = get_sap_client()
    api_key = load_api_key()
    _init_state()
    _sidebar(store, api_key)
    _take_upload()
    analysis = _analysis(sap, store, api_key)

    st.title("Lieferantenrechnungen")
    st.caption("Phase 1. Vorschlag und Begründung. Es wird nichts in SAP gebucht.")
    st.info("SAP ist nicht verbunden. Zuordnung und Ansprechpartner kommen aus einem lesenden Mock.")

    if analysis is not None and analysis.warning:
        st.warning(analysis.warning)

    left, right = st.columns(2, gap="large")
    with left:
        _preview(analysis)
    with right:
        if analysis is None:
            st.subheader("Auswertung")
            st.write("Lade eine Rechnung hoch oder starte links mit einer Demo.")
        else:
            _result(analysis)
            _chat_log()

    if analysis is not None:
        prompt = st.chat_input("Korrektur oder Frage zur Zuordnung")
        if prompt:
            reply, updated = handle_message(prompt, analysis, sap, store, api_key)
            st.session_state["messages"].append({"role": "user", "content": prompt})
            st.session_state["messages"].append({"role": "assistant", "content": reply})
            st.session_state["analysis"] = updated
            st.rerun()


def _init_state() -> None:
    st.session_state.setdefault("messages", [])


def _sidebar(store: CorrectionStore, api_key: str | None) -> None:
    with st.sidebar:
        st.header("Eingang")
        st.file_uploader(
            "PDF oder Scan",
            type=["pdf", "png", "jpg", "jpeg", "tif", "tiff"],
            key="invoice_file",
            label_visibility="collapsed",
        )
        st.caption("Demo-Rechnungen")
        demos = (
            ("demo_po", "Mit Bestellbezug", "po", "Demo-Bestellung.pdf"),
            ("demo_institute", "Ohne Bestellbezug", "institute", "Demo-Institut.pdf"),
            ("demo_history", "Lieferantenhistorie", "history", "Demo-Historie.pdf"),
            ("demo_collective", "Sammelrechnung", "collective", "Demo-Strom.pdf"),
        )
        for key, label, kind, filename in demos:
            if st.button(label, key=key):
                st.session_state["upload_bytes"] = demo_pdf(kind)
                st.session_state["upload_name"] = filename
        st.divider()
        if api_key:
            st.success(f"Grok bereit ({MODEL})")
        else:
            st.caption(f"Grok nicht konfiguriert. Lokale Regeln. Modell später: {MODEL}.")
        st.caption("Korrekturen liegen in data/corrections.db auf diesem Rechner.")
        rows = store.recent()
        if rows:
            st.markdown("**Gespeicherte Korrekturen**")
            for row in rows:
                st.caption(f"{row.supplier_name}: {row.cost_object_id} {row.cost_object_name}")
        else:
            st.caption("Noch keine gespeicherte Korrektur.")


def _take_upload() -> None:
    uploaded = st.session_state.get("invoice_file")
    if uploaded is None:
        return
    data = uploaded.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    if digest == st.session_state.get("widget_digest"):
        return
    st.session_state["widget_digest"] = digest
    st.session_state["upload_bytes"] = data
    st.session_state["upload_name"] = uploaded.name


def _analysis(sap, store, api_key):
    data = st.session_state.get("upload_bytes")
    name = st.session_state.get("upload_name")
    if not data or not name:
        return None
    digest = hashlib.sha256(data).hexdigest()
    if st.session_state.get("digest") == digest and st.session_state.get("analysis") is not None:
        return st.session_state["analysis"]
    try:
        with st.spinner("Rechnung wird gelesen …"):
            result = analyze(data, name, sap, store, api_key)
    except Exception as exc:
        st.error(f"Die Datei konnte nicht gelesen werden. {exc}")
        return None
    st.session_state["analysis"] = result
    st.session_state["digest"] = digest
    st.session_state["messages"] = []
    st.session_state["preview_key"] = None
    return result


def _preview(analysis) -> None:
    st.subheader("Originalrechnung")
    data = st.session_state.get("upload_bytes")
    name = st.session_state.get("upload_name")
    if not data or analysis is None:
        st.write("Noch keine Datei.")
        return
    page_count = analysis.page_count
    page_index = 0
    if page_count > 1:
        page_index = st.number_input(
            "Seite",
            min_value=1,
            max_value=page_count,
            value=1,
            key=f"page-{st.session_state.get('digest', '')[:8]}",
        )
        page_index = int(page_index) - 1
    zoom = st.slider("Zoom in Prozent", min_value=60, max_value=220, value=100, step=10)
    preview_key = f"{st.session_state.get('digest')}:{page_index}"
    if st.session_state.get("preview_key") != preview_key:
        st.session_state["preview_png"] = render_page(data, name, page_index)
        st.session_state["preview_key"] = preview_key
    encoded = base64.standard_b64encode(st.session_state["preview_png"]).decode("ascii")
    st.markdown(
        f"""
        <div style="overflow:auto; height:74vh; border:1px solid #d0d7e2; background:#eef2f6;">
          <img alt="Seite der Rechnung" src="data:image/png;base64,{encoded}"
               style="width:{zoom}%; max-width:none; display:block;" />
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(f"Seite {page_index + 1} von {page_count}. {name}")


def _result(analysis) -> None:
    fields = analysis.fields
    suggestion = analysis.suggestion
    st.subheader("Auswertung")
    if suggestion.is_collective:
        reason = html.escape(suggestion.collective_reason or "Sammelrechnung")
        st.markdown(
            f"""
            <div style="border-left:4px solid #a33b32; background:#fdf4f3; padding:0.8rem 1rem;">
              <strong>Sammelrechnung ({reason})</strong>
              <div>Kein Vorschlag und kein Ansprechpartner. Diese Rechnungen werden in Phase 1 nicht zugeordnet.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        _score(suggestion)
        if suggestion.cost_object_id:
            kind = "Kostenstelle" if suggestion.cost_object_type == "kostenstelle" else "Innenauftrag"
            st.markdown(
                f"""
                <div style="border-left:4px solid #1f4e79; background:#f4f7fb; padding:0.8rem 1rem; margin-top:0.8rem;">
                  <div style="color:#52606d;">Vorgeschlagener Kostenträger</div>
                  <div style="font-size:1.15rem; font-weight:650;">{html.escape(kind)} {html.escape(suggestion.cost_object_id)}</div>
                  <div>{html.escape(suggestion.cost_object_name or "")}</div>
                  <div style="margin-top:0.45rem; color:#52606d;">Ansprechpartner</div>
                  <div style="font-weight:650;">{html.escape(suggestion.contact_name or "—")}</div>
                  <div>{html.escape(suggestion.contact_email or "keine E-Mail")}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    st.markdown("**Begründung**")
    st.write(suggestion.reasoning)
    st.markdown("**Extrahierte Felder**")
    for label, value in _field_rows(fields):
        _field(label, value, label in suggestion.used_fields)
    with st.expander("Erkannter Text"):
        st.text(fields.text or "Kein Text erkannt.")


def _score(suggestion) -> None:
    if suggestion.confidence >= 80:
        color = "#1b7f4a"
    elif suggestion.confidence >= 50:
        color = "#8a5a00"
    else:
        color = "#a33b32"
    origin = ORIGIN_LABEL.get(suggestion.origin, suggestion.origin)
    engine = ENGINE_LABEL.get(suggestion.engine, suggestion.engine)
    st.markdown(
        f"""
        <div style="display:flex; gap:1rem; align-items:center; border:1px solid #d0d7e2; padding:0.75rem 1rem;">
          <div style="font-size:2rem; font-weight:700; color:{color}; min-width:5rem;">{suggestion.confidence} %</div>
          <div>
            <div style="font-weight:650;">{html.escape(origin)}</div>
            <div style="color:#52606d;">Grundlage: {html.escape(engine)}. SAP-Mock, nur lesend.</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _field_rows(fields) -> list[tuple[str, str]]:
    amount = "—"
    if fields.total_amount:
        amount = f"{fields.total_amount} {fields.currency or ''}".strip()
    rows = [
        ("Lieferant", fields.supplier_name),
        ("Rechnungsnummer", fields.invoice_number),
        ("Rechnungsdatum", fields.invoice_date),
        ("Betrag", amount if fields.total_amount else None),
        ("Bestellnummer", fields.po_number),
        ("Kostenstelle im Text", ", ".join(fields.cost_center_hints)),
        ("Innenauftrag im Text", ", ".join(fields.internal_order_hints)),
        ("Institut", ", ".join(fields.institute_hints)),
    ]
    return [(label, value if value else "—") for label, value in rows]


def _field(label: str, value: str, used: bool) -> None:
    border = "3px solid #1f4e79" if used else "3px solid transparent"
    background = "#f4f7fb" if used else "transparent"
    mark = " · verwendet" if used else ""
    st.markdown(
        f"""
        <div style="border-left:{border}; background:{background}; padding:0.15rem 0.6rem; margin-bottom:0.15rem;">
          <span style="color:#52606d;">{html.escape(label)}{mark}</span><br/>
          <span>{html.escape(value)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _chat_log() -> None:
    st.markdown("**Korrektur**")
    messages = st.session_state.get("messages") or []
    if not messages:
        st.caption("Zum Beispiel: Kostenstelle 150340, Ansprechpartner ist Dr. Thomas Maier.")
        return
    for message in messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])


main()

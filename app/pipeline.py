from __future__ import annotations

from app.corrections import CorrectionStore
from app.documents import read_text, render_page
from app.extract import extract_fields, merge_fields
from app.grok_client import GrokError, extract_invoice, match_with_context
from app.models import Analysis, InvoiceFields
from app.match import apply_grok_choice, grok_context, match_invoice
from app.sap import SapClient, supplier_key


def analyze(
    data: bytes,
    filename: str,
    sap: SapClient,
    store: CorrectionStore,
    api_key: str | None = None,
) -> Analysis:
    text, page_count = read_text(data, filename)
    local = extract_fields(text)
    warning = None
    fields = local
    if api_key:
        try:
            images = _page_images(data, filename, page_count) if len(text.strip()) < 80 else None
            remote = _fields_from_grok(extract_invoice(api_key, text, images), text)
            fields = merge_fields(local, remote)
            if images and page_count > 2:
                warning = "An Grok gehen nur die ersten zwei Seiten."
        except GrokError as exc:
            warning = f"Grok hat die Rechnung nicht gelesen. Lokale Regeln werden verwendet. {exc}"
            fields = local
    elif len(text.strip()) < 40:
        warning = (
            "In dieser Datei steht kaum Text. Scans liest Grok, sobald XAI_API_KEY "
            "in der Umgebung oder in .streamlit/secrets.toml steht."
        )

    correction = store.latest_for(supplier_key(fields.supplier_name))
    suggestion = match_invoice(fields, sap, correction)
    if api_key and not suggestion.is_collective:
        try:
            payload = match_with_context(api_key, grok_context(fields, sap, correction))
            suggestion = apply_grok_choice(suggestion, payload, sap, fields)
        except GrokError as exc:
            extra = f"Grok hat nicht zugeordnet. Die Regeln bleiben aktiv. {exc}"
            warning = extra if warning is None else f"{warning} {extra}"
    return Analysis(fields=fields, suggestion=suggestion, warning=warning, page_count=page_count)


def _page_images(data: bytes, filename: str, page_count: int) -> list[bytes]:
    return [render_page(data, filename, index) for index in range(min(page_count, 2))]


def _fields_from_grok(payload: dict, text: str) -> InvoiceFields:
    def as_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def as_text(value) -> str | None:
        if value is None:
            return None
        text_value = str(value).strip()
        return text_value or None

    return InvoiceFields(
        supplier_name=as_text(payload.get("supplier_name")),
        invoice_number=as_text(payload.get("invoice_number")),
        invoice_date=as_text(payload.get("invoice_date")),
        total_amount=as_text(payload.get("total_amount")),
        currency=as_text(payload.get("currency")),
        po_number=as_text(payload.get("po_number")),
        cost_center_hints=as_list(payload.get("cost_center_hints")),
        internal_order_hints=as_list(payload.get("internal_order_hints")),
        institute_hints=as_list(payload.get("institute_hints")),
        is_collective=bool(payload.get("is_collective")),
        collective_reason=as_text(payload.get("collective_reason")),
        text=text,
        extraction_source="grok",
    )

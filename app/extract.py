from __future__ import annotations

import re

from app.models import InvoiceFields

SUPPLIER_RE = re.compile(r"\b(GmbH|KG|AG|OG|e\.U\.)\b")
PO_RE = re.compile(r"Bestell(?:nummer|ung)?\s*[:\-]?\s*(45\d{8})", re.IGNORECASE)
INVOICE_NO_RE = re.compile(r"Rechnungsnummer\s*[:\-]?\s*(\S+)", re.IGNORECASE)
DATE_RE = re.compile(r"Rechnungsdatum\s*[:\-]?\s*(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)
AMOUNT_RE = re.compile(
    r"(?:Gesamtbetrag|Rechnungsbetrag|Endbetrag)\s*(?:EUR|€)?\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
COST_CENTER_RE = re.compile(r"Kostenstelle\s*[:\-]?\s*(\d{4,10})", re.IGNORECASE)
ORDER_RE = re.compile(r"Innenauftrag\s*[:\-]?\s*([A-Za-z]{1,4}-?\d{2,4}-?\d{2,6})", re.IGNORECASE)
INSTITUTE_RE = re.compile(
    r"Institut für [A-ZÄÖÜ][\wÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß\-]+)*"
)

# Title or supplier has to carry the utility. A passing mention is not enough.
COLLECTIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"stromrechnung|stromabschlag|energieabrechnung", "Strom"),
    (r"gasrechnung", "Gas"),
    (r"fernwärme|fernwaerme", "Fernwärme"),
    (r"telefonrechnung|mobilfunkrechnung", "Telefon"),
    (r"internetrechnung", "Internet"),
    (r"versicherungsprämie|versicherungspolice|versicherungsrechnung", "Versicherung"),
)
COLLECTIVE_SUPPLIERS: tuple[tuple[str, str], ...] = (
    ("energie ag", "Strom"),
    ("linz ag", "Strom"),
    ("wien energie", "Strom"),
    ("magenta", "Telefon"),
    ("a1 telekom", "Telefon"),
    ("uniqa", "Versicherung"),
)


def extract_fields(text: str) -> InvoiceFields:
    supplier = _supplier(text)
    reason = _collective_reason(text, supplier)
    return InvoiceFields(
        supplier_name=supplier,
        invoice_number=_first(INVOICE_NO_RE, text),
        invoice_date=_first(DATE_RE, text),
        total_amount=_first(AMOUNT_RE, text),
        currency="EUR" if _first(AMOUNT_RE, text) else None,
        po_number=_first(PO_RE, text),
        cost_center_hints=_unique(COST_CENTER_RE.findall(text)),
        internal_order_hints=_unique(match.group(1) for match in ORDER_RE.finditer(text)),
        institute_hints=_unique(INSTITUTE_RE.findall(text)),
        is_collective=reason is not None,
        collective_reason=reason,
        text=text,
        extraction_source="lokal",
    )


def merge_fields(local: InvoiceFields, remote: InvoiceFields) -> InvoiceFields:
    collective, reason = _combine_collective(local, remote)
    return InvoiceFields(
        supplier_name=remote.supplier_name or local.supplier_name,
        invoice_number=remote.invoice_number or local.invoice_number,
        invoice_date=remote.invoice_date or local.invoice_date,
        total_amount=remote.total_amount or local.total_amount,
        currency=remote.currency or local.currency,
        po_number=remote.po_number or local.po_number,
        cost_center_hints=_unique([*local.cost_center_hints, *remote.cost_center_hints]),
        internal_order_hints=_unique([*local.internal_order_hints, *remote.internal_order_hints]),
        institute_hints=_unique([*local.institute_hints, *remote.institute_hints]),
        is_collective=collective,
        collective_reason=reason,
        text=local.text,
        extraction_source="grok",
    )


def _combine_collective(local: InvoiceFields, remote: InvoiceFields) -> tuple[bool, str | None]:
    if local.is_collective:
        return True, local.collective_reason
    if not remote.is_collective:
        return False, None
    # A scan has no text layer. Trust the model. On a text invoice, require a utility word.
    if len(local.text.strip()) < 80:
        return True, remote.collective_reason or "Sammelrechnung"
    folded = local.text.casefold()
    words = ("strom", "telefon", "mobilfunk", "internet", "versicherung", "gas", "fernwärme", "fernwaerme")
    if any(word in folded for word in words):
        return True, remote.collective_reason or "Sammelrechnung"
    return False, None


def _supplier(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:12]:
        if re.search(r"johannes kepler|\bjku\b", line, re.IGNORECASE):
            continue
        if SUPPLIER_RE.search(line):
            return line
    return lines[0] if lines else None


def _collective_reason(text: str, supplier: str | None) -> str | None:
    folded = text.casefold()
    head = "\n".join(folded.splitlines()[:8])
    for pattern, label in COLLECTIVE_PATTERNS:
        if re.search(pattern, head):
            return label
    supplier_folded = (supplier or "").casefold()
    for needle, label in COLLECTIVE_SUPPLIERS:
        if needle in supplier_folded or needle in head:
            return label
    return None


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def _unique(values) -> list[str]:
    seen: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen

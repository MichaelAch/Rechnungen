from __future__ import annotations

from app.models import Correction, CostObject, InvoiceFields, Suggestion
from app.sap import SapClient, supplier_key

ORIGIN_LABEL = {
    "po": "Bestellbezug",
    "text": "Hinweis im Text",
    "institute": "Institutsname",
    "correction": "gespeicherte Korrektur",
    "history": "Lieferantenhistorie",
    "manual": "manuelle Korrektur",
    "none": "kein Treffer",
    "collective": "Sammelrechnung",
}

SETTLED = {"po", "text", "institute", "correction", "history"}


def match_invoice(
    fields: InvoiceFields,
    sap: SapClient,
    correction: Correction | None,
) -> Suggestion:
    if fields.is_collective:
        reason = fields.collective_reason or "Sammelrechnung"
        return _suggestion(
            fields,
            None,
            0,
            (
                f"Die Rechnung ist als {reason} erkennbar. Sammelrechnungen für Strom, Telefon, "
                "Internet oder Versicherungen werden in Phase 1 nicht zugeordnet."
            ),
            ["Lieferant"],
            "collective",
            collective_reason=reason,
        )

    order = sap.purchase_order(fields.po_number)
    if fields.po_number and order is not None:
        overlap = _supplier_overlap(fields.supplier_name, order.supplier_name)
        confidence = 93 if overlap else 68
        fit = (
            "Der Lieferant auf der Rechnung passt zur Bestellung."
            if overlap
            else (
                f"Der Lieferant auf der Rechnung ({fields.supplier_name or 'unbekannt'}) "
                f"weicht vom Lieferanten der Bestellung ({order.supplier_name}) ab."
            )
        )
        return _from_object(
            fields,
            order.cost_object,
            confidence,
            (
                f"Die Rechnung nennt die Bestellnummer {order.number}. Im SAP-Auszug hängt an dieser "
                f"Bestellung {_label(order.cost_object)}. Verantwortliche Person laut Stammdaten: "
                f"{order.cost_object.responsible.name}, {order.cost_object.responsible.email}. {fit}"
            ),
            ["Bestellnummer", "Lieferant"],
            "po",
        )
    if fields.po_number and order is None:
        return _suggestion(
            fields,
            None,
            34,
            (
                f"Die Rechnung nennt die Bestellnummer {fields.po_number}. "
                "Zu dieser Nummer gibt es im SAP-Auszug keine Bestellung."
            ),
            ["Bestellnummer"],
            "none",
        )

    mentions = sap.find_in_text(fields.text)
    id_hits = [obj for obj in mentions if obj.object_id.casefold() in fields.text.casefold()]
    if len(id_hits) == 1:
        obj = id_hits[0]
        return _from_object(
            fields,
            obj,
            84,
            (
                f"Im Rechnungstext steht {_label(obj)}. "
                f"Verantwortliche Person laut Stammdaten: {obj.responsible.name}, {obj.responsible.email}."
            ),
            ["Kostenstelle im Text" if obj.object_type == "kostenstelle" else "Innenauftrag im Text"],
            "text",
        )
    if len(id_hits) > 1:
        return _ambiguous(fields, id_hits)

    named = [obj for obj in mentions if obj.name.casefold() in fields.text.casefold() or _tail(obj) in fields.text.casefold()]
    if len(named) == 1:
        obj = named[0]
        return _from_object(
            fields,
            obj,
            74,
            (
                f"Im Rechnungstext steht „{obj.name}“. Im SAP-Auszug gehört dazu {_label(obj)}. "
                f"Verantwortliche Person laut Stammdaten: {obj.responsible.name}, {obj.responsible.email}."
            ),
            ["Institut"],
            "institute",
        )
    if len(named) > 1:
        return _ambiguous(fields, named)

    saved = _saved_object(correction, sap)
    if saved is not None and correction is not None:
        contact_name = correction.contact_name or saved.responsible.name
        contact_email = correction.contact_email or saved.responsible.email
        return _suggestion(
            fields,
            saved,
            76,
            (
                f"Auf der Rechnung steht kein eigener Kostenträger. Für {fields.supplier_name} ist eine "
                f"frühere Korrektur gespeichert: {_label(saved)}, {contact_name}"
                + (f", {contact_email}" if contact_email else "")
                + ". Bitte prüfen, ob sie auch für diese Rechnung gilt."
            ),
            ["Lieferant", "gespeicherte Korrektur"],
            "correction",
            contact_name=contact_name,
            contact_email=contact_email,
        )

    key = supplier_key(fields.supplier_name)
    historical = sap.history_for(key)
    if historical is not None:
        return _from_object(
            fields,
            historical,
            58,
            (
                f"Auf der Rechnung steht kein Kostenträger. In der Lieferantenhistorie wurde "
                f"{fields.supplier_name} zuletzt {_label(historical)} zugeordnet. "
                f"Ansprechpartner daraus: {historical.responsible.name}, {historical.responsible.email}. "
                "Das ist ein Hinweis, keine Bestellzuordnung."
            ),
            ["Lieferant", "Lieferantenhistorie"],
            "history",
        )

    return _suggestion(
        fields,
        None,
        22,
        (
            "Weder eine Bestellnummer noch eine Kostenstelle, ein Innenauftrag oder ein Institutsname "
            "lassen sich einem SAP-Stammsatz zuordnen. Für diesen Lieferanten gibt es auch keine "
            "gespeicherte Korrektur. Bitte die Zuordnung im Chat nachtragen."
        ),
        [],
        "none",
    )


def apply_grok_choice(
    local: Suggestion,
    payload: dict,
    sap: SapClient,
    fields: InvoiceFields,
) -> Suggestion:
    """Keep a rule hit. Accept Grok only when the rules found no cost object and the id exists in SAP."""
    if local.is_collective or (local.origin in SETTLED and local.cost_object_id):
        if payload.get("cost_object_id") == local.cost_object_id and payload.get("reasoning"):
            local.reasoning = str(payload["reasoning"]).strip()
            local.engine = "grok"
        return local
    obj = sap.get_cost_object(payload.get("cost_object_type"), payload.get("cost_object_id"))
    if obj is None:
        return local
    confidence = _clamp(payload.get("confidence"), 15, 90)
    reasoning = str(payload.get("reasoning") or "").strip() or local.reasoning
    folded = fields.text.casefold()
    origin = "text" if obj.object_id.casefold() in folded or obj.name.casefold() in folded else "history"
    return _from_object(fields, obj, confidence, reasoning, ["Lieferant"], origin, engine="grok")


def grok_context(fields: InvoiceFields, sap: SapClient, correction: Correction | None) -> dict:
    order = sap.purchase_order(fields.po_number)
    mentions = [
        _object_payload(obj) for obj in sap.find_in_text("\n".join(filter(None, [
            fields.text,
            " ".join(fields.institute_hints),
            " ".join(fields.cost_center_hints),
            " ".join(fields.internal_order_hints),
        ])))
    ]
    history = sap.history_for(supplier_key(fields.supplier_name))
    return {
        "rechnung": {
            "lieferant": fields.supplier_name,
            "rechnungsnummer": fields.invoice_number,
            "datum": fields.invoice_date,
            "betrag": fields.total_amount,
            "bestellnummer": fields.po_number,
            "kostenstellen_im_text": fields.cost_center_hints,
            "innenauftraege_im_text": fields.internal_order_hints,
            "institute_im_text": fields.institute_hints,
            "sammelrechnung": fields.is_collective,
            "sammelgrund": fields.collective_reason,
        },
        "sap_auszug": {
            "bestellung": None if order is None else {
                "nummer": order.number,
                "lieferant": order.supplier_name,
                "kostentraeger": _object_payload(order.cost_object),
            },
            "treffer_im_text": mentions,
            "lieferantenhistorie": None if history is None else _object_payload(history),
            "gespeicherte_korrektur": None if correction is None else {
                "kostentraeger_typ": correction.cost_object_type,
                "kostentraeger_id": correction.cost_object_id,
                "name": correction.cost_object_name,
                "ansprechpartner": correction.contact_name,
                "email": correction.contact_email,
            },
        },
    }


def _from_object(
    fields: InvoiceFields,
    obj: CostObject,
    confidence: int,
    reasoning: str,
    used: list[str],
    origin: str,
    engine: str = "regeln",
    contact_name: str | None = None,
    contact_email: str | None = None,
) -> Suggestion:
    return _suggestion(
        fields,
        obj,
        confidence,
        reasoning,
        used,
        origin,
        engine=engine,
        contact_name=contact_name or obj.responsible.name,
        contact_email=contact_email or obj.responsible.email,
    )


def _suggestion(
    fields: InvoiceFields,
    obj: CostObject | None,
    confidence: int,
    reasoning: str,
    used: list[str],
    origin: str,
    engine: str = "regeln",
    contact_name: str | None = None,
    contact_email: str | None = None,
    collective_reason: str | None = None,
) -> Suggestion:
    return Suggestion(
        is_collective=origin == "collective" or fields.is_collective and origin == "collective",
        collective_reason=collective_reason,
        cost_object_type=None if obj is None else obj.object_type,
        cost_object_id=None if obj is None else obj.object_id,
        cost_object_name=None if obj is None else obj.name,
        contact_name=contact_name,
        contact_email=contact_email,
        confidence=confidence,
        reasoning=reasoning,
        used_fields=used,
        origin=origin,
        engine=engine,
        sap_mode="mock",
    )


def _ambiguous(fields: InvoiceFields, objects: list[CostObject]) -> Suggestion:
    names = ", ".join(f"{obj.object_id} {obj.name}" for obj in objects)
    return _suggestion(
        fields,
        None,
        40,
        f"Im Text passen mehrere Kostenträger: {names}. Bitte im Chat einen davon festlegen.",
        ["Institut"],
        "none",
    )


def _saved_object(correction: Correction | None, sap: SapClient) -> CostObject | None:
    if correction is None:
        return None
    return sap.get_cost_object(correction.cost_object_type, correction.cost_object_id)


def _label(obj: CostObject) -> str:
    kind = "die Kostenstelle" if obj.object_type == "kostenstelle" else "der Innenauftrag"
    return f"{kind} {obj.object_id} {obj.name}"


def _tail(obj: CostObject) -> str:
    return _distinctive(obj.name)


def _distinctive(name: str) -> str:
    return name.casefold().removeprefix("institut für ").strip()


def _supplier_overlap(invoice_supplier: str | None, order_supplier: str) -> bool:
    left = set(supplier_key(invoice_supplier).split())
    right = set(supplier_key(order_supplier).split())
    return bool(left and right and left & right)


def _object_payload(obj: CostObject) -> dict:
    return {
        "typ": obj.object_type,
        "id": obj.object_id,
        "name": obj.name,
        "ansprechpartner": obj.responsible.name,
        "email": obj.responsible.email,
        "sap_user": obj.responsible.user_id,
    }


def _clamp(value, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))

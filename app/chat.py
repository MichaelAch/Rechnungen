from __future__ import annotations

import re

from app.corrections import CorrectionStore
from app.grok_client import GrokError, answer_question
from app.models import Analysis, Correction, CostObject, Suggestion
from app.sap import SapClient, supplier_key

QUESTION_RE = re.compile(r"^\s*(warum|wieso|weshalb|wie|was|welche[rs]?|wo)\b", re.IGNORECASE)
EXPLICIT_RE = re.compile(
    r"\b(falsch|korrekt\w*|änder\w*|aender\w*|gehört|gehoert|sondern|nimm|nehme|setzen|ansprechpartner)\b",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[\w.\-+]+@[\w.\-]+\.\w+")
NAME_RE = re.compile(
    r"ansprechpartner(?:in)?\s*(?:ist|sind|:|=)?\s+(.+?)(?:[,;]|$)",
    re.IGNORECASE,
)
PREFER_RE = re.compile(
    r"\b(?:sondern|nimm|nehme|korrigiere auf|ändere auf|aendere auf)\b\s*(.+)$",
    re.IGNORECASE,
)


def handle_message(
    message: str,
    analysis: Analysis,
    sap: SapClient,
    store: CorrectionStore,
    api_key: str | None = None,
) -> tuple[str, Analysis]:
    text = message.strip()
    if not text:
        return "Die Nachricht ist leer.", analysis

    if QUESTION_RE.search(text) and not EXPLICIT_RE.search(text):
        return _explain(text, analysis, api_key), analysis

    resolved = _resolve(text, analysis, sap)
    if resolved == "ambiguous":
        return "In der Nachricht stehen mehrere Kostenträger. Nenne bitte genau einen.", analysis
    if not isinstance(resolved, tuple):
        if re.search(r"\b(kostenstelle|innenauftrag|kst)\b|\b\d{5,10}\b", text, re.IGNORECASE):
            return "Diese Nummer steht nicht im SAP-Auszug.", analysis
        return _explain(text, analysis, api_key), analysis

    obj, contact_name, contact_email = resolved
    supplier = analysis.fields.supplier_name
    if not supplier:
        return "Der Lieferant fehlt auf der Rechnung. Ohne Lieferant kann ich die Korrektur nicht speichern.", analysis

    note = None
    if analysis.suggestion.is_collective:
        note = "Manuell gesetzt, obwohl die Rechnung als Sammelrechnung erkannt wurde."
    suggestion = Suggestion(
        is_collective=False,
        collective_reason=None,
        cost_object_type=obj.object_type,
        cost_object_id=obj.object_id,
        cost_object_name=obj.name,
        contact_name=contact_name,
        contact_email=contact_email,
        confidence=100,
        reasoning=_manual_reasoning(supplier, obj, contact_name, contact_email, note),
        used_fields=["manuelle Korrektur"],
        origin="manual",
        engine="korrektur",
        sap_mode="mock",
    )
    store.save(
        Correction(
            supplier_name=supplier,
            supplier_key=supplier_key(supplier),
            cost_object_type=obj.object_type,
            cost_object_id=obj.object_id,
            cost_object_name=obj.name,
            contact_name=contact_name,
            contact_email=contact_email,
            note=note or text,
            source_invoice=analysis.fields.invoice_number,
        )
    )
    updated = Analysis(
        fields=analysis.fields,
        suggestion=suggestion,
        warning=analysis.warning,
        page_count=analysis.page_count,
    )
    kind = "Kostenstelle" if obj.object_type == "kostenstelle" else "Innenauftrag"
    reply = (
        f"Gespeichert für {supplier}: {kind} {obj.object_id} {obj.name}, "
        f"Ansprechpartner {contact_name or 'ohne Namen'}"
        + (f" ({contact_email})" if contact_email else "")
        + ". Für künftige Rechnungen dieses Lieferanten gilt das, solange die Rechnung "
        "keinen eigenen Bestell- oder Kostenträgerhinweis enthält."
    )
    if note:
        reply = note + " " + reply
    return reply, updated


def _resolve(message: str, analysis: Analysis, sap: SapClient):
    preferred_text = message
    preferred = PREFER_RE.search(message)
    if preferred:
        preferred_text = preferred.group(1)
    hits = _unique(sap.find_in_text(preferred_text) or sap.find_in_text(message))
    if preferred and sap.find_in_text(preferred_text):
        hits = sap.find_in_text(preferred_text)
    if len(hits) > 1:
        return "ambiguous"
    obj = hits[0] if hits else None
    if obj is None and analysis.suggestion.cost_object_id and _contact_only(message):
        obj = sap.get_cost_object(analysis.suggestion.cost_object_type, analysis.suggestion.cost_object_id)
    contact_name, contact_email = _contact(message)
    if obj is None:
        return None
    if contact_name is None and contact_email is None:
        contact_name = obj.responsible.name
        contact_email = obj.responsible.email
    elif contact_email is None and contact_name and _same_person(contact_name, obj.responsible.name):
        contact_name = obj.responsible.name
        contact_email = obj.responsible.email
    return obj, contact_name, contact_email


def _contact(message: str) -> tuple[str | None, str | None]:
    email_match = EMAIL_RE.search(message)
    email = email_match.group(0) if email_match else None
    name_match = NAME_RE.search(message)
    name = None
    if name_match:
        name = EMAIL_RE.sub("", name_match.group(1))
        name = re.split(
            r"\b(danke|bitte|kostenstelle|innenauftrag)\b",
            name,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        name = name.strip(" .,-")
        if len(name) > 80 or len(name) < 3:
            name = None
    return name, email


def _contact_only(message: str) -> bool:
    return NAME_RE.search(message) is not None or EMAIL_RE.search(message) is not None


def _unique(objects: list[CostObject]) -> list[CostObject]:
    seen: list[CostObject] = []
    for obj in objects:
        if all(obj.object_id != other.object_id for other in seen):
            seen.append(obj)
    return seen


def _same_person(left: str, right: str) -> bool:
    return left.casefold() in right.casefold() or right.casefold() in left.casefold()


def _manual_reasoning(
    supplier: str,
    obj: CostObject,
    contact_name: str | None,
    contact_email: str | None,
    note: str | None,
) -> str:
    kind = "Kostenstelle" if obj.object_type == "kostenstelle" else "Innenauftrag"
    text = (
        f"Manuell korrigiert für {supplier}: {kind} {obj.object_id} {obj.name}, "
        f"{contact_name or 'ohne Ansprechpartner'}"
        + (f", {contact_email}" if contact_email else "")
        + "."
    )
    if note:
        return note + " " + text
    return text


def _explain(message: str, analysis: Analysis, api_key: str | None) -> str:
    suggestion = analysis.suggestion
    if api_key:
        try:
            return answer_question(
                api_key,
                message,
                {
                    "lieferant": analysis.fields.supplier_name,
                    "vorschlag": suggestion.cost_object_id,
                    "name": suggestion.cost_object_name,
                    "ansprechpartner": suggestion.contact_name,
                    "email": suggestion.contact_email,
                    "konfidenz": suggestion.confidence,
                    "herkunft": suggestion.origin,
                    "begründung": suggestion.reasoning,
                    "sammelrechnung": suggestion.is_collective,
                },
            )
        except GrokError:
            pass
    if suggestion.is_collective:
        return suggestion.reasoning
    if suggestion.cost_object_id:
        return (
            f"{suggestion.reasoning} Konfidenz {suggestion.confidence} %. "
            "Eine andere Zuordnung kannst du im Chat festlegen."
        )
    return suggestion.reasoning



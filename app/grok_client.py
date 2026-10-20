from __future__ import annotations

import base64
import json
import re

from app.config import MODEL

EXTRACT_INSTRUCTIONS = """
Du liest eine Lieferantenrechnung für die Johannes Kepler Universität Linz.
Antworte ausschließlich mit JSON in dieser Form:
{
  "supplier_name": null,
  "invoice_number": null,
  "invoice_date": null,
  "total_amount": null,
  "currency": "EUR",
  "po_number": null,
  "cost_center_hints": [],
  "internal_order_hints": [],
  "institute_hints": [],
  "is_collective": false,
  "collective_reason": null
}
Setze ein Feld nur, wenn es in der Rechnung steht. po_number ist eine Bestellnummer, sonst null.
is_collective ist true nur bei Strom, Gas, Fernwärme, Telefon, Mobilfunk, Internet oder Versicherung.
collective_reason ist dann eines dieser Wörter. Erfinde nichts.
""".strip()

MATCH_INSTRUCTIONS = """
Du ordnest eine Lieferantenrechnung einem Kostenträger zu.
Du hast keinen SAP-Zugriff. Verwende nur Kostenträger aus dem mitgelieferten SAP-Auszug.
Regeln in dieser Reihenfolge:
1. Sammelrechnungen für Strom, Telefon, Internet oder Versicherungen nicht zuordnen.
2. Steht eine Bestellung im Auszug, verwende deren Kostenträger und die dort genannte Person.
3. Sonst Kostenstelle, Innenauftrag oder Institutsname aus dem Rechnungstext.
4. Sonst die gespeicherte Korrektur, danach die Lieferantenhistorie.
5. Ohne klaren Hinweis keinen Kostenträger setzen und confidence unter 40 lassen.
Begründung auf Deutsch, mit den verwendeten Feldern.
Antworte ausschließlich mit JSON:
{
  "is_collective": false,
  "collective_reason": null,
  "cost_object_type": null,
  "cost_object_id": null,
  "cost_object_name": null,
  "contact_name": null,
  "contact_email": null,
  "confidence": 0,
  "reasoning": ""
}
""".strip()

CHAT_INSTRUCTIONS = """
Du beantwortest eine Frage eines Mitarbeiters zu einer bereits angezeigten Rechnungszuordnung.
Du änderst keine Daten. Antworte auf Deutsch in höchstens vier Sätzen und bleibe bei den mitgelieferten Fakten.
Wenn die Fakten nicht reichen, sag das.
""".strip()


class GrokError(RuntimeError):
    pass


def extract_invoice(api_key: str, text: str, images: list[bytes] | None = None) -> dict:
    body = text.strip() or "Die Rechnung liegt nur als Bild vor."
    if len(body) > 12000:
        body = body[:12000]
    return _json_call(api_key, EXTRACT_INSTRUCTIONS, body, images)


def match_with_context(api_key: str, context: dict) -> dict:
    return _json_call(api_key, MATCH_INSTRUCTIONS, json.dumps(context, ensure_ascii=False))


def answer_question(api_key: str, question: str, context: dict) -> str:
    payload = json.dumps({"frage": question, "auswertung": context}, ensure_ascii=False)
    return _text_call(api_key, CHAT_INSTRUCTIONS, payload).strip()


def _json_call(api_key: str, instructions: str, user_text: str, images: list[bytes] | None = None) -> dict:
    raw = _text_call(api_key, instructions, user_text, images)
    return _parse_json(raw)


def _text_call(api_key: str, instructions: str, user_text: str, images: list[bytes] | None = None) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise GrokError("Das Paket openai ist nicht installiert.") from exc

    content: str | list[dict] = user_text
    if images:
        content = [_image_part(blob) for blob in images[:2]]
        content.append({"type": "input_text", "text": user_text})
    try:
        client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1", timeout=120)
        response = client.responses.create(
            model=MODEL,
            store=False,
            input=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": content},
            ],
        )
    except Exception as exc:
        raise GrokError(str(exc)) from exc
    text = _response_text(response)
    if not text.strip():
        raise GrokError("Grok hat keine Antwort geliefert.")
    return text


def _image_part(blob: bytes) -> dict:
    mime = "image/png" if blob.startswith(b"\x89PNG") else "image/jpeg"
    encoded = base64.standard_b64encode(blob).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:{mime};base64,{encoded}",
        "detail": "high",
    }


def _response_text(response) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            value = getattr(content, "text", None)
            if value:
                parts.append(value)
    return "\n".join(parts)


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise GrokError("Die Antwort von Grok enthält kein JSON.")
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise GrokError("Die Antwort von Grok ist kein gültiges JSON.") from exc
    if not isinstance(payload, dict):
        raise GrokError("Die Antwort von Grok hat nicht die erwartete Form.")
    return payload

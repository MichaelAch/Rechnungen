from __future__ import annotations

from pathlib import Path

import pymupdf

KIND = {
    ".pdf": "pdf",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".tif": "tiff",
    ".tiff": "tiff",
}

DEMOS: dict[str, list[str]] = {
    "po": [
        "Bürobedarf Mayer GmbH",
        "Wiener Straße 12",
        "4020 Linz",
        "",
        "Rechnung",
        "Rechnungsnummer: RE-2026-1042",
        "Rechnungsdatum: 12.10.2026",
        "Bestellnummer: 4500012345",
        "",
        "Pos  Beschreibung                 Betrag",
        "1    Kopierpapier und Ordner      1.190,00",
        "     MwSt 20 Prozent                238,00",
        "Gesamtbetrag EUR                 1.428,00",
    ],
    "institute": [
        "Fachbuch Holzer GmbH",
        "Altenberger Straße 7",
        "4040 Linz",
        "",
        "Rechnung",
        "Rechnungsnummer: RE-2026-2210",
        "Rechnungsdatum: 03.10.2026",
        "Bezug: Fachbücher für das Institut für Wirtschaftsinformatik",
        "Kein Bestellbezug",
        "",
        "Gesamtbetrag EUR 860,40",
    ],
    "history": [
        "Papier Union Österreich GmbH",
        "Industriezeile 40",
        "4020 Linz",
        "",
        "Rechnung",
        "Rechnungsnummer: RE-88331",
        "Rechnungsdatum: 01.09.2026",
        "Büromaterial laut Rahmenvereinbarung",
        "",
        "Gesamtbetrag EUR 240,00",
    ],
    "collective": [
        "Energie AG Oberösterreich",
        "Böhmerwaldstraße 3",
        "4020 Linz",
        "",
        "Stromrechnung",
        "Rechnungsnummer: STR-2026-0901",
        "Rechnungsdatum: 05.09.2026",
        "Zeitraum: August 2026",
        "Kundennummer: 445566",
        "Verbrauchsstelle: Altenberger Straße 69, 4040 Linz",
        "",
        "Gesamtbetrag EUR 3.102,55",
    ],
}


def demo_pdf(kind: str) -> bytes:
    lines = DEMOS[kind]
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 72
    for index, line in enumerate(lines):
        size = 16 if index == 0 else 12
        page.insert_text((64, y), line, fontsize=size, fontname="helv")
        y += 22 if index == 0 else 18
    data = doc.tobytes()
    doc.close()
    return data


def file_kind(filename: str) -> str | None:
    return KIND.get(Path(filename).suffix.lower())


def open_document(data: bytes, filename: str) -> pymupdf.Document:
    kind = file_kind(filename)
    if kind is None:
        raise ValueError("Erlaubt sind PDF, PNG, JPG und TIFF.")
    return pymupdf.open(stream=data, filetype=kind)


def read_text(data: bytes, filename: str) -> tuple[str, int]:
    doc = open_document(data, filename)
    try:
        text = "\n".join(page.get_text() for page in doc)
        return text, doc.page_count
    finally:
        doc.close()


def render_page(data: bytes, filename: str, page_index: int) -> bytes:
    doc = open_document(data, filename)
    try:
        page = doc[page_index]
        scale = 1.7 if file_kind(filename) == "pdf" else 1.0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()

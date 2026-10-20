from pathlib import Path

from app.chat import handle_message
from app.corrections import CorrectionStore
from app.documents import demo_pdf, read_text
from app.pipeline import analyze
from app.sap import COST_OBJECTS, MockSapClient, select_mentions, supplier_key


def _store(tmp_path: Path) -> CorrectionStore:
    return CorrectionStore(tmp_path / "corrections.db")


def _analyze(kind: str, tmp_path: Path):
    return analyze(demo_pdf(kind), f"{kind}.pdf", MockSapClient(), _store(tmp_path), api_key=None)


def test_purchase_order_demo_follows_sap_chain(tmp_path: Path):
    result = _analyze("po", tmp_path)
    suggestion = result.suggestion
    assert result.fields.po_number == "4500012345"
    assert result.fields.supplier_name == "Bürobedarf Mayer GmbH"
    assert result.fields.total_amount == "1.428,00"
    assert suggestion.cost_object_id == "150210"
    assert suggestion.contact_name == "Dr. Eva Huber"
    assert suggestion.contact_email == "eva.huber@jku.at"
    assert suggestion.confidence >= 80
    assert "4500012345" in suggestion.reasoning
    assert suggestion.is_collective is False


def test_institute_demo_prefers_the_longer_name(tmp_path: Path):
    result = _analyze("institute", tmp_path)
    assert result.suggestion.cost_object_id == "150110"
    assert result.suggestion.contact_email == "sara.leitner@jku.at"
    assert result.suggestion.origin == "institute"
    assert 50 <= result.suggestion.confidence < 80


def test_history_demo_is_a_weak_hint(tmp_path: Path):
    result = _analyze("history", tmp_path)
    assert result.suggestion.cost_object_id == "150210"
    assert result.suggestion.origin == "history"
    assert result.suggestion.confidence < 70
    assert "Historie" in result.suggestion.reasoning or "historie" in result.suggestion.reasoning.casefold()


def test_collective_demo_has_no_assignment(tmp_path: Path):
    result = _analyze("collective", tmp_path)
    assert result.suggestion.is_collective is True
    assert result.suggestion.cost_object_id is None
    assert result.suggestion.contact_name is None
    assert result.fields.collective_reason == "Strom"


def test_saved_correction_is_reused_for_the_same_supplier(tmp_path: Path):
    sap = MockSapClient()
    store = _store(tmp_path)
    first = _analyze("history", tmp_path)
    # Separate store: the assertion above uses its own database.
    reply, updated = handle_message(
        "Bitte Kostenstelle 150110, Ansprechpartnerin ist Mag. Sara Leitner",
        first,
        sap,
        store,
    )
    assert "150110" in reply
    assert updated.suggestion.confidence == 100
    assert updated.suggestion.contact_email == "sara.leitner@jku.at"

    again = analyze(
        demo_pdf("history"),
        "history.pdf",
        sap,
        store,
        api_key=None,
    )
    # The history invoice names no cost object, so the saved correction wins over static history.
    assert again.fields.supplier_name == "Papier Union Österreich GmbH"
    assert again.suggestion.origin == "correction"
    assert again.suggestion.cost_object_id == "150110"
    assert again.suggestion.contact_email == "sara.leitner@jku.at"


def test_question_does_not_save_a_correction(tmp_path: Path):
    sap = MockSapClient()
    store = _store(tmp_path)
    analysis = _analyze("po", tmp_path)
    reply, updated = handle_message("Warum diese Kostenstelle?", analysis, sap, store)
    assert updated.suggestion.cost_object_id == "150210"
    assert store.recent() == []
    assert "150210" in reply or "Bestellnummer" in reply


def test_ambiguous_message_is_not_stored(tmp_path: Path):
    sap = MockSapClient()
    store = _store(tmp_path)
    analysis = _analyze("po", tmp_path)
    reply, updated = handle_message("150210 oder 150340", analysis, sap, store)
    assert "mehrere" in reply.casefold()
    assert updated.suggestion.origin == "po"
    assert store.recent() == []


def test_unknown_cost_center_is_rejected(tmp_path: Path):
    sap = MockSapClient()
    store = _store(tmp_path)
    analysis = _analyze("po", tmp_path)
    reply, updated = handle_message("Kostenstelle 999999", analysis, sap, store)
    assert updated.suggestion.cost_object_id == "150210"
    assert store.recent() == []
    assert "SAP-Auszug" in reply


def test_wirtschaftsinformatik_does_not_select_informatik():
    text = "Fachbücher für das Institut für Wirtschaftsinformatik"
    hits = select_mentions(text, list(COST_OBJECTS))
    assert [obj.object_id for obj in hits] == ["150110"]


def test_explicit_id_beats_a_shorter_name():
    text = "Kostenstelle 150340 für das Institut für Informatik"
    hits = select_mentions(text, list(COST_OBJECTS))
    ids = {obj.object_id for obj in hits}
    assert "150340" in ids
    assert "150210" in ids


def test_demo_pdf_has_a_text_layer():
    text, pages = read_text(demo_pdf("po"), "demo.pdf")
    assert pages == 1
    assert "4500012345" in text
    assert "Mayer" in text


def test_manual_override_of_collective_invoice_is_explicit(tmp_path: Path):
    sap = MockSapClient()
    store = _store(tmp_path)
    analysis = _analyze("collective", tmp_path)
    reply, updated = handle_message(
        "Das ist keine Sammelrechnung, sondern Kostenstelle 120100",
        analysis,
        sap,
        store,
    )
    assert updated.suggestion.is_collective is False
    assert updated.suggestion.cost_object_id == "120100"
    assert updated.suggestion.contact_email == "peter.gruber@jku.at"
    assert "Sammelrechnung" in reply
    assert store.latest_for(supplier_key("Energie AG Oberösterreich")) is not None


def test_contact_change_keeps_the_current_cost_object(tmp_path: Path):
    sap = MockSapClient()
    store = _store(tmp_path)
    analysis = _analyze("po", tmp_path)
    _reply, updated = handle_message(
        "Ansprechpartner ist Max Mustermann max.mustermann@jku.at",
        analysis,
        sap,
        store,
    )
    assert updated.suggestion.cost_object_id == "150210"
    assert updated.suggestion.contact_name == "Max Mustermann"
    assert updated.suggestion.contact_email == "max.mustermann@jku.at"


def test_purchase_order_beats_a_saved_correction(tmp_path: Path):
    sap = MockSapClient()
    store = _store(tmp_path)
    current = _analyze("po", tmp_path)
    handle_message("Kostenstelle 120100", current, sap, store)
    again = analyze(demo_pdf("po"), "po.pdf", sap, store, api_key=None)
    assert again.suggestion.origin == "po"
    assert again.suggestion.cost_object_id == "150210"
    assert again.suggestion.contact_email == "eva.huber@jku.at"



from __future__ import annotations

import re
from typing import Protocol

from app.models import CostObject, Person, PurchaseOrder

# Ersatzdaten, solange SAP nicht erreichbar ist. Der spätere OData-Client
# liefert nur Suchtreffer und ruft danach select_mentions auf. Er gibt nicht
# den gesamten Kostenstellenstamm zurück.


def _person(user_id: str, name: str, email: str) -> Person:
    return Person(user_id=user_id, name=name, email=email)


def _cc(object_id: str, name: str, person: Person) -> CostObject:
    return CostObject("kostenstelle", object_id, name, person)


def _io(object_id: str, name: str, person: Person) -> CostObject:
    return CostObject("innenauftrag", object_id, name, person)


HUBER = _person("HUBER_E", "Dr. Eva Huber", "eva.huber@jku.at")
LEITNER = _person("LEITNER_S", "Mag. Sara Leitner", "sara.leitner@jku.at")
MAIER = _person("MAIER_T", "Dr. Thomas Maier", "thomas.maier@jku.at")
GRUBER = _person("GRUBER_P", "Ing. Peter Gruber", "peter.gruber@jku.at")

COST_OBJECTS: tuple[CostObject, ...] = (
    _cc("150210", "Institut für Informatik", HUBER),
    _cc("150110", "Institut für Wirtschaftsinformatik", LEITNER),
    _cc("150340", "Institut für Chemie", MAIER),
    _cc("120100", "Facility Management", GRUBER),
    _io("IA-2026-014", "NMR-Spektrometer", MAIER),
    _io("IA-2026-031", "Serverraum Informatik", HUBER),
)

PURCHASE_ORDERS: tuple[PurchaseOrder, ...] = (
    PurchaseOrder("4500012345", "Bürobedarf Mayer GmbH", COST_OBJECTS[0]),
    PurchaseOrder("4500012402", "Labortechnik Schmidt KG", COST_OBJECTS[4]),
)

# Frühere Zuordnung ohne Bezug zur aktuellen Rechnung.
SUPPLIER_HISTORY: dict[str, str] = {
    "papier union oesterreich": "150210",
}


def supplier_key(name: str | None) -> str:
    if not name:
        return ""
    text = name.casefold()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    text = re.sub(r"\b(gmbh|kg|ag|og|e\.u\.|eu|co|ltd|inc|gesmbh)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _distinctive(name: str) -> str:
    return name.casefold().removeprefix("institut für ").strip()


def select_mentions(text: str, objects: list[CostObject]) -> list[CostObject]:
    """Keep objects named in the text. A shorter name loses if it only appears inside a longer hit."""
    folded = text.casefold()
    raw: list[tuple[CostObject, bool, str]] = []
    for obj in objects:
        full = obj.name.casefold() in folded or obj.object_id.casefold() in folded
        distinctive = _distinctive(obj.name)
        soft = len(distinctive) >= 8 and distinctive in folded
        if full or soft:
            raw.append((obj, full, distinctive))
    kept: list[CostObject] = []
    for obj, full, distinctive in raw:
        dominated = False
        if not full:
            for other, _other_full, other_distinctive in raw:
                if other is not obj and distinctive != other_distinctive and distinctive in other_distinctive:
                    dominated = True
                    break
        if not dominated:
            kept.append(obj)
    return kept


class SapClient(Protocol):
    def get_cost_object(self, object_type: str | None, object_id: str | None) -> CostObject | None: ...

    def find_in_text(self, text: str) -> list[CostObject]: ...

    def purchase_order(self, number: str | None) -> PurchaseOrder | None: ...

    def history_for(self, key: str) -> CostObject | None: ...


class MockSapClient:
    """Read-only stand-in. Nothing here is written back to SAP."""

    def __init__(self) -> None:
        self._objects = list(COST_OBJECTS)
        self._orders = {order.number: order for order in PURCHASE_ORDERS}

    def get_cost_object(self, object_type: str | None, object_id: str | None) -> CostObject | None:
        if not object_id:
            return None
        for obj in self._objects:
            if obj.object_id.casefold() != object_id.casefold():
                continue
            if object_type and obj.object_type != object_type:
                continue
            return obj
        return None

    def find_in_text(self, text: str) -> list[CostObject]:
        return select_mentions(text, self._objects)

    def purchase_order(self, number: str | None) -> PurchaseOrder | None:
        if not number:
            return None
        return self._orders.get(number.strip())

    def history_for(self, key: str) -> CostObject | None:
        object_id = SUPPLIER_HISTORY.get(key)
        if not object_id:
            return None
        return self.get_cost_object(None, object_id)


def get_sap_client() -> SapClient:
    return MockSapClient()

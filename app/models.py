from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Person:
    user_id: str
    name: str
    email: str


@dataclass
class CostObject:
    object_type: str
    object_id: str
    name: str
    responsible: Person


@dataclass
class PurchaseOrder:
    number: str
    supplier_name: str
    cost_object: CostObject


@dataclass
class InvoiceFields:
    supplier_name: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    total_amount: str | None = None
    currency: str | None = None
    po_number: str | None = None
    cost_center_hints: list[str] = field(default_factory=list)
    internal_order_hints: list[str] = field(default_factory=list)
    institute_hints: list[str] = field(default_factory=list)
    is_collective: bool = False
    collective_reason: str | None = None
    text: str = ""
    extraction_source: str = "lokal"


@dataclass
class Suggestion:
    is_collective: bool = False
    collective_reason: str | None = None
    cost_object_type: str | None = None
    cost_object_id: str | None = None
    cost_object_name: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    confidence: int = 0
    reasoning: str = ""
    used_fields: list[str] = field(default_factory=list)
    origin: str = "none"
    engine: str = "regeln"
    sap_mode: str = "mock"


@dataclass
class Analysis:
    fields: InvoiceFields
    suggestion: Suggestion
    warning: str | None = None
    page_count: int = 1


@dataclass
class Correction:
    supplier_name: str
    supplier_key: str
    cost_object_type: str
    cost_object_id: str
    cost_object_name: str
    contact_name: str | None
    contact_email: str | None
    note: str | None = None
    source_invoice: str | None = None
    created_at: str | None = None

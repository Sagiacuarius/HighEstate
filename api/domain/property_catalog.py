"""Property catalog domain model (change: property-catalog, WU11).

Provider-neutral, psycopg-free — the same layering as the accepted
api/domain/commercial_crm.py. The legacy Property dataclass stays untouched
(accepted WU3); PropertyRecord here is the enriched first-class view over the
extended properties table (migration 005). New writes go through the WU11
commands; the accepted read paths keep using the legacy columns.

Identity/tenant rules mirror the accepted baseline: ids validated with the
same guards, enums enforced here before SQL, and the commercial status machine
declared once (capturing -> available -> reserved -> sold/rented; paused is
allowed from any non-terminal status and is reversible).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from api.domain.commercial_crm import (
    CrmDomainError,
    _validate_choice,
    _validate_text,
    validate_crm_id,
)

OPERATION_TYPES_V11 = ("sale", "rent", "temporary_rent")
PROPERTY_TYPES = ("apartment", "house", "ph", "land", "office", "commercial", "warehouse")
CURRENCIES_V11 = ("USD", "ARS")
CONSTRUCTION_STATUSES = ("brand_new", "used", "needs_renovation")
CAPTURE_SOURCES = ("zone_scan", "digital_ads", "landing_page")
PROPERTY_STATUSES = ("capturing", "available", "reserved", "sold", "rented", "paused")
TERMINAL_PROPERTY_STATUSES = ("sold", "rented")
CONTACT_ROLES = ("owner", "tenant", "referrer")
INTEREST_ORIGINS = ("ai_agent", "manual")

# Status machine (brief): capturing -> available -> reserved -> sold/rented.
# paused: allowed from any non-terminal status, and reversible (back to the
# pre-pause pipeline states). sold/rented are terminal.
PROPERTY_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "capturing": ("available", "paused"),
    "available": ("reserved", "paused"),
    "reserved": ("sold", "rented", "paused"),
    "sold": (),
    "rented": (),
    "paused": ("capturing", "available", "reserved"),
}


def next_property_status(current: str, requested: str) -> str:
    """Pure transition guard: returns requested only if legal, else raises."""
    _validate_choice(current, PROPERTY_STATUSES, "Property.status")
    _validate_choice(requested, PROPERTY_STATUSES, "property.target_status")
    if requested not in PROPERTY_STATUS_TRANSITIONS[current]:
        raise CrmDomainError(
            f"property transition {current} -> {requested} is not allowed")
    return requested


def _validate_money(value: Any, label: str) -> Decimal:
    if not isinstance(value, Decimal) or value < 0:
        raise CrmDomainError(f"{label} must be a non-negative Decimal")
    return value


def _validate_optional_choice(value: Any, allowed: tuple[str, ...], label: str) -> None:
    if value is None:
        return
    _validate_choice(value, allowed, label)


def _validate_optional_int(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, int) or value < 0:
        raise CrmDomainError(f"{label} must be an integer >= 0")


def _validate_optional_urls(value: Any, label: str) -> None:
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value):
        raise CrmDomainError(f"{label} must be a list of non-empty strings")


@dataclass(frozen=True)
class PropertyRecord:
    """Enriched first-class property (migration 005 columns)."""
    id: str
    client_account_id: str
    title: str
    operation_type: str                      # sale | rent | temporary_rent
    status: str                              # commercial status machine
    price_amount: Decimal
    price_currency: str                       # USD | ARS
    reference_code: str | None = None
    property_type: str | None = None
    address: str | None = None
    floor_unit: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    expenses_amount: Decimal | None = None
    accepts_swap: bool = False
    covered_area_m2: Decimal | None = None
    total_area_m2: Decimal | None = None
    rooms: int | None = None
    bathrooms: int | None = None
    parking_spaces: int | None = None
    property_age_years: int | None = None
    construction_status: str | None = None
    photos: list[str] = field(default_factory=list)
    video_url: str | None = None
    capture_source: str | None = None
    captured_at: datetime | None = None
    responsible_agent_id: str | None = None

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "Property.id")
        validate_crm_id(self.client_account_id, "Property.client_account_id")
        _validate_text(self.title, "Property.title")
        _validate_choice(self.operation_type, OPERATION_TYPES_V11,
                         "Property.operation_type")
        _validate_choice(self.status, PROPERTY_STATUSES, "Property.status")
        _validate_money(self.price_amount, "Property.price_amount")
        _validate_choice(self.price_currency, CURRENCIES_V11, "Property.price_currency")
        if self.reference_code is not None:
            _validate_text(self.reference_code, "Property.reference_code")
            if len(self.reference_code) > 64:
                raise CrmDomainError("Property.reference_code is too long")
        _validate_optional_choice(self.property_type, PROPERTY_TYPES,
                                  "Property.property_type")
        _validate_optional_choice(self.construction_status, CONSTRUCTION_STATUSES,
                                  "Property.construction_status")
        _validate_optional_choice(self.capture_source, CAPTURE_SOURCES,
                                  "Property.capture_source")
        _validate_optional_int(self.rooms, "Property.rooms")
        _validate_optional_int(self.bathrooms, "Property.bathrooms")
        _validate_optional_int(self.parking_spaces, "Property.parking_spaces")
        _validate_optional_int(self.property_age_years, "Property.property_age_years")
        for label, value in (
                ("Property.expenses_amount", self.expenses_amount),
                ("Property.covered_area_m2", self.covered_area_m2),
                ("Property.total_area_m2", self.total_area_m2)):
            if value is not None:
                _validate_money(value, label)
        if not isinstance(self.accepts_swap, bool):
            raise CrmDomainError("Property.accepts_swap must be a boolean")
        _validate_optional_urls(self.photos, "Property.photos")
        if self.responsible_agent_id is not None:
            validate_crm_id(self.responsible_agent_id, "Property.responsible_agent_id")
        if (self.latitude is None) != (self.longitude is None):
            raise CrmDomainError("Property coordinates must come as a pair")
        for label, value in (("Property.latitude", self.latitude),
                             ("Property.longitude", self.longitude)):
            if value is not None and (
                    not isinstance(value, Decimal) or abs(value) > Decimal("180")):
                raise CrmDomainError(f"{label} must be a Decimal within +/-180")

    def with_status(self, requested: str) -> "PropertyRecord":
        return replace(self, status=next_property_status(self.status, requested))


@dataclass(frozen=True)
class PropertyAmenity:
    id: str
    name: str

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "PropertyAmenity.id")
        _validate_text(self.name, "PropertyAmenity.name")


@dataclass(frozen=True)
class PropertyContactLink:
    """Contact linked to a property with a role (owner | tenant | referrer)."""
    property_id: str
    contact_id: str
    role: str
    client_account_id: str
    linked_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_crm_id(self.property_id, "PropertyContactLink.property_id")
        validate_crm_id(self.contact_id, "PropertyContactLink.contact_id")
        validate_crm_id(self.client_account_id, "PropertyContactLink.client_account_id")
        _validate_choice(self.role, CONTACT_ROLES, "PropertyContactLink.role")


@dataclass(frozen=True)
class PropertyInterest:
    """Lightweight match marker. NEVER creates a lead: the lead->property link
    is set only when an interest converts into a visit/negotiation (documented
    in openspec/changes/property-catalog/proposal.md)."""
    id: str
    property_id: str
    contact_id: str
    origin: str
    client_account_id: str
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_crm_id(self.id, "PropertyInterest.id")
        validate_crm_id(self.property_id, "PropertyInterest.property_id")
        validate_crm_id(self.contact_id, "PropertyInterest.contact_id")
        validate_crm_id(self.client_account_id, "PropertyInterest.client_account_id")
        _validate_choice(self.origin, INTEREST_ORIGINS, "PropertyInterest.origin")

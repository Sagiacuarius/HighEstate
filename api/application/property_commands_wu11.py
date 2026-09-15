"""Atomic property-catalog commands (change: property-catalog, WU11).

Same accepted machinery as the WU5 CRM commands — canonical hash,
reserve(actor, operation, key) -> apply -> audit + outbox -> one transaction;
replay returns the canonical result, key reuse with a different payload
conflicts, denials roll back everything and append one separate redacted
audit record. The status machine is declared once in the domain layer and
re-checked inside the transaction against FOR UPDATE state (lost-update safe).

CreateProperty/UpdateProperty keep the WU3-owned legacy columns in sync
(location/bedrooms/price/currency/availability derived from the new fields,
documented in evidence) so accepted read paths never see NULL drift.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from api.application.crm_commands_wu5 import (
    CrmCommandBase,
    CrmCommandDenied,
    CrmCommandValidationError,
    CrmCommandOutcome,
)
from api.domain.commercial_crm import CrmDomainError
from api.domain.property_catalog import (
    PROPERTY_STATUSES,
    PropertyRecord,
    next_property_status,
)
from api.tenant_boundary import TenantContext

_LEGACY_AVAILABILITY = {"capturing": "unavailable", "available": "available",
                        "reserved": "reserved", "sold": "unavailable",
                        "rented": "unavailable", "paused": "paused"}


def _legacy_availability(status: str) -> str:
    # 'paused' has no legacy equivalent; availability CHECK allows
    # available|reserved|unavailable only, so paused maps to unavailable and
    # the enriched status column stays authoritative (documented).
    value = _LEGACY_AVAILABILITY[status]
    return "unavailable" if value == "paused" else value


class PropertyCommandBase(CrmCommandBase):
    audit_resource_type = "property"

    @staticmethod
    async def _lock_property(cur: Any, property_id: str, account_id: str) -> dict[str, Any]:
        await cur.execute(
            "SELECT * FROM properties WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (property_id, account_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise CrmCommandDenied("property_not_found_in_scope")
        return row

    @staticmethod
    async def _agent_has_membership(cur: Any, agent_id: str, account_id: str) -> bool:
        await cur.execute(
            "SELECT 1 FROM memberships WHERE user_id = %s AND client_account_id = %s LIMIT 1",
            (agent_id, account_id),
        )
        return await cur.fetchone() is not None


class CreatePropertyCommand(PropertyCommandBase):
    operation = "property.create"
    outbox_event_type = "PropertyCreated"

    async def execute_create(self, context: TenantContext | None, *,
                             record: PropertyRecord, idempotency_key: str,
                             amenity_ids: tuple[str, ...] = ()) -> CrmCommandOutcome:
        canonical = _record_canonical(record) | {"amenity_ids": sorted(amenity_ids)}
        return await self.execute(
            context, idempotency_key=idempotency_key, canonical_input=canonical,
            record=record, amenity_ids=tuple(amenity_ids),
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        record: PropertyRecord = intent["record"]
        account_id = context.client_account.id
        if record.client_account_id != account_id:
            raise CrmCommandDenied("event_scope_does_not_match_context")
        if record.reference_code is not None:
            await cur.execute(
                "SELECT 1 FROM properties WHERE client_account_id = %s AND reference_code = %s",
                (account_id, record.reference_code))
            if await cur.fetchone() is not None:
                raise CrmCommandValidationError("reference_code is already used in this account")
        if record.responsible_agent_id is not None:
            if not await self._agent_has_membership(cur, record.responsible_agent_id, account_id):
                raise CrmCommandDenied("responsible_agent_not_member")

        await cur.execute(
            """
            INSERT INTO properties
                (id, client_account_id, title, operation_type, status, price_amount,
                 price_currency, reference_code, property_type, address, floor_unit,
                 neighborhood, city, latitude, longitude, expenses_amount, accepts_swap,
                 covered_area_m2, total_area_m2, rooms, bathrooms, parking_spaces,
                 property_age_years, construction_status, photos, video_url,
                 capture_source, captured_at, responsible_agent_id,
                 location, bedrooms, price, currency, availability)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s::jsonb,%s,%s,%s,%s, %s,%s,%s,%s,%s)
            """,
            (record.id, account_id, record.title, record.operation_type, record.status,
             record.price_amount, record.price_currency, record.reference_code,
             record.property_type, record.address, record.floor_unit,
             record.neighborhood, record.city, record.latitude, record.longitude,
             record.expenses_amount, record.accepts_swap, record.covered_area_m2,
             record.total_area_m2, record.rooms, record.bathrooms, record.parking_spaces,
             record.property_age_years, record.construction_status,
             _json_list(record.photos), record.video_url, record.capture_source,
             record.captured_at, record.responsible_agent_id,
             # legacy mirror (WU3 columns; documented sync rule)
             record.address or record.neighborhood or record.city or record.title,
             updated_rooms(record), record.price_amount, record.price_currency,
             _legacy_availability(record.status)),
        )
        for amenity_id in intent["amenity_ids"]:
            await cur.execute("SELECT 1 FROM property_amenities WHERE id = %s",
                              (amenity_id,))
            if await cur.fetchone() is None:
                raise CrmCommandValidationError("amenity is not in the catalogue")
            await cur.execute(
                "INSERT INTO property_amenity_rel (property_id, amenity_id, client_account_id) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (record.id, amenity_id, account_id))
        return {"resource_id": record.id, "reference_code": record.reference_code,
                "status": record.status, "operation_type": record.operation_type,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload):
        return {"reference_code": payload["reference_code"], "status": payload["status"]}

    def _outbox_payload(self, payload):
        return {"resource_id": payload["resource_id"],
                "reference_code": payload["reference_code"]}


class UpdatePropertyCommand(PropertyCommandBase):
    operation = "property.update"
    outbox_event_type = "PropertyUpdated"

    async def execute_update(self, context: TenantContext | None, *,
                             property_id: str, changes: dict[str, Any],
                             idempotency_key: str) -> CrmCommandOutcome:
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"property_id": property_id,
                             "changes": _canonical(changes)},
            property_id=property_id, changes=changes,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        row = await self._lock_property(cur, intent["property_id"], account_id)
        try:
            current = _row_to_record(row)
        except CrmDomainError:
            raise CrmCommandValidationError("property row is not updatable") from None
        changes = intent["changes"]
        allowed = {"title", "operation_type", "price_amount", "price_currency",
                   "property_type", "address", "floor_unit", "neighborhood", "city",
                   "expenses_amount", "accepts_swap", "covered_area_m2",
                   "total_area_m2", "rooms", "bathrooms", "parking_spaces",
                   "property_age_years", "construction_status", "photos",
                   "video_url", "reference_code", "responsible_agent_id"}
        unknown = set(changes) - allowed
        if unknown:
            raise CrmCommandValidationError(
                f"unsupported property fields: {sorted(unknown)}")
        if not changes:
            raise CrmCommandValidationError("no changes requested")
        changes = _normalize_changes(changes)
        merged = {**_record_dict(current), **changes}
        try:
            updated = PropertyRecord(**merged)
        except CrmDomainError as error:
            raise CrmCommandValidationError(str(error)) from error
        if updated.status != current.status:
            raise CrmCommandValidationError("status changes go through property.status_change")
        if updated.reference_code != current.reference_code and updated.reference_code is not None:
            await cur.execute(
                "SELECT 1 FROM properties WHERE client_account_id = %s AND reference_code = %s "
                "AND id <> %s", (account_id, updated.reference_code, updated.id))
            if await cur.fetchone() is not None:
                raise CrmCommandValidationError(
                    "reference_code is already used in this account")
        if (updated.responsible_agent_id is not None
                and not await self._agent_has_membership(
                    cur, updated.responsible_agent_id, account_id)):
            raise CrmCommandDenied("responsible_agent_not_member")

        await cur.execute(
            """
            UPDATE properties SET
                title=%s, operation_type=%s, status=%s, price_amount=%s, price_currency=%s,
                reference_code=%s, property_type=%s, address=%s, floor_unit=%s,
                neighborhood=%s, city=%s, expenses_amount=%s, accepts_swap=%s,
                covered_area_m2=%s, total_area_m2=%s, rooms=%s, bathrooms=%s,
                parking_spaces=%s, property_age_years=%s, construction_status=%s,
                photos=%s::jsonb, video_url=%s, responsible_agent_id=%s,
                location=%s, bedrooms=%s, price=%s, currency=%s, availability=%s,
                updated_at = now()
            WHERE id = %s AND client_account_id = %s
            """,
            (updated.title, updated.operation_type, updated.status,
             updated.price_amount, updated.price_currency, updated.reference_code,
             updated.property_type, updated.address, updated.floor_unit,
             updated.neighborhood, updated.city, updated.expenses_amount,
             updated.accepts_swap, updated.covered_area_m2, updated.total_area_m2,
             updated.rooms, updated.bathrooms, updated.parking_spaces,
             updated.property_age_years, updated.construction_status,
             _json_list(updated.photos), updated.video_url, updated.responsible_agent_id,
             updated.address or updated.neighborhood or updated.city or updated.title,
             updated.rooms if updated.rooms is not None else row["bedrooms"],
             updated.price_amount, updated.price_currency,
             _legacy_availability(updated.status),
             updated.id, account_id),
        )
        return {"resource_id": updated.id, "changed_fields": sorted(changes),
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload):
        return {"changed_fields": payload["changed_fields"]}

    def _outbox_payload(self, payload):
        return {"resource_id": payload["resource_id"],
                "changed_fields": payload["changed_fields"]}


class ChangePropertyStatusCommand(PropertyCommandBase):
    operation = "property.status_change"
    outbox_event_type = "PropertyStatusChanged"

    async def execute_change(self, context: TenantContext | None, *, property_id: str,
                             to_status: str, idempotency_key: str) -> CrmCommandOutcome:
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"property_id": property_id, "to_status": to_status},
            property_id=property_id, to_status=to_status,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        to_status = intent["to_status"]
        if to_status not in PROPERTY_STATUSES:
            raise CrmCommandValidationError("target status is invalid")
        row = await self._lock_property(cur, intent["property_id"], account_id)
        try:
            next_property_status(row["status"], to_status)  # domain machine guard
        except CrmDomainError:
            raise CrmCommandDenied("property_status_transition_not_allowed") from None
        await cur.execute(
            "UPDATE properties SET status = %s, availability = %s, updated_at = now() "
            "WHERE id = %s AND client_account_id = %s AND status = %s",
            (to_status, _legacy_availability(to_status), intent["property_id"],
             account_id, row["status"]),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("property_status_transition_not_allowed")
        return {"resource_id": intent["property_id"], "previous_status": row["status"],
                "status": to_status, "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload):
        return {"previous_status": payload["previous_status"], "status": payload["status"]}

    def _outbox_payload(self, payload):
        return {"resource_id": payload["resource_id"],
                "previous_status": payload["previous_status"],
                "status": payload["status"]}


class LinkContactToPropertyCommand(PropertyCommandBase):
    operation = "property.link_contact"
    outbox_event_type = "PropertyContactLinked"

    async def execute_link(self, context: TenantContext | None, *, property_id: str,
                           contact_id: str, role: str,
                           idempotency_key: str) -> CrmCommandOutcome:
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"property_id": property_id, "contact_id": contact_id,
                             "role": role},
            property_id=property_id, contact_id=contact_id, role=role,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await self._lock_property(cur, intent["property_id"], account_id)
        await cur.execute(
            "SELECT 1 FROM contacts WHERE id = %s AND client_account_id = %s",
            (intent["contact_id"], account_id))
        if await cur.fetchone() is None:
            raise CrmCommandDenied("contact_not_found_in_scope")
        await cur.execute(
            "INSERT INTO property_contacts (property_id, contact_id, role, client_account_id) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (property_id, contact_id, role) DO NOTHING",
            (intent["property_id"], intent["contact_id"], intent["role"], account_id))
        return {"resource_id": intent["property_id"],
                "contact_id": intent["contact_id"], "role": intent["role"],
                "linked": cur.rowcount == 1, "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload):
        return {"contact_id": payload["contact_id"], "role": payload["role"]}

    def _outbox_payload(self, payload):
        return {"resource_id": payload["resource_id"],
                "contact_id": payload["contact_id"], "role": payload["role"]}


class RegisterPropertyInterestCommand(PropertyCommandBase):
    """Lightweight interest marker; NEVER creates a lead. Idempotent per
    (property, contact): re-registering shares the canonical first result."""

    operation = "property.register_interest"
    audit_resource_type = "property_interest"
    outbox_event_type = "PropertyInterestRegistered"

    async def execute_register(self, context: TenantContext | None, *, property_id: str,
                               contact_id: str, origin: str,
                               idempotency_key: str) -> CrmCommandOutcome:
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"property_id": property_id, "contact_id": contact_id,
                             "origin": origin},
            property_id=property_id, contact_id=contact_id, origin=origin,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await self._lock_property(cur, intent["property_id"], account_id)
        await cur.execute(
            "SELECT 1 FROM contacts WHERE id = %s AND client_account_id = %s",
            (intent["contact_id"], account_id))
        if await cur.fetchone() is None:
            raise CrmCommandDenied("contact_not_found_in_scope")
        interest_id = str(uuid4())
        await cur.execute(
            "INSERT INTO property_interests "
            "(id, property_id, contact_id, origin, client_account_id) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (property_id, contact_id) DO NOTHING",
            (interest_id, intent["property_id"], intent["contact_id"],
             intent["origin"], account_id))
        if cur.rowcount == 0:
            # existing pair: return its canonical identity, no new effects
            await cur.execute(
                "SELECT id, origin FROM property_interests "
                "WHERE property_id = %s AND contact_id = %s AND client_account_id = %s",
                (intent["property_id"], intent["contact_id"], account_id))
            existing = await cur.fetchone()
            return {"resource_id": intent["property_id"],
                    "interest_id": str(existing["id"]), "origin": existing["origin"],
                    "created": False, "correlation_id": context.correlation_id}
        return {"resource_id": intent["property_id"], "interest_id": interest_id,
                "origin": intent["origin"], "created": True,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload):
        return {"interest_id": payload["interest_id"], "created": payload["created"]}

    def _outbox_payload(self, payload):
        return {"resource_id": payload["resource_id"],
                "interest_id": payload["interest_id"]}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _json_list(items: list[str]) -> str:
    return json.dumps(items)


def _normalize_changes(changes: dict[str, Any]) -> dict[str, Any]:
    """JSON bodies carry numbers as strings/floats: coerce the Decimal-typed
    fields so the domain validation sees real Decimals (no silent defaults)."""
    decimal_fields = {"price_amount", "expenses_amount", "covered_area_m2",
                      "total_area_m2", "latitude", "longitude"}
    normalized = dict(changes)
    for field_name in decimal_fields & set(normalized):
        value = normalized[field_name]
        if value is None or isinstance(value, Decimal):
            continue
        try:
            normalized[field_name] = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise CrmCommandValidationError(
                f"{field_name} is not a valid number") from error
    return normalized


def updated_rooms(record: PropertyRecord) -> int:
    # legacy bedrooms mirror: rooms is the enriched field; fall back to a
    # neutral 0 for records created without room data.
    return record.rooms if record.rooms is not None else 0


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    return str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value


def _record_canonical(record: PropertyRecord) -> dict[str, Any]:
    return {k: v for k, v in _record_dict(record).items()
            if v is not None and k != "captured_at"}


def _record_dict(record: PropertyRecord) -> dict[str, Any]:
    # Decimals stay Decimal: the merge result is re-validated by the domain
    # dataclass (canonical_hash serializes them via default=str anyway).
    return {f: getattr(record, f) for f in record.__dataclass_fields__}

def _row_to_record(row: dict[str, Any]) -> PropertyRecord:
    from decimal import Decimal as D
    photos = row.get("photos")
    if isinstance(photos, str):
        photos = _json.loads(photos)
    return PropertyRecord(
        id=str(row["id"]), client_account_id=str(row["client_account_id"]),
        title=row["title"], operation_type=row["operation_type"],
        status=row["status"], price_amount=row["price_amount"],
        price_currency=row["price_currency"],
        reference_code=row.get("reference_code"),
        property_type=row.get("property_type"), address=row.get("address"),
        floor_unit=row.get("floor_unit"), neighborhood=row.get("neighborhood"),
        city=row.get("city"),
        latitude=row.get("latitude"), longitude=row.get("longitude"),
        expenses_amount=row.get("expenses_amount"),
        accepts_swap=bool(row.get("accepts_swap")),
        covered_area_m2=row.get("covered_area_m2"),
        total_area_m2=row.get("total_area_m2"), rooms=row.get("rooms"),
        bathrooms=row.get("bathrooms"), parking_spaces=row.get("parking_spaces"),
        property_age_years=row.get("property_age_years"),
        construction_status=row.get("construction_status"),
        photos=list(photos or []), video_url=row.get("video_url"),
        capture_source=row.get("capture_source"),
        captured_at=row.get("captured_at"),
        responsible_agent_id=str(row["responsible_agent_id"]) if row.get("responsible_agent_id") else None,
    )

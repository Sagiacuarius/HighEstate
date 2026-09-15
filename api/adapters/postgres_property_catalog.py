"""Tenant-scoped PostgreSQL repositories for the property catalog (WU11).

Extends — never replaces — the accepted PostgresPropertyRepository (WU3):
legacy columns stay writable by the accepted code paths. These repositories
cover the 005 tables/columns with the same mandatory patterns:
_require(context) guard, client_account_id predicate on every statement from
the SERVER context, composite same-account FKs enforced by the schema and
re-checked here, translation of sqlstates into non-enumerating domain errors.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg

from api.adapters.postgres_commercial_crm import (
    CrmNotFoundError,
    CrossTenantAccessDenied,
    _require,
    _translate,
)
from api.adapters.postgres_core import PostgresRunner
from api.domain.property_catalog import (
    PropertyAmenity,
    PropertyContactLink,
    PropertyInterest,
    PropertyRecord,
)

# Columns owned by the WU11 command layer (005). Legacy columns (title etc. are
# shared; location/bedrooms/price/currency/availability remain WU3-owned and
# are kept in sync by CreateProperty/UpdateProperty writes, documented).
_PROPERTY_COLUMNS = """
    id, client_account_id, title, operation_type, status, price_amount,
    price_currency, reference_code, property_type, address, floor_unit,
    neighborhood, city, latitude, longitude, expenses_amount, accepts_swap,
    covered_area_m2, total_area_m2, rooms, bathrooms, parking_spaces,
    property_age_years, construction_status, photos, video_url,
    capture_source, captured_at, responsible_agent_id
"""


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _record(row: dict[str, Any]) -> PropertyRecord:
    photos = row.get("photos")
    if isinstance(photos, str):
        photos = json.loads(photos)
    return PropertyRecord(
        id=str(row["id"]),
        client_account_id=str(row["client_account_id"]),
        title=row["title"],
        operation_type=row["operation_type"],
        status=row["status"],
        price_amount=_dec(row["price_amount"]),
        price_currency=row["price_currency"],
        reference_code=row.get("reference_code"),
        property_type=row.get("property_type"),
        address=row.get("address"),
        floor_unit=row.get("floor_unit"),
        neighborhood=row.get("neighborhood"),
        city=row.get("city"),
        latitude=_dec(row.get("latitude")),
        longitude=_dec(row.get("longitude")),
        expenses_amount=_dec(row.get("expenses_amount")),
        accepts_swap=bool(row.get("accepts_swap")),
        covered_area_m2=_dec(row.get("covered_area_m2")),
        total_area_m2=_dec(row.get("total_area_m2")),
        rooms=row.get("rooms"),
        bathrooms=row.get("bathrooms"),
        parking_spaces=row.get("parking_spaces"),
        property_age_years=row.get("property_age_years"),
        construction_status=row.get("construction_status"),
        photos=list(photos or []),
        video_url=row.get("video_url"),
        capture_source=row.get("capture_source"),
        captured_at=row.get("captured_at"),
        responsible_agent_id=str(row["responsible_agent_id"]) if row.get("responsible_agent_id") else None,
    )


class PropertyCatalogRepository:
    """list/filter + get + upsert of the enriched record + status change."""

    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def get(self, context, property_id: str) -> PropertyRecord | None:
        account_id = _require(context)
        rows = await self._runner.query(
            f"SELECT * FROM properties WHERE id = %s AND client_account_id = %s",
            (property_id, account_id),
        )
        return _record(rows[0]) if rows else None

    async def list_filtered(self, context, *, status: str | None = None,
                            property_type: str | None = None,
                            city: str | None = None,
                            price_min: Decimal | None = None,
                            price_max: Decimal | None = None) -> list[PropertyRecord]:
        account_id = _require(context)
        where = ["client_account_id = %s"]
        params: list[Any] = [account_id]
        if status is not None:
            where.append("status = %s"); params.append(status)
        if property_type is not None:
            where.append("property_type = %s"); params.append(property_type)
        if city is not None:
            where.append("lower(city) = lower(%s)"); params.append(city)
        if price_min is not None:
            where.append("price_amount >= %s"); params.append(price_min)
        if price_max is not None:
            where.append("price_amount <= %s"); params.append(price_max)
        rows = await self._runner.query(
            f"SELECT * FROM properties WHERE {' AND '.join(where)} "
            "ORDER BY created_at, id",
            tuple(params),
        )
        return [_record(r) for r in rows]

    async def upsert(self, context, record: PropertyRecord) -> PropertyRecord:
        account_id = _require(context, write=True)
        if record.client_account_id != account_id:
            raise CrossTenantAccessDenied("entity scope does not match context")
        try:
            await self._runner.execute(
                f"""
                INSERT INTO properties ({_PROPERTY_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title, operation_type = EXCLUDED.operation_type,
                    status = EXCLUDED.status, price_amount = EXCLUDED.price_amount,
                    price_currency = EXCLUDED.price_currency,
                    reference_code = EXCLUDED.reference_code,
                    property_type = EXCLUDED.property_type,
                    address = EXCLUDED.address, floor_unit = EXCLUDED.floor_unit,
                    neighborhood = EXCLUDED.neighborhood, city = EXCLUDED.city,
                    latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude,
                    expenses_amount = EXCLUDED.expenses_amount,
                    accepts_swap = EXCLUDED.accepts_swap,
                    covered_area_m2 = EXCLUDED.covered_area_m2,
                    total_area_m2 = EXCLUDED.total_area_m2,
                    rooms = EXCLUDED.rooms, bathrooms = EXCLUDED.bathrooms,
                    parking_spaces = EXCLUDED.parking_spaces,
                    property_age_years = EXCLUDED.property_age_years,
                    construction_status = EXCLUDED.construction_status,
                    photos = EXCLUDED.photos, video_url = EXCLUDED.video_url,
                    capture_source = EXCLUDED.capture_source,
                    captured_at = EXCLUDED.captured_at,
                    responsible_agent_id = EXCLUDED.responsible_agent_id,
                    updated_at = now()
                WHERE properties.client_account_id = EXCLUDED.client_account_id
                """,
                (record.id, account_id, record.title, record.operation_type,
                 record.status, record.price_amount, record.price_currency,
                 record.reference_code, record.property_type, record.address,
                 record.floor_unit, record.neighborhood, record.city,
                 record.latitude, record.longitude, record.expenses_amount,
                 record.accepts_swap, record.covered_area_m2, record.total_area_m2,
                 record.rooms, record.bathrooms, record.parking_spaces,
                 record.property_age_years, record.construction_status,
                 json.dumps(record.photos), record.video_url,
                 record.capture_source, record.captured_at,
                 record.responsible_agent_id),
            )
        except psycopg.Error as error:
            raise _translate(error) from error
        saved = await self.get(context, record.id)
        if saved is None:
            raise CrmNotFoundError("property is not available in this account scope")
        return saved

    async def set_status(self, context, property_id: str, new_status: str) -> PropertyRecord | None:
        """Guarded write: the caller (command) already validated the machine;
        SQL re-checks the current status for lost-update safety."""
        account_id = _require(context, write=True)
        await self._runner.execute(
            "UPDATE properties SET status = %s, updated_at = now() "
            "WHERE id = %s AND client_account_id = %s",
            (new_status, property_id, account_id),
        )
        return await self.get(context, property_id)

    async def reference_taken(self, context, reference_code: str,
                              *, excluding_id: str | None = None) -> bool:
        account_id = _require(context)
        sql = ("SELECT 1 FROM properties WHERE client_account_id = %s "
               "AND reference_code = %s")
        params: list[Any] = [account_id, reference_code]
        if excluding_id is not None:
            sql += " AND id <> %s"; params.append(excluding_id)
        rows = await self._runner.query("SELECT EXISTS (" + sql + ") AS taken", tuple(params))
        return bool(rows and rows[0]["taken"])


class PropertyAmenityRepository:
    """Global vocabulary + M:N attach/detach gated by property scope."""

    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def list_amenities(self, context) -> list[PropertyAmenity]:
        _require(context)  # catalogue read requires an authenticated scope
        rows = await self._runner.query(
            "SELECT id, name FROM property_amenities ORDER BY name, id")
        return [PropertyAmenity(id=str(r["id"]), name=r["name"]) for r in rows]

    async def attach(self, context, property_id: str, amenity_id: str) -> bool:
        """True when the link was created; False when it already existed."""
        account_id = _require(context, write=True)
        exists = await self._runner.query(
            "SELECT 1 FROM properties WHERE id = %s AND client_account_id = %s",
            (property_id, account_id))
        if not exists:
            raise CrmNotFoundError("property is not available in this account scope")
        affected = await self._runner.execute(
            "INSERT INTO property_amenity_rel (property_id, amenity_id, client_account_id) "
            "VALUES (%s, %s, %s) ON CONFLICT (property_id, amenity_id) DO NOTHING",
            (property_id, amenity_id, account_id))
        return affected == 1

    async def detach(self, context, property_id: str, amenity_id: str) -> bool:
        account_id = _require(context, write=True)
        affected = await self._runner.execute(
            "DELETE FROM property_amenity_rel "
            "WHERE property_id = %s AND amenity_id = %s AND client_account_id = %s",
            (property_id, amenity_id, account_id))
        return affected == 1

    async def for_property(self, context, property_id: str) -> list[PropertyAmenity]:
        account_id = _require(context)
        rows = await self._runner.query(
            """
            SELECT a.id, a.name FROM property_amenity_rel r
            JOIN property_amenities a ON a.id = r.amenity_id
            WHERE r.property_id = %s AND r.client_account_id = %s
            ORDER BY a.name, a.id
            """,
            (property_id, account_id))
        return [PropertyAmenity(id=str(r["id"]), name=r["name"]) for r in rows]


class PropertyContactRepository:
    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def link(self, context, link: PropertyContactLink) -> bool:
        """True when created, False when the exact (property, contact, role)
        already existed. Both FKs are composite same-account: a foreign
        contact/property raises a relationship violation before any write."""
        account_id = _require(context, write=True)
        if link.client_account_id != account_id:
            raise CrossTenantAccessDenied("entity scope does not match context")
        affected = await self._runner.execute(
            """
            INSERT INTO property_contacts (property_id, contact_id, role, client_account_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (property_id, contact_id, role) DO NOTHING
            """,
            (link.property_id, link.contact_id, link.role, account_id))
        return affected == 1

    async def for_property(self, context, property_id: str) -> list[PropertyContactLink]:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM property_contacts WHERE property_id = %s "
            "AND client_account_id = %s ORDER BY role, contact_id",
            (property_id, account_id))
        return [_contact_link(r) for r in rows]

    async def for_contact(self, context, contact_id: str) -> list[PropertyContactLink]:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM property_contacts WHERE contact_id = %s "
            "AND client_account_id = %s ORDER BY linked_at, property_id",
            (contact_id, account_id))
        return [_contact_link(r) for r in rows]


def _contact_link(row: dict[str, Any]) -> PropertyContactLink:
    return PropertyContactLink(
        property_id=str(row["property_id"]),
        contact_id=str(row["contact_id"]),
        role=row["role"],
        client_account_id=str(row["client_account_id"]),
        linked_at=row.get("linked_at"),
    )


class PropertyInterestRepository:
    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def register(self, context, interest: PropertyInterest) -> bool:
        """True when created; False when the (property, contact) pair already
        exists (idempotent per unique constraint; never creates a lead)."""
        account_id = _require(context, write=True)
        if interest.client_account_id != account_id:
            raise CrossTenantAccessDenied("entity scope does not match context")
        affected = await self._runner.execute(
            """
            INSERT INTO property_interests
                (id, property_id, contact_id, origin, client_account_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (property_id, contact_id) DO NOTHING
            """,
            (interest.id, interest.property_id, interest.contact_id,
             interest.origin, account_id))
        return affected == 1

    async def for_property(self, context, property_id: str) -> list[PropertyInterest]:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM property_interests WHERE property_id = %s "
            "AND client_account_id = %s ORDER BY created_at, id",
            (property_id, account_id))
        return [_interest(r) for r in rows]

    async def for_contact(self, context, contact_id: str) -> list[PropertyInterest]:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM property_interests WHERE contact_id = %s "
            "AND client_account_id = %s ORDER BY created_at, id",
            (contact_id, account_id))
        return [_interest(r) for r in rows]


def _interest(row: dict[str, Any]) -> PropertyInterest:
    return PropertyInterest(
        id=str(row["id"]),
        property_id=str(row["property_id"]),
        contact_id=str(row["contact_id"]),
        origin=row["origin"],
        client_account_id=str(row["client_account_id"]),
        created_at=row.get("created_at"),
    )

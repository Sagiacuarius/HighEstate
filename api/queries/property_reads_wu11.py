"""Versioned tenant-scoped READ services for the property catalog (WU11).

Same pattern as the accepted WU6 reads: psycopg stays in the SQL adapter, the
service is provider-neutral, every count/list carries the mandatory
client_account_id predicate derived from the server context, and exposed
numeric values serialize as strings for decimal fidelity. Filters are
validated BEFORE SQL and reject unknown enum values (400), never silently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from api.adapters.postgres_property_catalog import (
    PropertyAmenityRepository,
    PropertyCatalogRepository,
    PropertyContactRepository,
    PropertyInterestRepository,
)
from api.queries.crm_reads import CrmReadNotFound, CrmReadValidation
from api.tenant_boundary import TenantContext


def _iso(value: datetime | None) -> str | None:
    return (value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z") if value else None)


def _str(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value


def property_view(record: Any) -> dict[str, Any]:
    return {
        "property_id": record.id,
        "reference_code": record.reference_code,
        "title": record.title,
        "operation_type": record.operation_type,
        "status": record.status,
        "property_type": record.property_type,
        "price_amount": _str(record.price_amount),
        "price_currency": record.price_currency,
        "expenses_amount": _str(record.expenses_amount),
        "address": record.address,
        "floor_unit": record.floor_unit,
        "neighborhood": record.neighborhood,
        "city": record.city,
        "latitude": _str(record.latitude),
        "longitude": _str(record.longitude),
        "accepts_swap": record.accepts_swap,
        "covered_area_m2": _str(record.covered_area_m2),
        "total_area_m2": _str(record.total_area_m2),
        "rooms": record.rooms,
        "bathrooms": record.bathrooms,
        "parking_spaces": record.parking_spaces,
        "property_age_years": record.property_age_years,
        "construction_status": record.construction_status,
        "photos": list(record.photos),
        "video_url": record.video_url,
        "capture_source": record.capture_source,
        "captured_at": _iso(record.captured_at),
        "responsible_agent_id": record.responsible_agent_id,
    }


def _validate_choice(raw: str | None, allowed: tuple[str, ...], label: str) -> str | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if value not in allowed:
        raise CrmReadValidation(f"{label} must be one of: {', '.join(allowed)}")
    return value


def _validate_price(raw: str | None, label: str) -> Decimal | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as error:
        raise CrmReadValidation(f"{label} must be a number") from error
    if value < 0:
        raise CrmReadValidation(f"{label} must be >= 0")
    return value


def _validate_text_filter(raw: str | None, label: str, max_len: int = 120) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value or len(value) > max_len or value != raw:
        raise CrmReadValidation(f"{label} is invalid")
    return value


class PropertyReadService:
    def __init__(self, runner, *, properties: PropertyCatalogRepository,
                 amenities: PropertyAmenityRepository,
                 links: PropertyContactRepository,
                 interests: PropertyInterestRepository):
        self._runner = runner
        self._properties = properties
        self._amenities = amenities
        self._links = links
        self._interests = interests

    async def _server_meta(self, context: TenantContext) -> dict[str, Any]:
        rows = await self._runner.query("SELECT now() AS now")
        now = rows[0]["now"].astimezone(timezone.utc)
        return {
            "account_id": context.client_account.id,
            "as_of": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "correlation_id": context.correlation_id,
        }

    async def list_properties(self, context: TenantContext,
                              query: dict[str, str | None]) -> dict[str, Any]:
        from api.domain.property_catalog import PROPERTY_STATUSES, PROPERTY_TYPES
        status = _validate_choice(query.get("status"), PROPERTY_STATUSES, "status")
        property_type = _validate_choice(query.get("property_type"), PROPERTY_TYPES,
                                         "property_type")
        city = _validate_text_filter(query.get("city"), "city")
        price_min = _validate_price(query.get("price_min"), "price_min")
        price_max = _validate_price(query.get("price_max"), "price_max")
        if price_min is not None and price_max is not None and price_min > price_max:
            raise CrmReadValidation("price_min must be <= price_max")
        records = await self._properties.list_filtered(
            context, status=status, property_type=property_type, city=city,
            price_min=price_min, price_max=price_max)
        items = [property_view(r) for r in records]
        meta = await self._server_meta(context)
        return {"items": items, "count": len(items),
                "filters": {"status": status, "property_type": property_type,
                            "city": city,
                            "price_min": _str(price_min), "price_max": _str(price_max)},
                **meta}

    async def property_detail(self, context: TenantContext,
                              property_id: str) -> dict[str, Any]:
        record = await self._properties.get(context, property_id)
        if record is None:
            raise CrmReadNotFound("property is not available in this account scope")
        amenities = await self._amenities.for_property(context, property_id)
        links = await self._links.for_property(context, property_id)
        interests = await self._interests.for_property(context, property_id)
        meta = await self._server_meta(context)
        return {
            **property_view(record),
            "amenities": [{"amenity_id": a.id, "name": a.name} for a in amenities],
            "contacts": [{"contact_id": l.contact_id, "role": l.role,
                          "linked_at": _iso(l.linked_at)} for l in links],
            "interests": [{"interest_id": i.id, "contact_id": i.contact_id,
                           "origin": i.origin, "created_at": _iso(i.created_at)}
                          for i in interests],
            **meta,
        }

    async def contact_properties(self, context: TenantContext,
                                 contact_id: str) -> dict[str, Any]:
        links = await self._links.for_contact(context, contact_id)
        interests = await self._interests.for_contact(context, contact_id)
        property_ids = [l.property_id for l in links] + [i.property_id for i in interests]
        items = []
        seen: set[str] = set()
        for pid in property_ids:
            if pid in seen:
                continue
            seen.add(pid)
            record = await self._properties.get(context, pid)
            if record is not None:
                view = property_view(record)
                view["relation"] = [
                    *( ["linked:" + l.role for l in links if l.property_id == pid]),
                    *(["interested"] if any(i.property_id == pid for i in interests) else []),
                ]
                items.append(view)
        meta = await self._server_meta(context)
        return {"contact_id": contact_id, "items": items, "count": len(items), **meta}

    async def amenities(self, context: TenantContext) -> dict[str, Any]:
        rows = await self._amenities.list_amenities(context)
        meta = await self._server_meta(context)
        return {"items": [{"amenity_id": a.id, "name": a.name} for a in rows],
                "count": len(rows), **meta}

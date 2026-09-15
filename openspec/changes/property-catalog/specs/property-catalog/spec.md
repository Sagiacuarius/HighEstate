# Property Catalog Specification

## Purpose

Define the enriched, queryable property entity used by the real-estate vertical: a first-class catalog over the extended `properties` table (migration `005`), with amenities, role-based contact links, lightweight interests, and a commercial status machine. All behavior MUST stay inside the `Agency → ClientAccount` isolation and the accepted command/read patterns of `mvp-commercial-demo` (baseline WU1–WU6).

## Requirements

### Requirement: Extend, never recreate

The catalog MUST be delivered by forward-only migration `005_property_catalog` applied after `004_commercial_crm`; migrations `001–004` MUST NOT be modified. The migration MUST preserve existing rows by keeping legacy columns and backfilling `status`, `price_amount`, `price_currency`, `address`, and `rooms` from them; the documented mapping is `unavailable → paused` and non-`USD`/`ARS` legacy currency → `USD`.

#### Scenario: 005 applies over data written by 004
- GIVEN a base with `001–004` applied and legacy properties seeded
- WHEN `005` is applied
- THEN every legacy row carries a non-null commercial `status`, `price_amount`, and `price_currency` derived from its legacy values
- AND the `001–004` migration files and accepted repositories remain unchanged and functional

### Requirement: Enriched property record

Each property MUST expose the catalogue attributes (reference code unique per client account, operation type `sale|rent|temporary_rent`, property type, address/floor/neighborhood/city, latitude/longitude fields without map behaviour, price amount/currency `USD|ARS`, expenses, swap flag, areas, rooms/bathrooms/parking, age, construction status, photos array, video URL, capture source, captured-at, responsible agent). Enum-valued fields MUST be validated in the provider-neutral domain layer before SQL.

#### Scenario: Reference code is per-account
- GIVEN accounts A and B with the same `reference_code`
- WHEN each creates its property
- THEN both succeed because uniqueness is scoped per `client_account_id`
- AND a second create inside one account with a used code fails with the conflict envelope

### Requirement: Commercial status machine

Property commercial status MUST be one of `capturing|available|reserved|sold|rented|paused`. Transitions MUST follow `capturing → available → reserved → sold|rented`; `paused` MUST be reachable from any non-terminal status and MUST be reversible to `capturing`, `available`, or `reserved`; `sold` and `rented` MUST be terminal. Illegal transitions MUST be refused atomically with a redacted denial audit and no mutation.

#### Scenario: Illegal jump rolls back
- GIVEN a property in `capturing`
- WHEN `ChangePropertyStatus` requests `sold`
- THEN the command fails, the stored status remains `capturing`, and exactly one `outcome=denied` audit record exists with no success audit or outbox event

### Requirement: Catalogue relations with account isolation

`property_amenities` MUST be a shared name catalogue; per-account links live in `property_amenity_rel`. `property_contacts` MUST bind a property, a contact, and a role (`owner|tenant|referrer`). `property_interests` MUST bind property and contact with origin `ai_agent|manual`, MUST be UNIQUE per `(property_id, contact_id)`, and MUST carry `client_account_id` with composite FKs `(child_id, client_account_id)` to the accepted parents, replicating the `004` isolation pattern.

#### Scenario: Cross-account link fails closed
- GIVEN property in A and contact in B
- WHEN `LinkContactToProperty` (or interest registration) targets the pair with context A
- THEN the operation is denied with the non-enumerating error shape, B is unchanged, and no link row exists

### Requirement: Interests never create leads

Registering a property interest MUST NOT create a lead or set `leads.selected_property_id`. The lead↔property link MUST only be set by a future visit/negotiation conversion flow. Replaying an identical interest command MUST return the canonical result via idempotency; a distinct contact on the same property MUST create a separate interest.

#### Scenario: Interest replay is a no-op
- GIVEN a registered interest for (property P, contact C)
- WHEN the same command is replayed with the same actor, operation, and key
- THEN the canonical result returns, exactly one interest row exists, and the account's lead count is unchanged

### Requirement: Atomic catalog commands

`CreateProperty`, `UpdateProperty`, `ChangePropertyStatus`, `LinkContactToProperty`, and `RegisterPropertyInterest` MUST run through the accepted WU5 command machinery: canonical payload hash, reserve/replay/conflict idempotency, single transaction, redacted denial audit, and outbox events published only after commit. `UpdateProperty` MUST apply partial merges, keep legacy mirror columns consistent with enriched values, and MUST NOT change status (status only via `ChangePropertyStatus`).

#### Scenario: Conflicting reuse fails without mutation
- GIVEN a successful create with key K
- WHEN a different payload reuses K for the same actor and operation
- THEN the request fails as an idempotency conflict and no second property exists

### Requirement: Versioned tenant-scoped reads

The read surface MUST expose `GET /v1/crm/properties` (filters: status, property_type, min/max price, city), `GET /v1/crm/properties/{id}` (detail with amenities, linked contacts, interests), `GET /v1/crm/contacts/{id}/properties`, and the amenity catalogue for filter vocabulary. Routes MUST NOT accept any account identifier; scope MUST come from the server-resolved `TenantContext`. Missing-in-scope and foreign ids MUST share one indistinguishable 404 envelope shape.

#### Scenario: Filters derive from persisted data
- GIVEN the seeded catalog for account A
- WHEN listing with `status=available` and a price range
- THEN only A's matching rows appear, counts match direct SQL, and a forged `clientAccountId` query parameter cannot change the resolved scope

### Requirement: Deterministic marked seed

The demo seed MUST add four enriched properties across distinct statuses (at least one each of capturing, available, reserved, and a terminal sold/rented) with varied amenities, plus catalogue-consistent amenity rows, all carrying `demo_seed_key` markers. Seed/reset MUST keep the accepted ownership contract: only marked rows of the designated demo accounts are deleted or recreated; unmarked rows block re-seed fail-closed.

#### Scenario: Seed re-apply converges
- GIVEN the enriched seed applied once
- WHEN it is re-applied inside the same account markers
- THEN row counts are identical and no duplicates exist

## Explicit No-goals

No map UI over lat/lng, no per-property market reports, no multi-titleholder UI, no lead auto-creation from interest, no legacy-column drop in this change, and no Kapso/WhatsApp, LLM, RLS, or productive auth.

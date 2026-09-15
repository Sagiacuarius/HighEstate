# Audit Screen Specification

## Purpose

Expose the durable `audit_records` trail (captured by accepted WU5/WU6/WU9/WU11 machinery) through a tenant-scoped, filtered read API and an operator dashboard view, without changing capture, schema, or adding roles. Baseline: `mvp-commercial-demo` + `property-catalog`.

## Behavior Definitions

The mapping between filter dimensions and stored audit fields is fixed (from the accepted writers, not invented):

- `contact_id`: rows where `resource_type='lead'` and `resource_id` is a lead of that contact (via `leads.contact_id`), OR rows whose `metadata->>'contact_id'` equals it (property link/interest writers).
- `property_id`: rows where `resource_type IN ('property','property_interest')` and `resource_id` equals the property (WU11 writers set `resource_id` to the property id), OR rows where `resource_type='lead'` and the lead's `selected_property_id` equals it.
- `user_id`: `actor_id`.
- `action`: exact match on `action`.
- `from`/`to`: inclusive `created_at` range.
- Pagination: `limit` (bounded, default applied server-side) + opaque keyset cursor over `(created_at, id)` DESC; no offset pagination.

## Requirements

### Requirement: Read-only exposure of the accepted trail

This change MUST NOT add tables, columns, migrations, or new audit writes. It MUST read `audit_records` as-is, reusing the accepted redaction guarantees (schema CHECK forbidding sensitive metadata keys, and the activity projection that never serializes raw `metadata` beyond the non-success `reason` class).

#### Scenario: No capture change
- GIVEN the accepted commands and harnesses
- WHEN this change is applied
- THEN every previously green audit scenario still passes unmodified
- AND no new code path inserts into `audit_records`

### Requirement: Server-derived tenant scope

Every audit query MUST filter `client_account_id = context.client_account.id` derived from the resolved `TenantContext`; rows with NULL `client_account_id` (cross-context) MUST remain invisible. No route parameter or query field MAY supply an account id, and a forged `clientAccountId` query parameter MUST NOT alter the resolved scope.

#### Scenario: Cross-context rows invisible
- GIVEN audit rows for account A, account B, and a NULL-account row
- WHEN the audit endpoint is called under the A demo session
- THEN only A's rows appear
- AND a request adding `clientAccountId=B` returns exactly the same A-scoped result

### Requirement: Filters match durable relations

The `contact_id` and `property_id` filters MUST be implemented with the mappings defined above and MUST NOT depend on columns that do not exist. Combining filters MUST intersect. An id that is foreign, malformed, or absent in scope MUST yield an empty result without disclosing whether it exists elsewhere (filters do not 404; only malformed uuid-shaped values fail 400).

#### Scenario: Contact filter follows the lead relation
- GIVEN a simulated inbound command for contact C that updated its lead
- WHEN auditing with `contact_id=C`
- THEN the `crm.inbound.process` row appears
- AND rows for other contacts' leads do not

#### Scenario: Property filter includes interest and lead links
- GIVEN a registered interest on property P by contact X and a lead with `selected_property_id=P`
- WHEN auditing with `property_id=P`
- THEN the `property.interest.register` row appears (resource_type `property_interest`, resource_id P)
- AND a contact-unrelated audit row does not

### Requirement: Keyset pagination

Responses MUST carry `limit`, a `next_cursor` when more rows exist, and stable ordering `created_at DESC, id DESC`. Replaying the same cursor MUST continue identically; a tampered or malformed cursor MUST fail closed with the versioned 400 envelope, never error.

#### Scenario: Paging has no duplicates or gaps
- GIVEN 7 visible audit rows and `limit=3`
- WHEN the client follows cursors to the end
- THEN it sees exactly 7 distinct rows in order
- AND the final page carries no `next_cursor`

### Requirement: Versioned response and error envelope

The response MUST be parsed by the versioned TS contract and MUST NOT include `metadata`, message bodies, or secrets. Invalid enum/date/uuid/range inputs, missing identity, and denied scope MUST use the accepted envelope with `X-Correlation-Id` consistent between header and body; unknown routes and wrong methods keep the accepted 404/405 discipline.

#### Scenario: Invalid range fails closed
- GIVEN a request with `from` after `to` or a non-ISO timestamp
- WHEN the audit endpoint processes it
- THEN a 400 `invalid_query` envelope returns with matching correlation
- AND no SQL executes with the unvalidated value

### Requirement: Operator dashboard view

The dashboard MUST expose an "Auditoría" view (navigation item alongside the existing "Actividad", which stays unmodified) showing a table of action, outcome, resource, actor, timestamp, and correlation, with filter controls for the six dimensions, values translated via the existing `statusLabel`, an explicit empty state, and error state rendering the envelope message with correlation. Cursor paging MUST be exposed as a "load more" control. The view MUST render only persisted API responses, never fixture data.

#### Scenario: Filters drive the view from the API
- GIVEN the seeded/operated demo base
- WHEN the operator filters by action `crm.property.status_change` and outcome is visible
- THEN the table shows exactly the API-filtered rows after reload
- AND the correlation id of a failed request is visible in the error state

## Explicit No-goals

Roles/permissions, CSV export, aggregate reports, new capture paths, new migration, per-row detail drill-down, consolidation of "Actividad" into "Auditoría" (deferred decision), Kapso/LLM/RLS/productive auth.

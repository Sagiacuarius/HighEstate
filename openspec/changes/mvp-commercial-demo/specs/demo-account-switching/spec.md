# Demo Account Switching Specification

## Purpose

Provide a local-only, visibly non-production identity and account-selection experience that demonstrates isolation between two seeded `ClientAccount` records.

## Requirements

### Requirement: Development demo identity

The system MUST provide a development-only demo user and visible user/account selector. The UI, API documentation, and runtime labels MUST state that the selector is a demo context mechanism, not production authentication, authorization, or session security. The server MUST still derive the effective `TenantContext` from accepted identity, membership, and tenant seams.

#### Scenario: Select account A or B
- GIVEN the local demo is running
- WHEN the viewer selects demo user/account A or B
- THEN the server establishes the corresponding authorized context
- AND the UI visibly identifies the selected demo account and development-only identity

#### Scenario: Selector cannot forge scope
- GIVEN the selector submits an account identifier without an authorized membership
- WHEN the context is resolved
- THEN the request fails closed, the prior valid context is preserved, and no cross-account data is returned

### Requirement: Deterministic seed and reset

Development seed/reset behavior MUST create exactly two visually distinct `ClientAccount` datasets with distinct properties, leads, conversations, pipeline state, and follow-ups. Reset MUST be explicit, deterministic, account-safe, and MUST NOT alter foundation migrations or represent fixture content as production data.

#### Scenario: Seed makes isolation visible
- GIVEN an empty local database
- WHEN the demo seed is applied
- THEN accounts A and B have distinguishable persisted records
- AND each account is reachable only through its authorized context

#### Scenario: Reset restores the baseline
- GIVEN either account has changed through the demo
- WHEN development reset is requested
- THEN both accounts return to their deterministic seed state
- AND no records from A appear in B or vice versa

### Requirement: Account switching isolates all surfaces

Switching context MUST affect every CRM query, command, repository predicate, cache/object key, event, audit/activity query, and simulator operation. The system MUST NOT perform implicit cross-account reads or writes.

#### Scenario: Switch A to B after a command
- GIVEN account A has a simulated inbound message and account B has different seed data
- WHEN the viewer switches from A to B and reloads Inbox, leads, properties, pipeline, dashboard, and agenda
- THEN B’s data and counts are shown, A’s new records are absent, and B remains unchanged

#### Scenario: Switch back preserves durable state
- GIVEN account B is selected after account A changed
- WHEN the viewer switches back to A and reloads
- THEN A’s prior persisted message, lead transition, follow-up, and activity remain visible

### Requirement: Safe failure and replay across accounts

Authorization failures, malformed simulator input, and idempotency conflicts MUST be non-enumerating and atomic. An idempotency key reused with a different account or payload MUST not apply either operation or mutate the active context.

#### Scenario: Foreign replay cannot mutate B
- GIVEN an event key already committed for account A
- WHEN it is replayed against account B or with a different payload
- THEN an idempotency conflict is returned, no B records are created, and no success audit/event is emitted

## Explicit No-goals

This change MUST NOT provide real authentication, production sessions, role administration, meetings, calendar integration, forms, automation builders, Kapso/WhatsApp, LLM/LangGraph, Docker, VPS, CI/CD, or deployment.

# Commercial CRM Demo Specification

## Purpose

Define the persisted, account-scoped CRM loop used by the local commercial demo. The demo MUST use PostgreSQL as system of record and MUST NOT treat mockup fixtures as live data.

## Requirements

### Requirement: Product CRM model and migration

The system MUST persist explicit account-scoped contacts, leads, properties, pipeline stages, conversations, messages, and follow-ups, with relationships and constraints sufficient to represent ownership, status, unread state, due time, and activity. A forward-only migration MUST run after `003_audit_outbox` and MUST NOT modify migrations `001`, `002`, or `003`.

#### Scenario: Migration preserves the foundation
- GIVEN migrations `001–003` are accepted
- WHEN the product migration is applied
- THEN existing tables and migration files remain unchanged
- AND product tables and account-leading constraints are available

### Requirement: Tenant-scoped repositories

Every product read and write MUST require the server-derived immutable `TenantContext`; repositories MUST derive `client_account_id` predicates only from that context, and MUST use the accepted transaction, idempotency, audit, and outbox seams. Client-supplied scope, resource IDs, or payload fields MUST NOT override context.

#### Scenario: Cross-account access fails closed
- GIVEN context A and records belonging to accounts A and B
- WHEN a read or write for B is attempted with context A
- THEN the operation is denied, account B is unchanged, and a sanitized denial is auditable

### Requirement: Normalized inbound and CRM commands

The system MUST accept a deterministic simulator event through a provider-neutral normalized inbound-message boundary. Processing MUST atomically create or update the contact/lead, persist conversation/message evidence, transition the lead to the defined stage, create the requested follow-up, audit the command, and publish its scoped outbox event. Replay with the same actor, operation, and idempotency key and identical payload MUST return the canonical result without duplicate effects; conflicting reuse MUST fail.

#### Scenario: Inbound message completes the loop
- GIVEN an account-scoped seeded contact and deterministic simulator payload
- WHEN the inbound command is submitted
- THEN the message, conversation, lead state, pipeline transition, follow-up, audit record, and outbox event are durable after commit

#### Scenario: Failure leaves no partial CRM state
- GIVEN a command that fails validation, authorization, or persistence
- WHEN processing ends
- THEN no CRM mutation, successful audit, or successful outbox event is committed

### Requirement: Read APIs and persisted UI

Versioned read APIs MUST expose dashboard counts and account-scoped views for Inbox, leads, properties, pipeline/stages, agenda/follow-ups, and activity. The Next.js UI MUST preserve the mockup’s navigation and visual language while rendering these persisted responses, not hard-coded fixture metrics.

#### Scenario: Reload shows durable results
- GIVEN the completed inbound loop
- WHEN the dashboard or Inbox is reloaded
- THEN the same account-scoped records and derived counts are displayed

### Requirement: Simulator controls

The UI MUST expose a clearly labeled development simulator button in Inbox, and an internal reproducible endpoint MUST accept the canonical deterministic payload. Neither surface MAY claim Kapso or WhatsApp connectivity.

#### Scenario: Repeated simulation is idempotent
- GIVEN the same selected account and simulator event
- WHEN the button or endpoint is invoked twice with the same idempotency key
- THEN one message and one command effect exist and both calls expose the same canonical result

### Requirement: CRM activity evidence

Successful and denied CRM commands MUST produce appropriately scoped, redacted audit/activity evidence with correlation identifiers; successful domain events MUST be published only after durable commit.

#### Scenario: Activity explains the command
- GIVEN a successful lead or follow-up command
- WHEN activity is queried for the selected account
- THEN the action, outcome, actor/context class, and correlation ID are visible without secrets or raw bodies

## Explicit No-goals

This change MUST NOT implement Kapso, WhatsApp delivery, provider credentials, LLM/LangGraph agents, real authentication, meetings, calendars, forms, automation builders, Docker, VPS, CI/CD, or deployment.

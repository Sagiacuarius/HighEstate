# Tenant Isolation Specification

## Purpose

Define the first product-owned tenancy slice: an administrator can switch between two `ClientAccount` tenants while the system proves authorization and prevents cross-account access.

## Requirements

### Requirement: Agency and account tenancy

The system MUST model `Agency` as the grouping boundary and `ClientAccount` as the minimum operational and data-isolation boundary. Tenant-scoped records MUST belong to exactly one client account.

#### Scenario: Two accounts are isolated within one agency

- GIVEN one agency owns accounts A and B
- WHEN account-scoped data is created for A
- THEN it MUST NOT be visible in B's account context

### Requirement: Explicit membership and authorized switching

The system MUST represent a user's membership and effective authorization for each client account explicitly. Agency membership alone MUST NOT authorize unrestricted access to every account. Switching MUST succeed only for an account the current user is authorized to access.

#### Scenario: Administrator switches to an authorized account

- GIVEN an administrator is explicitly a member of accounts A and B
- WHEN the administrator selects B
- THEN the system MUST establish an authorized context for B

#### Scenario: Unauthorized account selection is rejected

- GIVEN an administrator is not a member of account B
- WHEN the administrator requests B
- THEN the system MUST deny the switch and MUST NOT establish B context

### Requirement: Server-derived tenant context

The server MUST derive and revalidate `agency_id` and `client_account_id` from authenticated identity, membership, and the requested operation. A client-supplied scope MUST NEVER be authoritative.

#### Scenario: Forged scope cannot change context

- GIVEN an authorized context for A
- WHEN a request supplies B's `client_account_id`
- THEN the server MUST retain or reject the authorized context for A

### Requirement: Denied cross-account reads and writes

The system MUST deny reads, creates, updates, and deletes that resolve outside the authorized client account, including indirect or asynchronous operations. Denials MUST be observable as authorization failures without revealing protected data.

#### Scenario: Cross-account read and write are denied

- GIVEN the administrator is authorized only for A
- WHEN a request targets a record owned by B
- THEN both read and write attempts MUST be denied
- AND no B data MUST be returned or changed

### Requirement: First-slice evidence

The first slice MUST produce reviewable evidence of identity, memberships, selected account, resolved server context, allowed switching, and denied cross-account reads and writes. Visual account switching alone MUST NOT count as proof.

#### Scenario: Isolation evidence is reviewable

- GIVEN the two-account administrator journey is exercised
- WHEN evidence is reviewed
- THEN it MUST show successful authorized switching and explicit denied cross-account operations

### Requirement: Explicit non-goals

This change MUST NOT require CRM features, WhatsApp, operational agents, complete automations, meetings, or forms.

#### Scenario: Non-goal request remains outside the slice

- GIVEN a request for a CRM, WhatsApp, agent, automation, meeting, or form feature
- WHEN this change is planned
- THEN that request MUST remain out of scope

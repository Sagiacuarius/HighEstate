# WACRM Audit Governance Specification

## Purpose

Define the evidence-based gate for evaluating WACRM as a reference without allowing unverified upstream material to become a product dependency.

## Requirements

### Requirement: WACRM audit and adoption gate

Before WACRM code is copied, distributed, executed, or made a runtime dependency, the system's change record MUST document the source URL, owner or repository, exact revision, provenance, license compatibility and notices, security findings, persistence and authentication assumptions, architecture, extensibility, compatibility, and remediation ownership. The gate MUST record an adopt, adapt, replace, defer, or blocked decision per evaluated area.

#### Scenario: Complete audit permits controlled adoption

- GIVEN all required evidence is recorded and compatibility is accepted
- WHEN the gate is reviewed
- THEN adoption MAY proceed only within documented product-owned boundaries

#### Scenario: Missing or blocking evidence stops adoption

- GIVEN provenance, license, security, persistence, authentication, or compatibility evidence is missing or unacceptable
- WHEN adoption is requested
- THEN WACRM MUST remain study-only and MUST NOT be copied, distributed, or executed

### Requirement: Product-owned architecture boundaries

The product MUST retain ownership of tenancy, authorization, audit, and domain contracts. Its architecture MUST preserve dependency direction from presentation to application to domain ports; external WACRM, persistence, identity, and provider implementations MUST be replaceable adapters and MUST NOT define domain behavior.

#### Scenario: WACRM assumptions conflict with tenancy

- GIVEN an evaluated WACRM component assumes one workspace or lacks account authorization
- WHEN it is classified
- THEN it MUST be encapsulated or replaced rather than adopted into the product core

### Requirement: Standalone tenancy fallback

If the audit blocks adoption, the product MUST retain the audit record and continue with a standalone product-owned tenancy foundation. The fallback MUST preserve the same `Agency → ClientAccount` isolation, explicit membership, server-derived context, and cross-account denial requirements.

#### Scenario: Audit block activates fallback

- GIVEN the WACRM gate is blocked
- WHEN implementation planning continues
- THEN the WACRM intake MUST remain excluded and standalone tenancy MUST be the approved path

### Requirement: Auditability of the gate

The adoption decision MUST be traceable to evidence, reviewer, revision, date, unresolved risks, and rollback or remediation ownership. Unverified WACRM facts MUST NOT be stated as requirements.

#### Scenario: Reviewer traces an adoption decision

- GIVEN a component is marked adopt, adapt, replace, defer, or blocked
- WHEN a reviewer follows its record
- THEN the cited evidence and unresolved risks MUST be identifiable

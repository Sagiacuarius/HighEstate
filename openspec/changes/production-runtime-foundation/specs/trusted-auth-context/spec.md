# Trusted Authentication and Tenant Context Specification

## Purpose
Ensure identity and tenant authorization are derived server-side.

## Requirements

### Requirement: Trusted identity and context
The system MUST authenticate through a selected provider/method (open decision), map the trusted subject to a product-owned user and membership, and derive tenant context on the server. It MUST NOT trust client-supplied `userId`, `agencyId`, `accountId`, permissions, or tenant context.

#### Scenario: Authorized request resolves
- GIVEN a valid authenticated subject with membership in account A
- WHEN the request reaches an application handler
- THEN the server derives A and its agency and authorizes the operation

#### Scenario: Forged claims are rejected
- GIVEN a valid subject authorized for A and a request claiming B or elevated permissions
- WHEN the request is evaluated
- THEN the claim is ignored or rejected and no B operation occurs

#### Scenario: Missing membership fails closed
- GIVEN a valid subject with no membership in the requested account
- WHEN the subject requests that account
- THEN the response MUST be non-enumerating and MUST reveal neither membership nor account existence

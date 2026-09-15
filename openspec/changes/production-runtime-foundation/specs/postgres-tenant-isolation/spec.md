# PostgreSQL Tenant Isolation Specification

## Purpose
Prove isolation for one representative tenant-scoped resource against real PostgreSQL.

## Requirements

### Requirement: Database-enforced scoped access
Every representative resource row MUST contain a mandatory `client_account_id`, account-aware constraints, and query authorization derived from trusted context. Application checks MUST remain mandatory even if RLS is selected; service-role paths MUST NOT bypass ownership checks. The ORM or query layer remains an open implementation decision.

#### Scenario: Account A sees only A
- GIVEN resources exist in A and B and trusted context is A
- WHEN the resource is listed
- THEN only A rows are returned

#### Scenario: Forged identifier cannot cross scope
- GIVEN context A and a resource identifier belonging to B
- WHEN read, update, or delete is requested
- THEN no B data is returned or changed and the response MUST NOT enable enumeration via 404/403 differences

#### Scenario: Missing scope cannot write
- GIVEN a create request with no server-derived account context
- WHEN persistence is attempted
- THEN it MUST fail closed without inserting an unscoped row

# Persistent Agency and Membership Specification

## Purpose
Persist the Agency → ClientAccount hierarchy, users, identity links, and explicit memberships.

## Requirements

### Requirement: Durable tenant model
The system MUST persist each Agency, ClientAccount, User, identity link, and Membership with stable identifiers, foreign keys, lifecycle state, and account-scoped authorization data. Role matrix and invitation/recovery policy remain open decisions.

#### Scenario: Administrator has two memberships
- GIVEN one agency and accounts A and B
- WHEN an administrator is granted explicit memberships
- THEN both memberships persist and can be resolved after restart

#### Scenario: Invalid membership is rejected
- GIVEN a membership referencing a missing user or account
- WHEN persistence is attempted
- THEN the transaction MUST fail and leave no partial membership

#### Scenario: Duplicate membership is idempotent
- GIVEN an existing user-account membership
- WHEN the same membership is submitted again
- THEN the system MUST preserve one canonical membership and return a deterministic result

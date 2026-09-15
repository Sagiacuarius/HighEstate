# Audit Records and Transactional Outbox Specification

## Purpose
Provide durable accountability and reliable handoff for post-commit processing.

## Requirements

### Requirement: Audited committed events
The system MUST record actor, trusted agency/account context, operation, outcome, correlation identifier, and timestamp in an audit record, and MUST create an outbox event in the same transaction as the state change. Consumers MUST be idempotent.

#### Scenario: Successful mutation is audited
- GIVEN an authorized resource mutation
- WHEN its transaction commits
- THEN one audit record and one pending outbox event reference the committed change

#### Scenario: Rejected operation is accountable
- GIVEN an authentication or authorization denial
- WHEN the request ends
- THEN a redacted denial outcome is observable without exposing protected data

#### Scenario: Commit failure leaves no event
- GIVEN a transaction cannot commit
- WHEN the mutation is rolled back
- THEN no success audit or publishable success event claims completion

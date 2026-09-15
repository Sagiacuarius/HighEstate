# Transactions and Durable Idempotency Specification

## Purpose
Make authorized commands, mutations, replay records, and outcomes durable and coherent.

## Requirements

### Requirement: Atomic command outcome
The system MUST atomically coordinate authorization, domain mutation, idempotency reservation/result, audit linkage, and committed event linkage. It MUST NOT call external providers inside a long-running transaction.

#### Scenario: Retry replays one result
- GIVEN a successful command with actor, operation, and idempotency key K
- WHEN the same command is retried
- THEN the durable recorded result is returned without a second mutation

#### Scenario: Concurrent duplicate is safe
- GIVEN two concurrent identical commands using K
- WHEN both reach the command boundary
- THEN exactly one mutation and one durable result exist and both callers receive the canonical outcome

#### Scenario: Conflicting reuse fails
- GIVEN K is recorded for target A
- WHEN it is reused for a different target or payload
- THEN the command fails with a conflict and MUST NOT mutate either target

# Health, Readiness, and Redacted Logging Specification

## Purpose
Expose safe operational signals without leaking credentials or tenant data.

## Requirements

### Requirement: Safe health and structured logs
Services MUST expose separate liveness and readiness checks. Structured logs MUST include timestamp, level, service, environment, correlation ID, safe actor class/scope, outcome, and duration, and MUST redact credentials, tokens, raw content, and sensitive payloads.

#### Scenario: Healthy dependencies are ready
- GIVEN the process is live and required database checks pass
- WHEN liveness and readiness are queried
- THEN liveness and readiness both succeed without tenant data

#### Scenario: Database outage is distinguishable
- GIVEN the process remains running while PostgreSQL is unavailable
- WHEN readiness is queried
- THEN liveness succeeds, readiness fails, and the log identifies dependency failure without connection secrets

#### Scenario: Sensitive failure is redacted
- GIVEN an exception contains a token or request payload
- WHEN it is logged
- THEN the structured record contains correlation-safe metadata but no secret or raw payload

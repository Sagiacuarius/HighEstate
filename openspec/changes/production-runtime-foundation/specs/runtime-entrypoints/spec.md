# Production Runtime and Entrypoints Specification

## Purpose

Define the executable local Next.js and FastAPI boundaries without coupling domain code to transport. This specification is intentionally scoped: PostgreSQL and Docker Compose are later work-unit concerns, not requirements of Work Unit 1.

## Work Unit 1 boundary

Work Unit 1 covers only:

- Next.js entrypoint.
- FastAPI entrypoint.
- Versioned HTTP contracts.
- Startup/shutdown lifecycle.
- Local fail-fast configuration.
- Correlation/request IDs.
- Idempotency metadata.
- Versioned error envelopes.
- Local contract and lifecycle tests.

The following are explicitly outside Work Unit 1 and remain assigned to later work units:

- PostgreSQL and durable persistent tenancy: `persistent-tenancy`, `postgres-tenant-isolation`, and later persistence work.
- Docker Compose and environment packaging: `compose-environment`.
- Real authentication/trusted identity: `trusted-auth-context`.
- Durable transactions/idempotency: `transactional-idempotency`.
- Audit/outbox: `audit-outbox`.
- Health dependency checks and production logging: `health-logging`.
- CI/CD and deployment: `github-actions-deploy`.

## Requirements

### Requirement: Local service entrypoints

The system MUST provide documented local entrypoints for Next.js and FastAPI. The entrypoints MUST expose the versioned contracts, lifecycle behavior and request correlation defined here. Domain/application ports MUST remain independent of HTTP and framework code.

PostgreSQL, Docker Compose, persistent storage and production deployment are not prerequisites for this Work Unit; they are specified by later capabilities and work units.

#### Scenario: Local FastAPI start succeeds

- GIVEN valid local API configuration
- WHEN `python -m api.run` runs from the project root
- THEN FastAPI accepts local requests and exposes its advertised health endpoints

#### Scenario: Local Next.js start/build succeeds

- GIVEN explicit `RUNTIME_API_BASE_URL` configuration and workspace dependencies installed from the root lockfile
- WHEN the web workspace starts or builds
- THEN the minimal Next.js entrypoint is available without product UI

#### Scenario: Invalid startup fails safely

- GIVEN a required service configuration value is missing or invalid
- WHEN that service starts
- THEN it MUST fail before serving traffic and MUST NOT print secret values

#### Scenario: Request correlation is consistent

- GIVEN a missing, valid or invalid correlation header
- WHEN a request is processed
- THEN the service MUST generate or validate a correlation ID, return it in `X-Correlation-Id`, and use the same value in the response contract or error envelope

#### Scenario: Idempotency metadata is validated

- GIVEN an optional `X-Idempotency-Key`
- WHEN the request is processed
- THEN the key MUST be validated and propagated as metadata when valid, rejected when invalid, and MUST NOT imply durable replay or persistence in this Work Unit

#### Scenario: Shutdown drains bounded work

- GIVEN active request tasks during shutdown
- WHEN termination is requested
- THEN the service MUST enter draining, stop accepting new work, await the registered tasks for a bounded timeout, cancel remaining tasks, and expose the resulting lifecycle state

#### Scenario: HTTP errors use the v1 envelope

- GIVEN a 404, 405 or controlled application error
- WHEN the API responds
- THEN it MUST return the v1 error envelope and matching `X-Correlation-Id`

## Explicit non-goals for Work Unit 1

No PostgreSQL, Compose, authentication, persistence, ORM, migrations, repositories, RLS, transactions, durable idempotency, outbox, CI/CD, deployment, WACRM, CRM, WhatsApp, agents, LangGraph, automations, meetings or forms.

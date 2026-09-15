# GitHub Actions CI and Automatic Deploy Specification

## Purpose
Gate automatic deployment on executable quality, isolation, and runtime evidence.

## Requirements

### Requirement: Fail-closed CI/CD gates
GitHub Actions MUST install from the lockfile and block deployment unless typecheck, tests, builds, migration validation, real-PostgreSQL isolation tests, and health checks pass. Deployment MUST use an immutable artifact, capture a receipt, and perform health-gated rollout. Registry and observability providers remain open.

#### Scenario: All gates pass
- GIVEN every required check passes for an immutable revision
- WHEN the workflow deploys
- THEN the selected target receives that artifact and rollout completes only after readiness

#### Scenario: Any gate fails
- GIVEN typecheck, tests, build, migration, isolation, or health checks fail
- WHEN the workflow evaluates gates
- THEN no automatic deployment occurs and the failure is retained for review

#### Scenario: Health fails after rollout
- GIVEN a new artifact is deployed but readiness does not stabilize
- WHEN the rollout timeout expires
- THEN traffic returns to the prior compatible artifact, while destructive migration recovery remains an explicit operator decision

#### Scenario: Compose alone is insufficient
- GIVEN the application starts successfully in local Compose
- WHEN production readiness is assessed
- THEN it MUST remain unapproved until CI gates, deployment checks, backup evidence, and production health evidence also pass

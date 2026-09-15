# Compose Reproducibility and Environment Specification

## Purpose
Make local and VPS execution reproducible while separating configuration from secrets.

## Requirements

### Requirement: Portable packaging and validated configuration
Docker Compose MUST reproduce the selected services locally and on a VPS using the same immutable application images, persistent PostgreSQL storage, non-root runtime, and explicit environment validation. Production secrets MUST come from environment or a secret mechanism and MUST NOT be committed.

#### Scenario: Clean local bootstrap succeeds
- GIVEN a lockfile, approved images, and valid local variables
- WHEN Compose starts
- THEN services become reachable and data survives a restart

#### Scenario: Production secret is missing
- GIVEN production mode without a required secret
- WHEN deployment validation runs
- THEN it MUST fail before startup and identify only the missing variable name


#### Scenario: VPS differs only by configuration
- GIVEN the same image digest is deployed locally and to the VPS
- WHEN both environments run
- THEN runtime behavior and health contracts remain equivalent, aside from configured endpoints

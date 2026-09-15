# Work Unit 2 — Evidence and validation correction

## Status

**Accepted for the pure domain/application seam.** The original implementation was rejected because use cases trusted caller-supplied identity/scope and lacked a tenant-scoped repository port. This revision addresses those findings only.

## Corrections implemented

### Trusted identity

Added `CurrentIdentity` in `src/application/ports/tenant-ports.ts`.

Use cases now obtain the trusted `User` through the port. They no longer receive `userId` or `agencyId` as authorization inputs. `targetClientAccountId` is treated as a lookup hint only.

### Scope resolution

`ResolveAuthorizedTenantContext` now:

- Loads the trusted user from `CurrentIdentity`.
- Resolves the Agency and ClientAccount through `TenantRepository`.
- Resolves membership through trusted user identity and target account.
- Rejects a falsified requested agency.
- Rejects unknown/foreign accounts fail-closed.
- Constructs `TenantContext` only from server-derived objects.

### Switching

`SwitchClientAccount` now:

- Verifies the current context belongs to the trusted identity.
- Requires `client_account:switch` from the current context.
- Validates the target through the resolver and server-side membership repository.
- Does not mutate the prior context on rejection.
- Checks the stored idempotency target before replaying a result.
- Throws `IdempotencyConflict` when a key is reused for another target.

### TenantScopedRepository

Added a generic port requiring `TenantContext` explicitly for:

- `read`
- `create`
- `update`
- `delete`

`assertTenantScope` rejects missing context, missing client-account permission and resources from another client account.

### Permissions

`TenantContext` now rejects any permission not present in its `Membership`. Context construction uses membership-derived permissions by default. No caller-provided permission list is accepted as authority by the use cases.

### Contract scenarios

Expanded `tenant-contracts.test.ts` to cover:

- Authorized resolution.
- Authorized switching.
- Unauthorized switching.
- Forged agency scope.
- Forged account/membership mismatch.
- Cross-account resolution rejection.
- Cross-account resource guard for read/create/update/delete through the repository contract.
- No-op/context preservation after denial.
- Permissions derived from membership.
- Idempotency target conflict.
- Audit and event emission.

The module was adapted to Vitest by Work Unit 2.5.

## Verification

### Work Unit 2.5 executable verification

Work Unit 2.5 added only the minimum TypeScript/Vitest toolchain needed to execute these pure contracts:

```text
npm install ✅
npm run typecheck ✅
npm test ✅
```

Exact result:

```text
Test Files  1 passed (1)
Tests       1 passed (1)
```

Dependency additions are limited to dev dependencies:

```text
typescript ^5.8.3
vitest ^3.2.4
```

`npm install` reported 0 vulnerabilities. npm reported that an esbuild install script was blocked by the environment install-script policy; typecheck and tests still passed.

Earlier `node --check` commands were syntax-only and are not equivalent to compilation or test execution. The executable verification above supersedes that limitation.

No framework, database, WACRM, HTTP API or product infrastructure was added.

## Remaining limitation

The ports have no concrete adapter by design. Durable idempotency and actual repository-level cross-account enforcement require a future adapter implementation and executable runtime verification. `CurrentIdentity` and `CurrentTenantContext` explicitly document that the future adapter owns authenticated identity and current server context. This work unit intentionally adds no persistence or infrastructure.

## Acceptance status

The requested validation corrections are reflected in code and contract scenarios. Work Unit 2 is **accepted for the pure domain/application seam** after independent `npm run typecheck` and `npm test` verification. Concrete adapters, durable idempotency, persistence-backed isolation and runtime authentication remain future work. No PDR, architecture, specs, WACRM, application framework or database were added or modified by this work unit.

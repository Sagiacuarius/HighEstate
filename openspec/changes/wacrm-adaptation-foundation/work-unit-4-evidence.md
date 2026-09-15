# Work Unit 4 — Final first-slice evidence

## Scope and status

This document records the final executable evidence for the first product-owned tenant slice. It does not add new functionality. It records the in-memory adapter scenarios implemented in Work Unit 3 and the executable verification available through Work Unit 2.5.

No production readiness is claimed.

## Scenario fixtures

### Agency

```text
Agency A: agency-a
Agency B: agency-b
```

### ClientAccounts

```text
Account A: account-a, owned by agency-a
Account B: account-b, owned by agency-a
Foreign Account: account-foreign, owned by agency-b
```

### Users and memberships

```text
User A: user-a
  membership: membership-a → agency-a / account-a
  membership: membership-b → agency-a / account-b
  roles: operator
  permissions: client_account:read, client_account:write, client_account:switch

User B: user-b
  no membership in Account B
```

## Context server-derived

`InMemoryCurrentIdentity` supplies the authenticated `User` for the scenario. `InMemoryCurrentTenantContext` supplies the current context for switching. The use cases do not accept `userId`, `agencyId`, or an arbitrary current context from the request as authority.

The requested `targetClientAccountId` is only an intention. The resolver obtains Agency, ClientAccount and Membership through the server-owned adapters, then creates the immutable `TenantContext`.

The future `CurrentIdentity`/`CurrentTenantContext` adapter is responsible for returning authenticated identity and the authenticated current context. These in-memory adapters are only evidence fixtures.

## Executable evidence

### Authorized switch: Account A → Account B

```text
User A + Membership B + client_account:switch
→ target account-b
→ new context for Account B
→ success audit recorded
→ TenantContextSwitched event recorded
```

### Unauthorized switch

```text
User B without Membership B
→ target account-b
→ fail-closed rejection
→ previous Account A context unchanged
→ no successful switch audit
→ no TenantContextSwitched event
```

### Forged scopes

- Falsified Agency scope is rejected.
- Foreign `client_account_id` belonging to Agency B is rejected.
- Agency membership alone does not grant access to every ClientAccount.
- Membership, Agency ownership and permissions are revalidated server-side.

### Cross-account read/write isolation

Using `InMemoryTenantScopedRepository`:

- Read Account B using Account A context: rejected.
- Create an Account B resource using Account A context: rejected.
- Update Account B using Account A context: rejected.
- Delete Account B using Account A context: rejected.
- Account B record remains present and unchanged.
- Repository write count remains zero after rejected cross-account operations.

The repository contract requires `TenantContext` for all `read`, `create`, `update` and `delete` operations. Adapters must use `tenantContext.clientAccountId` as their mandatory predicate and may not resolve the tenant from external input, payloads, claims or resource IDs.

### Context preservation

After a denied switch, the server-owned current context remains Account A. No caller-supplied context is mutated or accepted as replacement authority.

### Permission derivation

`TenantContext` permissions are derived from `Membership`. Construction rejects permissions not present in that membership.

### Idempotency conflict

```text
Stored key: reuse-key → Account A
New command: reuse-key → Account B
Result: IdempotencyConflict
```

No successful switch audit or success event is emitted for the conflict.

### Auditing and events

Denials are recorded by `InMemoryAuditSink` with the requested scope, trusted user, correlation ID and reason. `InMemoryDomainEventPublisher` records domain events.

Rejected operations do not emit `TenantContextSwitched` and do not create successful switch audit entries. Authorization-denial evidence remains distinct from successful switch evidence.

## Commands and exact results

Executed from:

```text
C:\Users\Windows\Documents\AI Agency Platform
```

### Typecheck

```text
npm.cmd run typecheck ✅
exit code: 0
```

### Tests

```text
npm.cmd test ✅
exit code: 0
```

Exact Vitest result:

```text
Test Files  2 passed (2)
Tests       7 passed (7)
Duration    946ms
```

The total includes:

```text
Work Unit 2 contract file: 1 test
Work Unit 3 adapter evidence file: 6 tests
Total: 7 passed
```

## Guarantees demonstrated

- Explicit memberships are required.
- Agency membership does not imply unrestricted ClientAccount access.
- Context is server-derived in the use-case flow.
- The requested account ID is not authorization authority.
- Cross-account reads and writes fail closed.
- Denied operations do not modify records.
- Denied operations do not emit success events.
- Idempotency target conflicts are rejected.
- Correlation and idempotency values are carried through audit/event contracts.

## Limitations

These are in-memory reference adapters, not production infrastructure:

- No real authentication or identity provider.
- No persistence.
- No durable idempotency.
- No database transactions.
- No concurrency guarantees.
- No distributed audit/event delivery.
- No HTTP/runtime integration.
- No database-backed isolation proof.
- No production storage or cache boundaries.

A future persistence adapter must make idempotency lookup/result recording atomic. A future identity adapter must never trust client-provided user, agency or account claims. A future scoped repository must apply `tenantContext.clientAccountId` to every operation.

## Deferred decisions

The following remain intentionally deferred:

- Runtime and application deployment stack.
- Database and persistence technology.
- Authentication and identity provider.
- Transaction and concurrency model.
- Durable idempotency store.
- Audit/event persistence and observability backend.
- Queue, worker and distributed execution model.

## WACRM status

WACRM remains an audited architectural reference only:

- No fork created.
- No WACRM code copied or executed.
- No WACRM dependency added.
- No WACRM runtime used.
- Agency → ClientAccount and authorization contracts remain product-owned.

## Acceptance boundary

This evidence validates the in-memory first slice only. It does not declare the platform production-ready and does not prove real authentication, persistence, database isolation or distributed idempotency.

Work Unit 4 records verification evidence without implementing new product functionality.

## Acceptance status

Work Unit 4 is complete as an evidence/documentation work unit. Production readiness remains explicitly out of scope.

## Source artifacts reviewed

- `tasks.md`
- `proposal.md`
- `specs/tenant-isolation/spec.md`
- `specs/wacrm-audit-governance/spec.md`
- `design.md`
- `audit.md`
- `adoption-decision.md`
- `work-unit-2-evidence.md`
- `work-unit-3-evidence.md`
- `src/domain/tenant/`
- `src/application/tenant/`
- `src/application/ports/`
- `src/application/audit/`
- `src/adapters/in-memory/`

Both requested specification files were present and included in the review. No specification-path gap remains for this evidence work unit.

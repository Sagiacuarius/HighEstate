# Work Unit 3 — In-memory authorization and isolation evidence

## Scope

This work unit adds reference/test adapters only. They are not production infrastructure.

No Next.js, React, Supabase, FastAPI, database, HTTP, WACRM, external provider, persistence runtime or product feature was added.

## Files created

- `src/adapters/in-memory/tenant-adapters.ts`
- `src/adapters/in-memory/tenant-adapters.test.ts`
- `openspec/changes/wacrm-adaptation-foundation/work-unit-3-evidence.md`

## Adapters implemented

- `InMemoryCurrentIdentity`: supplies a trusted authenticated `User` for scenarios; no request `userId` is accepted.
- `InMemoryCurrentTenantContext`: supplies the server-owned current context for switch scenarios.
- `InMemoryTenantRepository`: stores Agencies and ClientAccounts; rejects missing agencies and account reassignment across agencies.
- `InMemoryMembershipRepository`: stores explicit user/account memberships and idempotency results.
- `InMemoryTenantScopedRepository`: implements `read`, `create`, `update`, `delete` and calls the tenant scope guard before mutation.
- `InMemoryAuditSink`: records authorization denials and successful switches.
- `InMemoryDomainEventPublisher`: records emitted events and allows assertions that no success event was emitted after denial.

## Scenarios covered

- Authorized administrator switches Account A → Account B.
- Non-member cannot switch to Account B.
- Falsified agency scope rejected.
- Foreign ClientAccount rejected.
- Cross-account read rejected.
- Cross-account create rejected.
- Cross-account update rejected.
- Cross-account delete rejected.
- Account B record is not returned or modified.
- No writes occur after rejected cross-account operations.
- Previous context remains unchanged after denied switch.
- Permissions are derived from Membership.
- Idempotency target conflict rejected.
- Denials are audited.
- No successful switch audit/event is emitted after a denied operation.

## Commands required for verification

```bash
npm run typecheck
npm test
```

## Limitations

- Adapters are in-memory and process-local.
- Idempotency durability is not durable; it is represented through an adapter seam only.
- Authentication is represented by `InMemoryCurrentIdentity`, not a real identity provider.
- No persistent transaction or concurrency guarantee exists.
- Repository enforcement is reference behavior, not a production database isolation guarantee.
- This evidence does not claim Work Unit 3 complete until both commands pass.

## Next-stage risks

- A future adapter must enforce the same `TenantContext.clientAccountId` predicate for every read/write.
- A future persistence adapter must make idempotency lookup and result recording atomic.
- A real identity adapter must never trust client-provided user, agency or account claims.
- Runtime tests must prove cross-account reads and writes fail against the selected persistence technology.
- Audit/event durability and ordering require a future adapter/runtime decision.

## Work Unit 3 verification

```text
npm run typecheck ✅
npm test ✅
```

PowerShell's `npm.ps1` wrapper is blocked by the local execution-policy setting;
the equivalent `npm.cmd` invocations completed successfully with exit code 0.

Exact result:

```text
Test Files  2 passed (2)
Tests       7 passed (7)
Duration    992ms
```

The Work Unit 3 test file contains 6 tests; Work Unit 2's existing pure contract file contains 1 test. All 7 tests passed.

## Acceptance status

Work Unit 3 is **accepted for the in-memory adapter and evidence slice**. Production
identity, persistence, durable idempotency, transactionality and runtime isolation
remain deferred to a future infrastructure work unit.

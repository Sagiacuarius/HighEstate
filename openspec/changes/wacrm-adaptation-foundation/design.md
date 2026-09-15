# Design: WACRM Adaptation Foundation

This design establishes a product-owned tenancy seam and an evidence gate for WACRM. It is intentionally framework- and database-neutral because the repository currently contains only documentation and a static demo.

## Technical Approach

Treat WACRM as an audited external input, never as a domain authority. Until provenance, exact revision, license/notices, security, persistence, authentication, compatibility, and remediation ownership are evidenced, the gate is `blocked` and the standalone tenancy path is used. Build the first runtime slice around `Agency → ClientAccount`, explicit memberships, server-derived context, authorized switching, and denial evidence.

## 1. Context Map and Boundaries

`Identity Provider` authenticates a user; the product owns membership and authorization. `Web UI` requests application use cases. `Tenant Core` owns Agency, ClientAccount, Membership, policy, audit, and events. `Persistence`, `WACRM Intake`, and future providers are replaceable adapters. The demo/WACRM shell is presentation material only; it cannot define tenancy.

```
UI -> Application -> Domain ports -> Persistence / Identity / Audit
                         ^
                    WACRM adapter (only after gate)
```

## 2. Clean Architecture and Hexagonal Ports

Dependencies point inward: presentation → application → domain; infrastructure implements ports and depends inward. Proposed product-owned module boundaries (paths are implementation targets, not current files): `domain/tenant`, `application/tenant`, `application/audit`, `adapters/inbound`, and `adapters/outbound`. Their eventual filesystem representation remains a later implementation concern.

| Port | Purpose |
|---|---|
| `CurrentIdentity` | Authenticated subject and claims; never client scope. |
| `MembershipRepository` | Find explicit account membership and permissions. |
| `TenantRepository` | Load Agency/ClientAccount relationships. |
| `TenantScopedRepository` | Require a resolved context for every read/write. |
| `AuditSink` | Append governance, authorization, and outcome evidence. |
| `EventPublisher` | Publish committed, scoped domain events. |
| `WacrmAuditStore` | Store evidence and per-area adopt/adapt/replace/defer/blocked decisions. |

## 3. Domain Model and Use Cases

Entities: `Agency`, `ClientAccount`, `User`, and `Membership`. Value objects: `AgencyId`, `ClientAccountId`, `UserId`, `Role`, `Permission`, `TenantContext`, `CorrelationId`, `IdempotencyKey`, and `WacrmRevision`. `TenantContext` is server-created and immutable for a use-case execution. Invariants: each scoped record has exactly one account; Agency membership alone is insufficient; account membership must belong to the same Agency.

`ResolveAuthorizedTenantContext` authenticates identity, loads the requested/active account relation from server state, checks membership and action permission, verifies Agency ownership, and returns `TenantContext`; absent or ambiguous scope fails closed. `SwitchClientAccount` accepts only a target identifier and correlation/idempotency data, resolves authorization through the same use case, records success/failure, and returns a new context without mutating the prior context on denial.

## 4. Authorization and Safe Denial

Inbound middleware extracts identity and correlation only. A policy service calls context resolution before handlers, repositories, jobs, tools, or cache access. Client-provided `agency_id`/`client_account_id` is a hint to resolve, never authority. Repositories require context and reject missing/mismatched scope. Denials return a non-enumerating authorization error, make no state change, and emit sanitized audit evidence. Asynchronous commands serialize scope and revalidate it before execution.

## 5. Persistence, Events, and Idempotency

Use repository interfaces and transaction boundaries without selecting a database or row-level-security product. The `client_account_id` requirement applies only to tenant-scoped data and artifacts: predicates, uniqueness constraints, cache/object keys, jobs, commands, and domain events whose subject or effect belongs to one ClientAccount MUST carry the resolved account scope. Agency-level records, global authorization/governance records, and WACRM audit evidence MUST NOT be forced into a client-account scope when their ownership is broader; they retain their appropriate Agency or global identity and explicit references where applicable. The persistence design must later prove tenant-scoped isolation with cross-account read/write evidence. Publish `TenantContextSwitched` and scoped `AuthorizationDenied` only after the relevant decision is durable. `SwitchClientAccount` is idempotent by `(actor, target, idempotency_key)`; duplicate commands return the recorded result. Correlation IDs link request, audit, event, job, and evidence.

## 6. WACRM Governance and Fallback

Represent an audit record as source metadata, evidence references, reviewer/date, findings, risk, remediation owner, and one decision per area. The admission policy allows copy/distribution/execution only when required evidence is complete and accepted; otherwise WACRM remains study-only. A blocked audit selects standalone product-owned adapters while retaining the record. Rollback removes provisional WACRM intake, not tenant contracts or evidence.

## 7. Runtime Context and Maquette Correspondence

The detected baseline is a static HTML/CSS/JavaScript demo with no executable application stack, package manifest, backend, database, authentication, or test runner. Runtime technology selection is intentionally deferred; it is neither an architecture decision nor a dependency of this slice. The demo’s workspace switcher, breadcrumb, dashboard, and operator modal map to future UI/context/query responsibilities; its metrics, accounts, conversations, “Kapso” status, and draft response are fixtures, not runtime proof. No demo edit is required.

## 8. Work Units, Risks, and Rollback

1. **Governance intake**: define audit evidence schema and gate; dependency: WACRM URL/access; rollback: keep blocked record.
2. **Domain seam**: entities, value objects, ports, and use cases; dependency: approved contracts; rollback: standalone remains default.
3. **Authorization evidence**: middleware/policy, repositories, two-account allowed switch and denied read/write scenarios; dependency: identity/persistence adapters.
4. **Verification handoff**: define runtime-agnostic evidence for identity, memberships, resolved server context, authorized switching, denied cross-account reads/writes, correlation/idempotency, and fixture/runtime separation; dependency: approved contracts and evidence format. Runtime technology selection is a later dependency for implementation, not a blocker for this design slice.

Risks are unverified upstream facts, license/security incompatibility, single-workspace assumptions, and false confidence from fixtures. No CRM, real WhatsApp, operational agents, complete automation engine, meetings, or forms belong to this change.

## Open Questions

- [ ] WACRM URL, owner, revision, license, and repository strategy.
- [ ] Authentication/session, persistence/RLS, queue, and observability selections.
- [ ] Initial role/permission matrix and audit retention policy.

## Threat Matrix

N/A — this design introduces no shell, subprocess, VCS/PR automation, executable classification, or process-integration boundary; middleware is an application policy seam, not route implementation.

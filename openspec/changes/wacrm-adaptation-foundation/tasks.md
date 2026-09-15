# Tasks: WACRM Adaptation Foundation

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated authored implementation lines | 900–1,200 (future runtime slice, contracts, tests, evidence) |
| 800-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 governance; PR 2 domain seam; PR 3 authorization/isolation slice; PR 4 verification evidence |
| Delivery strategy | auto-chain |
| Chain strategy | pending — apply auto-chain policy; chain strategy is not selected |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Audit record and blocked gate | PR 1 | N/A: no runner; checklist review | N/A: no runtime selected | Proposed audit artifacts only |
| 2 | Product-owned tenant contracts | PR 2 | N/A: no runner; contract review | N/A: runtime deferred | Proposed tenant module only |
| 2.5 | Minimal pure-contract verification toolchain | PR 2.5 | `npm run typecheck`; `npm test` | TypeScript + Vitest only | Remove toolchain and test adapter, preserving domain contracts |
| 3 | Authorized switch and isolation | PR 3 | `npm run typecheck`; `npm test` | In-memory adapters only | Remove adapters/evidence, preserving domain contracts |
| 4 | Evidence handoff and scope guard | PR 4 | N/A: no runner; evidence checklist | Same journey plus denied read/write proof | Proposed evidence artifacts only |

## Phase 1: Governance and Documentation (audit-only)

- [x] 1.1 Record WACRM URL, owner, revision, provenance, license/notices, security, persistence/authentication, compatibility, extensibility, maintenance, risks, reviewers, dates, and remediation owners in proposed `openspec/changes/.../audit.md`; depends on source access; acceptance: every required area has evidence or `blocked`; evidence: linked references and review record.
- [x] 1.2 Define per-area adopt/adapt/replace/defer/blocked decisions and the copy/distribute/execute gate in proposed `openspec/changes/.../adoption-decision.md`; depends on 1.1; acceptance: incomplete evidence keeps WACRM study-only and selects standalone fallback; evidence: traceable decision matrix and rollback owner.
- [ ] 1.3 Document product-owned boundaries and explicit non-goals in the existing proposal/design/spec artifacts only where later implementation needs clarification; depends on 1.2; acceptance: no runtime, database, identity, CRM, WhatsApp, agents, automations, meetings, or forms are selected; evidence: reviewed OpenSpec diff.

## Phase 2: Future Implementation — Product-Owned Domain Seam

- [x] 2.1 In proposed `domain/tenant` area, define Agency, ClientAccount, User, Membership, IDs, permissions, immutable TenantContext, correlation/idempotency values, and invariants; depends on approved contracts; acceptance: each scoped record has exactly one account and agency membership alone is insufficient; evidence: domain contract tests when a runner exists.
- [x] 2.2 In proposed `application/tenant` and `application/audit` areas, define context resolution, SwitchClientAccount, denial, audit, event, and repository ports; depends on 2.1; acceptance: server derives scope, denial is fail-closed/no-op, and WACRM remains an adapter; evidence: use-case contract scenarios.
- [x] 2.5 Add the minimal TypeScript/Vitest verification harness required to compile and execute the pure tenant contracts; acceptance: `npm run typecheck` and `npm test` pass without adding application infrastructure; evidence: `work-unit-2-evidence.md`.

## Phase 3: Future Implementation — Authorization and Isolation Slice

- [x] 3.1 In proposed `adapters/inbound` and `adapters/outbound` areas, wire identity, membership/tenant repositories, scoped repositories, audit sink, and event publisher without choosing runtime/database; depends on 2.2; acceptance: repositories reject missing/mismatched context and client scope is non-authoritative; evidence: adapter contract review.
- [x] 3.2 Implement the two-ClientAccount administrator journey in in-memory reference adapters, including authorized switch, unauthorized switch, forged scope, and denied cross-account read/write; depends on 3.1; acceptance: B data is neither returned nor changed and prior context survives denial; evidence: executable scenario results from the reference adapter.

## Phase 4: Verification and Documentation Handoff

- [x] 4.1 Produce proposed isolation evidence covering identity, memberships, selected account, server context, allowed switch, denied read/write, correlation, and idempotency; depends on 3.2; acceptance: visual switching alone is rejected as proof; evidence: `work-unit-4-evidence.md` with executable adapter results.
- [x] 4.2 Record runtime selection, persistence/identity choices, and WACRM admission outcome as later decisions; depends on governance and slice review; acceptance: runtime, persistence, identity and observability remain deferred and no unverified WACRM facts become dependencies; evidence: `work-unit-4-evidence.md` and existing audit/adoption artifacts.

## Explicitly Deferred

CRM, real WhatsApp/Kapso, agents, complete automation engine, meetings, forms, and executable stack selection remain outside this change.

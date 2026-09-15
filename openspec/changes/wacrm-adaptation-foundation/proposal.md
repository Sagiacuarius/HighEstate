# Proposal: WACRM Adaptation Foundation

## Intent
Create a product-owned foundation for evaluating/adapting WACRM while proving the first invariant: an administrator switches between two `ClientAccount` tenants without cross-account access. This is a new base informed by permitted findings, not automatic copying.

## Motivation
The repository is documentation plus a static demo; executable tenancy, authentication, persistence, and verified WACRM source are absent. Unverified upstream assumptions create license, provenance, security, data-model, and lock-in risk.

## Scope
- Audit WACRM: frontend, workflows, persistence, authentication, architecture, provenance, revision, license/notices, security, extensibility, compatibility, and maintenance.
- Record an adoption decision and hard gate: no WACRM code may be copied, distributed, or executed before acceptance.
- Define product-owned Clean Architecture and hexagonal boundaries for `Agency → ClientAccount`, memberships, authorized context resolution, and isolation.
- Define the bounded account-switching slice and evidence.

## Explicit non-goals
No complete CRM, real WhatsApp/Kapso, operational agents, automation engine, meeting integration, executable-stack or database/authentication selection, or invented WACRM facts.

## Product/architecture outcomes
- Reviewable adopt/adapt/replace/defer decisions per area.
- Tenant contracts independent of WACRM assumptions.
- Server-verified account selection; agency membership alone grants no unrestricted account access.
- Dependency direction: presentation → application → domain ports; adapters isolate WACRM, persistence, identity, and future providers.

## Audit gate and first bounded slice
The gate records provenance, owner/repository, revision, license compatibility/notices, security findings, authentication/persistence assessment, and remediation ownership. If incomplete, WACRM remains study-only; a standalone tenancy foundation is the fallback.

The slice covers one administrator, an agency, two accounts, explicit memberships, authorized switching, server-derived `client_account_id`, and rejected cross-account reads/writes. Evidence must prove isolation, not visual switching.

## Capabilities
### New Capabilities
- `tenant-isolation`: agency/account membership, authorized resolution, and isolation evidence.
- `wacrm-audit-governance`: provenance, license, security, compatibility, and adoption gate.
### Modified Capabilities
- None; no executable capability currently exists.

## Affected areas
| Area | Impact |
|---|---|
| `openspec/changes/wacrm-adaptation-foundation/` | Proposal, audit, and later SDD artifacts |
| `docs/02-PDR-plataforma-agencias-ia.md`, `docs/03-arquitectura-plataforma.md` | Constraints/reference; no implementation change |

## Risks and dependencies
High risks: incompatible licensing, vulnerable or single-workspace assumptions, fork divergence, and fixture-driven false confidence. Dependencies: WACRM access/revision, audit evidence, repository strategy, and later runtime, persistence, and identity decisions.

## Acceptance direction
- [ ] Audit is complete or explicitly blocks adoption.
- [ ] Tenant boundaries are documented and reviewable.
- [ ] Switching proves authorization and denied cross-account access.
- [ ] No unverified WACRM fact becomes a dependency.

## Rollback plan
Reject adoption, remove provisional WACRM intake, retain the audit record, and continue with a standalone product-owned tenancy foundation.

## Open questions
- What are WACRM’s exact URL, owner, revision, and license?
- Fork, vendored snapshot, or separate repository with upstream tracking?
- Which findings, if any, are permitted for adaptation?

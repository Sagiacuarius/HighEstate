# Exploration: WACRM Adaptation Foundation

## Current State

The repository is a documentation and static-demo baseline, not an executable application. The requested evidence files are present, but no WACRM source, upstream URL, package manifest, backend, persistence layer, authentication, CI, test runner, or build configuration is present. The target root also has no detectable Git repository, so fork history and upstream synchronization cannot yet be established locally.

The product direction is clear: a shared runtime for a multi-tenant SaaS with `Agency → ClientAccount` isolation, a Kapso-backed `WhatsAppProvider`, conversational and operator agents, validated tools, a deterministic automation engine, explicit confirmation, execution, and audit. The documented implementation direction is Next.js/React/TypeScript plus FastAPI/Python and LangGraph, but these are intended technologies, not detected stack evidence.

The demo is a static visual shell for one apparent account (`Aurea Realty`) inside an agency context. It exposes navigation for Resumen, Inbox, Leads, Propiedades, Automatizaciones, Pipeline, Agenda, WhatsApp, Conocimiento, Métricas, and Configuración. The HTML contains fixture metrics, conversations, properties, pipeline cards, flows, and calendar items. JavaScript only switches hash-based views and returns an escaped textual “draft preparation” response from the operator modal; it does not call an API, persist data, execute automation, or resolve tenancy. Several visible links have no corresponding functional section, and Agenda is declared twice in navigation.

The architecture documentation already states the desired boundary: WACRM may provide an initial frontend/canvas/component base, but `Agency → ClientAccount`, permissions, audit, providers, data contracts, and commercial core remain product-owned. WACRM must be treated as an implementation input to audit and adapt, not as a permanent domain dependency.

## Affected Areas

- `docs/01-brief-comercial.md` — establishes product scope, MVP entities, agent roles, automation safety cycle, and provider intent.
- `docs/02-PDR-plataforma-agencias-ia.md` — defines functional/non-functional requirements, tenancy, separation of agent/tools/engine, and the WACRM fork decision.
- `docs/03-arquitectura-plataforma.md` — defines logical boundaries, tenant isolation rules, provider contracts, security/audit expectations, and the documented-versus-pending stack.
- `demo/agencia-inmobiliaria.html` — static navigation and visual surfaces that a future fork adaptation may preserve selectively.
- `demo/agencia-inmobiliaria.css` — confirmed plain CSS styling and responsive shell; no framework or design-system dependency is proven.
- `demo/agencia-inmobiliaria.js` — local-only navigation and operator mock; useful as behavior evidence but not an application boundary.
- `openspec/config.yaml` — confirms hybrid persistence, interactive execution, auto-chain delivery, 800-line review budget, no detected testing capabilities, and the progressive WACRM strategy.
- `openspec/changes/wacrm-adaptation-foundation/exploration.md` — this exploration artifact; no proposal, specification, design, or task artifact has been created.

## Architecture Findings

### Fork versus product-owned core

The fork boundary should be explicit from the first executable slice. Candidate reusable WACRM material is the presentation shell, canvas, and generic components only after license, security, extensibility, and compatibility review. Product-owned contracts must include tenant resolution, authorization, provider interfaces, audit identity, domain events, and execution policy. Any WACRM persistence or domain model that assumes a single workspace or lacks account-scoped authorization must be wrapped or replaced rather than copied into the core.

### Tenancy

`Agency` is the commercial administrator and grouping boundary; `ClientAccount` is the operational tenant and minimum data-isolation boundary. A membership in an agency must not imply unrestricted access to every client account. Every request, repository query, agent context, tool invocation, event, job, cache key, provider connection, and stored object needs an explicit and server-verified `client_account_id`; client-supplied scope cannot be authoritative.

### Agents, tools, and automation engine

The conversational agent operates in a lead conversation; the operator agent serves panel users and may produce drafts. Tools are small permission-checked contracts, not direct access to persistence or providers. The automation engine is deterministic and consumes committed structured events such as `tag.added`, not a model's in-flight subjective inference. The safe path remains interpretation → draft → human confirmation → engine execution → audit.

### Maquette-to-architecture distance

The demo proves visual intent and navigation vocabulary, but proves none of the runtime architecture. There is no authenticated account selector, server-side authorization, real inbox, provider webhook, CRM persistence, agent runtime, tool registry, automation versioning/executor, queue, idempotency, audit log, or metric query. The largest ambiguity is therefore not visual implementation; it is the boundary between a reusable WACRM shell and the product-owned tenant-aware application contracts.

## Approaches

1. **Fork intake and boundary baseline** — audit the WACRM source before adoption, record provenance/license/security findings, import it into a separately named product repository or source boundary, and define a minimal tenant-aware shell contract (`Agency`, `ClientAccount`, membership/context selection) without implementing the full SaaS.
   - Pros: resolves the highest-risk unknowns first; creates a controlled fork/upstream relationship; tests whether the visual base can be retained without surrendering core ownership.
   - Cons: cannot be completed until the WACRM URL, revision, license, and access are supplied; produces little end-user functionality by itself.
   - Dependencies: WACRM repository access; license and notice files; Git repository strategy; intended deployment/runtime decision.
   - Effort: Medium.

2. **Standalone tenancy contract spike** — ignore WACRM initially and implement a minimal executable domain/API slice for `Agency → ClientAccount`, memberships, scope resolution, and cross-account access tests.
   - Pros: gives the riskiest security invariant executable proof; independent of uncertain upstream quality and license.
   - Cons: may duplicate or later conflict with WACRM's application structure; does not validate the fork/canvas strategy; requires selecting a real stack and persistence approach first.
   - Dependencies: backend stack, database, authentication model, repository creation, test runner.
   - Effort: Medium to High.

3. **Visual shell adaptation first** — port or reshape WACRM/demo navigation and canvas surfaces before introducing tenancy or persistence.
   - Pros: fast visual progress and early confirmation of UX vocabulary.
   - Cons: risks turning static assumptions into architecture; can hide scope leaks and single-tenant assumptions; provides weak evidence for the core decision.
   - Dependencies: WACRM source and license audit, frontend stack selection, component compatibility review.
   - Effort: Medium.

## Recommendation

Recommend Approach 1 as the first bounded change, with a hard admission gate: no WACRM code is copied, distributed, or made a runtime dependency until provenance, exact revision, license compatibility, security posture, and repository ownership are recorded. The implementable slice should establish the fork/upstream boundary and a minimal tenant-aware application seam, not build the inbox, CRM, agents, automation engine, or full SaaS.

The slice should leave behind evidence that answers: what is inherited, what is product-owned, where tenant context enters, which WACRM modules are forbidden from defining domain behavior, and how future synchronization will be reviewed. If the WACRM audit cannot be completed, Approach 2 is the safer fallback and the fork remains a documented future dependency rather than an unverified implementation base.

## Dependencies and Product Questions

- What is the exact WACRM repository URL, commit/release, owner, and license? These values are not present in the supplied sources and must remain pending audit.
- Will the product repository be a true Git fork, a vendored snapshot, or a separate repository with a documented upstream remote?
- Which WACRM surfaces are intended for reuse: shell, canvas, component library, workflow model, persistence, authentication, or other modules?
- What is the first supported user journey: agency administrator switching between two client accounts, or a single client-account inbox flow?
- Which agency roles and account memberships exist, and which actions always require confirmation?
- What database, authentication/session model, queue/worker system, object storage, and observability stack will be selected?
- Is the first slice allowed to create a new Git repository or modify only the current documentation baseline?
- What privacy, retention, deletion, export, and provider-secret requirements apply to the pilot?

## Risks

- License incompatibility or missing notices could block use or distribution of the fork.
- A WACRM single-workspace data model could silently undermine `Agency → ClientAccount` isolation.
- Fork divergence may become permanent if upstream synchronization ownership and boundaries are not defined immediately.
- Static fixture navigation may be mistaken for implemented capabilities, inflating the apparent delivery baseline.
- Shared agent runtime, caches, asynchronous jobs, and provider webhooks create high-severity cross-tenant leakage paths if scope is implicit.
- No executable stack or tests exist, so architecture claims cannot yet be validated by runtime evidence.
- The 800-line budget and auto-chain strategy are available for later planning, but this exploration does not estimate implementation lines without WACRM source and repository access.

## Ready for Proposal

**Partial.** The product-owned boundaries and a recommended first slice are sufficiently clear for proposal framing, but implementation planning should remain blocked until WACRM provenance/access/license and repository strategy are answered. The next proposal should explicitly make the audit gate and the minimal tenant-aware seam in-scope, while excluding CRM breadth, real WhatsApp, agent execution, automation runtime, persistence selection, and production security hardening unless separately approved.

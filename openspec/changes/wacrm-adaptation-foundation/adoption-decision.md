# WACRM adoption decision — Work Unit 1

**Audited source:** `https://github.com/ArnasDon/wacrm.git`  
**Branch:** `main`  
**Commit:** `6ed9191189e71d2e69d9380422f9415ecc589266`  
**Scope:** audit and governance only. No WACRM code, dependency or runtime was added.

## Decision vocabulary

- **adopt:** adopt the concept/contract with no known structural conflict; implementation remains project-owned.
- **adapt:** useful reference, but requires redesign for this product.
- **replace:** do not use the WACRM approach; implement the project's own boundary.
- **defer:** potentially useful later, outside the current MVP or Work Unit.
- **blocked:** insufficient evidence or a material legal/security/architecture blocker.

## Area decisions

| Area | Decision | Decision and rationale |
|---|---|---|
| Repository provenance | adopt | Exact upstream, owner, branch and commit are recorded. Re-audit the commit before any future source use. |
| MIT licence | adapt | MIT is permissive, but future fork/distribution must preserve LICENSE and copyright notices. Third-party licences and trademarks still require review. |
| Fork governance | blocked | No fork is approved now. Need dependency licence inventory, notices, commercial/legal review and an upstream-sync policy. |
| Next.js / TypeScript stack | defer | Technically compatible, but the current platform has its own implementation and no code should be copied yet. |
| Supabase/PostgreSQL persistence | adapt | Useful reference for Postgres, migrations and RLS; project data contracts and schema remain independent. |
| Supabase Auth | adapt | WACRM's auth model informs role/invitation design, but identity and agency membership must be project-owned. |
| WACRM account model | adapt | Account can inform `ClientAccount`; it does not provide the required `Agency → ClientAccount` hierarchy. |
| Agency → ClientAccount | replace | Must be a first-party contract with agency membership, client isolation, agency administration, billing scope and audit. |
| User roles | adapt | `owner/admin/agent/viewer` is useful, but roles and permissions need agency/client scopes and tool policies. |
| RLS policies | adapt | Account membership policies are a useful pattern. Every project table/storage path must be reviewed for the agency/client boundary. |
| Service-role background paths | replace | Project tools must use explicit ports, ownership checks, scope validation and audit; never assume RLS alone protects service-role calls. |
| WhatsApp Meta integration | defer | WACRM's direct Meta implementation is useful reference, but the MVP remains Kapso through a project-owned provider abstraction. |
| `WhatsAppProvider` boundary | adopt | Keep provider-neutral operations such as send, templates, webhooks and connection state behind a project-owned interface. |
| Kapso provider | adopt | Continue as the MVP provider already selected by the project. WACRM must not be added as a dependency for this. |
| Meta Cloud provider | defer | Future enterprise/self-hosted option after the Kapso pilot and provider contract are stable. |
| WhatsApp templates | adapt | WACRM's template lifecycle/API ideas are useful; ownership, approval status and account scope must follow project contracts. |
| Webhook signatures | adapt | HMAC, raw-body verification, replay protection and event dedupe are good patterns; use the project's webhook identity model. |
| Outbound webhooks | adapt | Useful account-scoped API/webhook design; add agency/client scope, durable delivery and project audit requirements. |
| Public REST API | adapt | Scoped, revocable API keys are valuable; project API contracts must not expose cross-client access. |
| MCP integration | defer | Interesting future operator integration; not part of the current MVP and requires project-specific tool policies. |
| Visual workflow builder | adapt | Strong candidate for UX and workflow concepts; implement or integrate only after project automation contracts are defined. |
| Automation engine | adapt | Trigger/condition/action/wait model is useful; the project must preserve the rule that structured events trigger deterministic execution. |
| Agent-generated automations | replace | WACRM's builder cannot define the project's operator-agent contract. The project needs draft → confirmation → execution → audit. |
| AI provider abstraction | adopt | Multiple providers and usage controls support the planned `ModelProvider` design. Keep LangGraph/FastAPI as project-owned runtime. |
| Knowledge/RAG | adapt | FTS + optional pgvector is a useful reference; tenant filtering, citations, ingestion and provider policy need project design. |
| Custom fields | adapt | Useful for the CRM vertical; fields must be account/client scoped and governed by the project's domain model. |
| Pipeline/CRM | adapt | Useful product reference for leads/deals/stages; real-estate objects remain project-owned vertical modules. |
| Storage/media | blocked | Evidence shows historical user-scoped storage and later rescoping notes; complete agency/client storage isolation is not verified. |
| Encryption/secrets | adopt | AES-256-GCM, hash-only API keys and one-time secret display are strong patterns; key rotation/secret management remain project requirements. |
| Rate limiting | adapt | In-memory per-process limiting is acceptable only for a single instance; project scaling requires shared storage or explicit deployment limits. |
| CSP/security headers | adapt | Strong baseline, but CSP is Report-Only in the audited source and requires deployment verification before production enforcement. |
| SSRF protection | adopt | Public HTTPS target restrictions and private-address rejection are valuable for outbound webhook tools. |
| Tests | adopt | The breadth of unit/integration tests is a useful quality target; no WACRM tests were executed in this audit. |
| CI | adapt | Lint/typecheck/test/build pipeline is a good target; the project's own CI and secrets policy remain authoritative. |
| Clean Architecture | blocked | Modular boundaries exist, but full Clean/SOLID/Hexagonal compliance is not proven. Do not treat WACRM as a ready architecture. |
| SOLID | adapt | Domain-oriented modules and provider seams are useful; coupling in routes/service-role clients requires redesign. |
| Hexagonal providers | replace | Create explicit project-owned ports for WhatsApp, models, calendar, repositories, automation and audit. |
| Fork as product base | blocked | Material divergence is expected around agency tenancy, Kapso, agent runtime, visual system and domain modules. |
| Direct source copying | blocked | Explicitly prohibited by Work Unit 1; no files were copied or executed. |
| WACRM dependency | defer | Do not add it now. Reconsider only after a separate legal, dependency, architecture and maintenance review. |

## Current adoption posture

```text
Adopt concepts:
  provider-neutral boundaries
  RLS/account-scoping patterns
  API key scopes
  webhook signing/dedupe
  encryption patterns
  visual workflow concepts
  testing/CI targets

Adapt with project ownership:
  accounts → ClientAccount
  roles and invitations
  CRM/pipelines
  knowledge/RAG
  templates
  automations
  storage
  rate limiting

Replace:
  Agency → ClientAccount
  agent runtime and LangGraph contracts
  WhatsAppProvider/Kapso boundary
  tool permissions and audit
  deterministic automation approval/execution

Defer:
  direct Meta provider
  MCP
  self-hosted fork
  advanced enterprise RAG

Blocked until evidence/review:
  direct fork
  source copying
  dependency adoption
  storage isolation claims
  full Clean/Hexagonal equivalence
```

## Governance gates before any future adoption

1. Reconfirm the upstream commit and licence notices.
2. Generate a full third-party licence/SBOM report.
3. Decide whether adoption is reference-only, a clean reimplementation, or a fork.
4. If forking, preserve `LICENSE` and copyright notices and document modifications.
5. Define `Agency → ClientAccount` and prove cross-client isolation with tests.
6. Define project-owned provider ports before considering Meta/WACRM code.
7. Review all service-role paths, storage paths, webhooks and background jobs.
8. Run the selected source's lint, typecheck, tests and build in an isolated audit environment; this was intentionally not done in Work Unit 1.
9. Establish an upstream update/diff/security-review policy.

## Final decision

**Do not add WACRM as a dependency, copy files, or use its runtime now.** Treat WACRM as a documented reference for CRM/inbox/automation/security patterns. Continue with the project's own Agency → ClientAccount contracts, provider abstractions, agent runtime, automation approval model and audit trail.

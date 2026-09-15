# WACRM audit — Work Unit 1

**Status:** completed as a source audit only. No WACRM code was copied, executed, installed, or added as a dependency.

## 1. Audited source

- Repository: `https://github.com/ArnasDon/wacrm.git`
- Owner: `ArnasDon`
- Audited branch: `main`
- Audited commit: `6ed9191189e71d2e69d9380422f9415ecc589266`
- Repository description: self-hostable WhatsApp CRM template.
- `package.json` version observed: `0.8.0`.
- Audit date: 2026-08-18 (environment date).

The commit was obtained independently with `git ls-remote`; source inspection used GitHub raw/API content at the exact commit. The repository was not cloned into the target project.

## 2. Licence and governance

Evidence:

- `LICENSE` states MIT License and copyright `Arnas Donauskas`, 2026.
- `package.json` declares `license: MIT` and author `Arnas Donauskas`.
- README explicitly describes the project as a template intended to be forked, branded and hosted.

Findings:

- MIT permits use, modification, distribution and sublicensing, subject to preserving the copyright and licence notice.
- A future fork/adaptation must preserve `LICENSE` and applicable copyright notices.
- This audit does not clear third-party dependency licences, trademarks, WhatsApp/Meta terms, or commercial branding rights.

Governance recommendation:

- Preserve upstream attribution in any future fork.
- Keep a third-party notices inventory before distribution.
- Do not imply Meta, WhatsApp, WACRM or Hostinger endorsement.
- Obtain a legal review before selling a materially modified fork or offering self-hosted binaries.

## 3. Stack and architecture

Evidence from `README.md`, `package.json`, `tsconfig.json`, `docker-compose.yml`:

- Next.js `16.2.12`, App Router.
- React `19.2.4` and React DOM `19.2.4`.
- TypeScript `^6` in dev dependencies; strict mode enabled.
- Tailwind v4, Radix/Base UI related components, React Flow (`@xyflow/react`), Recharts, dnd-kit.
- Supabase SSR/client SDKs.
- Node engine `>=20.0.0`; package manager npm `10.9.9`.
- Supabase PostgreSQL, Auth, Storage and RLS.
- Docker Compose and Hostinger deployment documentation.
- Public REST API under `/api/v1` and an MCP wrapper over that API.

Architecture evidence:

- `src/app` contains route handlers and dashboard pages.
- `src/components` contains UI, inbox, pipelines, automations, flows and settings.
- `src/lib` contains domain-oriented modules for AI, WhatsApp, automations, flows, contacts, API keys, webhooks and storage.
- The repository is a modular Next.js application, not a separately deployed domain/application/adapter architecture.

## 4. Authentication, users and accounts

Evidence:

- Supabase Auth is used through `@supabase/ssr` in `src/lib/supabase/server.ts` and `src/middleware.ts`.
- Protected dashboard paths and WhatsApp API paths are checked in middleware.
- Migration `017_account_sharing.sql` introduces `accounts`, account roles (`owner`, `admin`, `agent`, `viewer`), invitations, `account_id` columns and `is_account_member` checks.
- Migration `018_account_member_rpcs.sql` uses narrowly scoped `SECURITY DEFINER` RPCs for member role/removal operations.
- Public API keys in migration `026_api_keys.sql` are account-scoped, hashed, revocable and scope-limited.

Finding:

- WACRM evolved from user-scoped data to a single-account-per-user/team model. It is not evidence that the product supports an agency hierarchy.

## 5. Data model and isolation

Evidence:

- Early migration `001_initial_schema.sql` uses `user_id` as the tenant boundary on contacts, tags, conversations, WhatsApp config, templates, pipelines, deals and broadcasts.
- Migration `017` backfills `account_id` and replaces old tenancy policies with membership checks, while retaining `user_id` for ownership/audit semantics.
- Migration `030` uses account-owned AI knowledge documents/chunks and account-scoped retrieval RPCs.
- Public API documentation says keys and resources are account-scoped and cross-account access returns forbidden/not found.

Finding:

- Current isolation is account/team-level. It is not `Agency → ClientAccount`.
- There is no audited first-class `agency_id` / reseller / partner layer in the evidence reviewed.
- WACRM's `account` can be a candidate mapping to `ClientAccount`, but the agency layer, agency permissions and cross-client administration must remain product-owned contracts.

## 6. Supabase, PostgreSQL, RLS and persistence

Evidence:

- Supabase PostgreSQL migrations are the persistence source.
- RLS is enabled on domain and settings tables.
- Policies use `auth.uid()` in the initial schema and `is_account_member(account_id, minimum_role)` after account sharing.
- Storage is used for avatars, flow media and mirrored inbound media; storage is called out as still having user-scoped areas in migration 017, with later rescoping noted as follow-up.
- Knowledge uses PostgreSQL full-text search and optional `pgvector` embeddings with HNSW.
- Supabase service-role clients are used for webhook/background paths that have no browser auth context.

Risks:

- Service-role paths bypass RLS and require application-level ownership checks; the automation engine explicitly checks contact ownership, but this must be reviewed for every future tool.
- Storage scoping is not uniformly proven to be agency/client scoped from the inspected excerpts.
- Hosted Supabase settings are intentionally not fully represented in `supabase/config.toml`; environment/project configuration remains an operational dependency.

## 7. WhatsApp, APIs, webhooks and integrations

Evidence:

- WACRM uses Meta WhatsApp Cloud API directly, not Kapso.
- `src/lib/whatsapp/meta-api.ts` provides named-argument Meta API helpers, phone verification, registration and message operations.
- Webhook route: `src/app/api/whatsapp/webhook/route.ts`; it verifies Meta signatures, resolves WhatsApp config, handles inbound messages/statuses/media/templates and dispatches automation/flow/AI/webhook work.
- Public REST API covers messages, contacts, conversations, broadcasts and outbound webhooks.
- Outbound webhook docs specify HMAC signing, event IDs for deduplication, HTTPS/public-target restrictions, failure counters and auto-disable.
- Templates have API/UI routes for sync, submit and lifecycle handling.
- MCP is a thin wrapper over the scoped REST API and is read-only by default; writes/broadcasts are opt-in.

Finding:

- WACRM provides useful provider-specific behavior, but its Meta Cloud API integration cannot be adopted as the project's Kapso integration without an explicit provider adapter and a separate contract review.
- WhatsApp authority, credentials, template lifecycle and webhook identity should remain behind this project's own `WhatsAppProvider` boundary.

## 8. Automations and workflow builder

Evidence:

- `src/components/automations/automation-builder.tsx` implements a visual builder with triggers, steps, branches and configuration forms.
- `src/components/flows/flow-canvas.tsx` uses React Flow for draggable nodes, edges, branching slots, minimap, controls and persisted positions.
- `src/lib/automations/engine.ts` executes account-scoped triggers, validates contact ownership, handles waits/pending executions, sends messages/templates/interactive messages, tags, assignments, contact fields, deals and webhooks.
- API routes exist for automation CRUD, activation, cron/engine execution and run logs.

Finding:

- The visual workflow experience is the strongest candidate for adaptation/reference.
- It must not be imported as a dependency or copied in Work Unit 1.
- The project's own distinction remains required: agent decisions generate structured events; the deterministic automation engine executes approved flows.

## 9. AI and knowledge

Evidence:

- AI API routes and modules exist for configuration, draft replies, auto-reply, usage, playground, knowledge and reindexing.
- Providers observed: OpenAI and Anthropic modules.
- Knowledge supports lexical PostgreSQL FTS and optional pgvector semantic retrieval.
- Auto-reply has per-conversation and account-level controls in the inspected materials.

Finding:

- Useful reference for provider abstraction, usage limits, knowledge retrieval and human handoff.
- It does not replace the planned FastAPI/LangGraph agent runtime or the project's own agent modes and tool policies.

## 10. Security

Evidence:

- `next.config.ts` defines HSTS, nosniff, frame denial, referrer policy, Permissions Policy and CSP Report-Only.
- WhatsApp access tokens and webhook secrets use AES-256-GCM at rest, with legacy CBC decrypt-only compatibility in `src/lib/whatsapp/encryption.ts`.
- API keys store SHA-256 hashes and are shown once.
- Webhook signatures use HMAC over timestamp plus raw body and include replay protection guidance.
- SSRF restrictions are documented for outbound webhook URLs.
- Rate limiting exists in `src/lib/rate-limit.ts`, but the documented limiter is in-memory and per-process.
- CI/security documentation exists; the inspected CI uses dummy build secrets and runs lint, typecheck, test and build.

Risks:

- In-memory rate limiting does not enforce a global limit across multiple instances.
- CSP is explicitly Report-Only in the inspected `next.config.ts`.
- Service-role/background operations need continuing ownership checks.
- Encryption key handling and rotation are deployment responsibilities.

## 11. Tests, CI, lint, typecheck and build

Evidence:

- `package.json` scripts: `lint`, `typecheck`, `test` (Vitest), `build`, format checks.
- `.github/workflows/ci.yml` runs checkout, Node setup, npm ci, lint, typecheck, test and build.
- Tests are present across AI, API, auth, automation, flows, WhatsApp, webhooks, encryption, rate limiting and storage.
- Migration CI exists in `.github/workflows/migrations.yml`.

Not executed:

- No WACRM dependencies were installed.
- No WACRM tests, lint, typecheck or build were run, per Work Unit 1 rules.
- CI claims are source evidence, not independent verification of the audited commit's green status.

## 12. Clean Architecture, SOLID and Hexagonal compatibility

Assessment: **partial compatibility; not proven as a ready-made architecture**.

Positive evidence:

- Domain-oriented modules under `src/lib`.
- Provider modules for Meta and AI.
- Separate UI components and route handlers.
- API/webhook/engine seams.

Gaps/risks:

- Next.js route handlers, Supabase clients and domain logic are materially coupled in several paths.
- Service-role access and provider calls are not uniformly expressed as ports/adapters.
- No independent application/domain/infrastructure packages were verified.
- The code should be treated as a reference implementation, not imported wholesale into a clean architecture.

Recommendation:

- Adopt behavioral ideas only.
- Define project-owned ports for `WhatsAppProvider`, `ModelProvider`, `CalendarProvider`, repositories, automation execution and audit.
- Keep Agency/ClientAccount policies in the application/domain layer.

## 13. Compatibility with Agency → ClientAccount

Decision basis:

- WACRM has a useful `accounts` concept and account-scoped roles/resources.
- Its audited hierarchy stops at account; the agency/partner layer is not evidenced.
- `user_id` remains in several tables for ownership/audit and must not be mistaken for agency tenancy.

Recommendation:

- Map only conceptually: WACRM `account` may inform `ClientAccount`.
- Build `Agency`, agency membership, client-account administration, partner-level permissions, billing and cross-client audit as first-party contracts.
- Never allow an agent/API key from one client account to select another account merely by request input.

## 14. Fork coupling and divergence risk

Risks:

- Upstream is an active template with rapid version/dependency evolution; the exact audited commit is a point-in-time snapshot.
- A fork that modifies core domain tables, auth, UI and provider code will incur merge conflicts and security review burden.
- Direct adoption of Meta integration would diverge from the planned Kapso abstraction.
- Upstream account semantics are not the planned Agency → ClientAccount model.
- Tailwind/UI conventions and product branding would require substantial adaptation to the project's exact navy/slate/ember + glassmorphism design.
- Future upstream migrations may not be safely replayable after schema divergence.
- Third-party and deployment assumptions (Supabase, Hostinger, Meta) become operational coupling.

Mitigation:

- Prefer reference/adaptation over direct dependency.
- Keep a compatibility boundary if a fork is later chosen.
- Maintain an upstream commit ledger and migration diff review.
- Preserve upstream MIT notices and third-party notices.
- Do not copy source during this Work Unit.

## 15. Data not verified

- No independent live deployment verification.
- No runtime behavior verification against Meta or Supabase.
- No audit of every migration policy and every storage policy.
- No full dependency licence report.
- No independent CI status for commit `6ed9191...`.
- No performance/load testing.
- No confirmation of commercial terms or Meta compliance for a downstream product.
- No verification that all future storage paths are account-scoped.
- No verification that all provider/API routes enforce the future agency boundary.

## 16. Recommendations

1. Do not add WACRM as a dependency in this Work Unit.
2. Adopt the product ideas and behavioral seams, not source files.
3. Adapt concepts: inbox, account-scoped contacts, pipelines, templates, visual flows, knowledge retrieval, API scopes and webhook signatures.
4. Replace with project-owned contracts: Agency → ClientAccount, permissions, audit, `WhatsAppProvider`, `ModelProvider`, automation engine boundaries and agent runtime.
5. Defer direct Meta/WACRM integration until the Kapso pilot and provider abstraction are stable.
6. Block code copying/forking until licence notices, dependencies, data isolation and commercial review are documented.
7. Before any future fork, run the upstream test suite and a migration/RLS/security review against the exact selected commit.

## Audit conclusion

WACRM is a strong reference and possible future accelerator for WhatsApp CRM, visual automations, account-scoped RLS, templates, knowledge and API/webhook governance. It is not currently approved as a dependency or direct code base for this project. The most defensible current decision is: adapt ideas and contracts selectively; keep the project's Agency → ClientAccount, provider boundaries, agent runtime and audit model proprietary and independent.

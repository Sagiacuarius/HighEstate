# Design: Property Catalog

## Technical Approach

Extend the accepted `properties` table in place via forward-only migration `005`, add three relation tables replicating the `004` composite-FK isolation pattern, and layer the catalog behavior exactly on the baseline seams: WU3-branded domain validation, W3-style tenant-scoped psycopg repositories, WU5 command machinery (canonical hash, reserve/replay/conflict, transaction, redacted denial audit, outbox), WU6 read discipline (no account id on routes, one 404 shape, DB-derived counts), and the marked seed/reset ownership contract. The accepted WU6 composition app is not edited; the catalog ships as its own named composition (`api/composition_property_catalog_wu11.py`). No new infrastructure (RLS, auth, Docker, providers) is introduced.

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Table strategy | `ALTER TABLE properties` ADD columns + new relation tables; legacy 004 columns (`location`, `bedrooms`, `price`, `currency`, `availability`) kept, backfilled, and mirrored on new writes | Brief forbids recreating the table; accepted WU3/WU5/WU6 repositories and harnesses read/write legacy columns — dropping them in the same unit would break the green regression requirement. Drop is deferred to a later migration. |
| Enums | `text` + `CHECK` constraints (widening the 004 CHECK in place for `operation_type`), not PostgreSQL native enums | Matches every accepted migration so far (`004` uses CHECK); native enums complicate future widening; domain layer validates before SQL anyway. |
| `reference_code` uniqueness | `UNIQUE NULLS NOT DISTINCT (client_account_id, reference_code)` (PG18) | Per-account uniqueness required by spec; most legacy/foreign rows have no code, so NULLs must coexist. |
| Commercial status | New nullable `status` column with CHECK + backfill `CASE availability … ELSE 'paused'`; legacy `availability` retained and mirrored | Brief says nullable unless stated; accepted seed/WU3 repo do not write `status`, so a NOT NULL would break them. `sold`/`rented` intentionally have no legacy equivalent (documented). |
| Price/currency replacement | Add `price_amount`/`price_currency` with CHECKs and backfill from `price`/`currency` (non-USD/ARS → USD); keep legacy NOT NULL columns, mirror values on writes | Data-preserving per brief; mirror keeps legacy reads truthful for demo data. |
| Amenity catalogue | Global `property_amenities` (unique name), per-account membership only via `property_amenity_rel` carrying `client_account_id` + composite FK | The vocabulary is shared; the *link* is the tenant-scoped fact. FK-restrict on delete keeps catalogue stable. |
| Agent responsibility | `responsible_agent_id` FK to foundation `users` with `ON DELETE RESTRICT`; membership authorization enforced in the command layer, not by FK | The FK proves referential integrity only; "is this agent a member of the acting account" is a tenant-context check the repository layer owns. |
| Interests | `UNIQUE (property_id, contact_id)` at the schema level; command is idempotent per key AND per pair; NEVER writes `leads` | Brief: lightweight link, no lead creation; duplicate matches are meaningless. |
| Composition | Separate named FastAPI app (routes under the same `/v1/crm` prefix discipline), reusing WU5 idempotency/audit/outbox ports and the demo identity adapter | Keeps the accepted WU6 app byte-identical for regression; the catalog composes the seams, it does not fork them. |
| Seed | Additive `validation/mvp_seed_005.sql` (marked rows: 4 enriched A properties across capturing/available/reserved/sold, B one terminal property, amenities + rels + a seed contact link/interest), accepted `mvp_seed.sql` untouched | Preserves the accepted fail-closed ownership gates; the new script converges under re-apply with the same marker discipline. |

## Contracts, Invariants, and Flow

Command flow per WU5: `resolve TenantContext (CurrentIdentity + membership) → reserve(actor, operation, key, canonical_hash) → validate domain (enums, money Decimals, transition guard) → scoped write(s) with mandatory client_account_id predicate → append audit + outbox → commit → response`. Replay returns the stored canonical result; hash conflict fails closed with zero mutation; denials roll back and append one separate redacted `outcome=denied` audit record with correlation only.

Status machine (domain-owned, single source): `capturing→{available,paused}`, `available→{reserved,paused}`, `reserved→{sold,rented,paused}`, `paused→{capturing,available,reserved}`, `sold`/`rented` terminal. `UpdateProperty` refuses status changes; the transition guard is a pure function tested without a database.

Legacy mirror invariants on new writes: `availability` mirrors `status` (`sold`/`rented` → `reserved`), `price`/`currency` mirror `price_amount`/`price_currency` (mirror currency only when USD/ARS), `location` mirrors `address`, `bedrooms` mirrors `rooms` when present. Declared so accepted reads never diverge from enriched facts for demo data.

Isolation: `property_amenity_rel`, `property_contacts`, `property_interests` each carry `client_account_id NOT NULL` and composite FKs to `(properties.id, client_account_id)` / `(contacts.id, client_account_id)` — an A-context row can physically reference only A parents. Reads filter by `client_account_id = context.client_account.id` in every statement; foreign and missing ids share the accepted 404 envelope shape.

## Files and Composition

- `infra/migrations/005_property_catalog.up.sql` (+ reviewed-only destructive `.down` if required by runner convention; forward-only up is authoritative).
- `api/domain/property_catalog.py` — `PropertyRecord`, relation dataclasses, enums, `PROPERTY_STATUS_TRANSITIONS`, `next_property_status`, money/URL/int guards; reuses `api/domain/commercial_crm.py` validators.
- `api/adapters/postgres_property_catalog.py` — the four repositories, `_require(context)` guard pattern from the accepted CRM repos.
- `api/application/property_commands_wu11.py` — the five commands over the WU5 port machinery.
- `api/queries/property_reads_wu11.py` — list/detail/contact reverse view with filter parsing (status, property_type, min/max price, city).
- `api/composition_property_catalog_wu11.py` — routes: `GET /v1/crm/properties`, `GET /v1/crm/properties/{id}`, `GET /v1/crm/contacts/{id}/properties`, `GET /v1/crm/amenities`, plus `POST …/commands/{create|update|status|link-contact|interest}`.
- `infra/migrations/validation/mvp_seed_005.sql` — marked enriched seed; runner already orders `005` after `004`.
- `api/test_property_catalog_wu11.py` — real-PostgreSQL scenario harness (`run_all()` shape, live module entrypoint), new disposable base `production_runtime_mvp_wu11`.

## Testing Strategy

Harness covers: 005 backfill visible through legacy reads; filtered list vs direct SQL oracles; detail aggregation; create idempotent replay/conflict; per-account `reference_code` uniqueness incl. cross-account reuse; update partial merge + mirror sync + status-change refusal; full legal transition path and each illegal edge (rollback + single denial audit + no outbox); paused reversibility; link/interest cross-account denials; interest replay no-op with lead-count invariant; A/B isolation and shared 404 shape; seed re-apply convergence and fail-closed block on unmarked children. Regression: `compileall`, WU3–WU10 harnesses on their documented bases, `npm` typecheck/tests — reported with exit codes in `work-unit-11-evidence.md`. No runner is configured in `openspec/config.yaml`; evidence distinguishes executed runs from syntax-only checks.

## Risks and Rollback

Main risk is drift between legacy and enriched columns; mitigated by write-side mirroring inside the same transaction and oracle assertions in the harness. `NULLS NOT DISTINCT` pins the migration to PG ≥15 (local baseline is 18.6, declared). Rollback: revert WU11 code/seed/routes; discard `production_runtime_mvp_wu11`; earlier bases and migrations `001–004` untouched by construction.

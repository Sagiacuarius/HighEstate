# Proposal: Property Catalog

## Intent

Make `Property` a first-class entity of the real-estate vertical so the future agent matches against a queryable catalog instead of loose facts: rich listing attributes, role-based contact links, lightweight interests (never auto-leads), and its own commercial status machine. Built strictly on the validated WU1–WU6 patterns of `mvp-commercial-demo` (baseline): tenant-scoped repositories, atomic idempotent commands with audit/outbox, versioned read APIs, deterministic marked seed/reset.

## Scope

### In Scope
- Migration `005_property_catalog`: EXTENDS the existing `properties` table from `004_commercial_crm` (never recreate it, never edit `001–004`); adds catalogue columns, the commercial `status` machine column, `price_amount`/`price_currency` replacements with backfill, and three new relation tables: `property_amenities` + `property_amenity_rel` (M:N catalogue), `property_contacts` (role links), `property_interests` (idempotent per property+contact, UNIQUE enforced).
- Enriched domain model and four PostgreSQL repositories (`PropertyCatalogRepository` extension of the property surface, `PropertyAmenityRepository`, `PropertyContactRepository`, `PropertyInterestRepository`), all requiring `TenantContext` with a mandatory `client_account_id` predicate.
- Five atomic commands reusing the WU5 machinery (canonical hash, reserve/replay/conflict, transaction, redacted denial audit, outbox): `CreateProperty`, `UpdateProperty`, `ChangePropertyStatus` (transition guard: capturing → available → reserved → sold/rented; paused allowed and reversible), `LinkContactToProperty` (role), `RegisterPropertyInterest` (idempotent per property+contact, never creates a lead).
- Versioned read APIs following the WU6 pattern: filtered properties list, property detail (amenities + linked contacts + interests), contact→properties reverse view.
- Deterministic demo seed: four enriched properties across distinct statuses (capturing, available, reserved, sold/rented) with varied amenities, integrated with the existing fail-closed seed/reset ownership contract.
- Regression: typecheck, `npm test`, `compileall`, and the accepted WU3–WU10 Python harnesses must stay green against a NEW disposable base `production_runtime_mvp_wu11`; earlier bases untouched.

### Out of Scope / No-goals
- Map UI over `latitude`/`longitude` (columns exist, no behaviour).
- Per-property reports (days on market, conversion) — needs real volume.
- Multi-titleholder UI (M:N supported in data only).
- Kapso/WhatsApp, LLM/LangGraph, RLS, productive auth, Docker, CI/CD, deployment.
- Dropping legacy 004 columns (`location`/`bedrooms`/`price`/`currency`/`availability`): deferred so accepted WU3/WU5/WU6 code paths keep working; new writes go through the enriched columns.

## Capabilities

### New Capabilities
- `property-catalog`: enriched, queryable property entity with amenities catalogue, role-based contact links, interests, and a commercial status machine, exposed through tenant-scoped commands and reads.

### Modified Capabilities
- `commercial-crm-demo` (requirements only, no baseline edits): the properties surface gains enriched attributes and status; `leads.selected_property_id` remains the only lead↔property link and is NOT set by interest registration.

## Approach

Forward-only, data-preserving `ALTER`s over the 004 table plus new relation tables replicating the 004 composite-FK isolation pattern (`(child_id, client_account_id)` FKs, `demo_seed_key` ownership markers). Legacy columns are mirrored on new writes so accepted read paths and harnesses stay valid. A separate named FastAPI composition (`property-catalog` WU) carries the new routes and commands; the accepted WU6 app is untouched. Seed extends via an additive `mvp_seed_005.sql` so the accepted `mvp_seed.sql` ownership gates remain intact.

## Acceptance

- [ ] `005` applies on top of a base with `001–004` and backfills existing rows (status/price/currency/address/rooms); `001–004` files unmodified.
- [ ] Create/Update/Status/Link/Interest commands are atomic and idempotent (replay canonical, reuse-with-different-payload conflict), audited, outbox-published only after commit; denials roll back with one redacted audit and no success event.
- [ ] Illegal status transitions and cross-account targets fail closed; A/B isolation proven; `reference_code` unique per account.
- [ ] Interest replay for the same (property, contact) creates no duplicate and never creates or mutates a lead.
- [ ] Filtered reads (status, property_type, price range, city) return DB-derived results; detail includes amenities, contacts, interests; 404 shape shared for missing-in-scope and foreign ids.
- [ ] Seed/reset remain deterministic and fail-closed over the new marked tables; re-applying seed converges without duplicates.
- [ ] Full regression sweep green on `production_runtime_mvp_wu11`; earlier bases untouched; `work-unit-11-evidence.md` records commands and exit codes.

## Review Workload Forecast

Estimated authored implementation: 1,800–2,400 lines (SQL ~300, Python domain/repos/commands/reads/composition/harness ~1,500, seed ~150, contracts/tests ~300). 800-line budget risk: High. Chained PRs recommended: Yes — this change is delivered as one reviewable unit boundary (WU11) atop the accepted chain; decision needed before apply: No. Delivery: feature-branch-chain, own verification and rollback boundary.

## Risks and Rollback

Extending an accepted table risks breaking WU3/WU5/WU6 paths that write the legacy columns; mitigated by keeping legacy columns NOT NULL-compatible, mirroring on new writes, and running the accepted harnesses on the new base as regression evidence. Backfill mapping (`unavailable → paused`, non-USD/ARS currency → USD) is declared, silent-defaulting only for legacy demo data. Rollback: revert the WU11 code/seed/routes slice and the disposable `production_runtime_mvp_wu11` base; `005` down script is reviewed-only destructive; `001–004` and prior bases untouched.

## Open Decisions

Confirm only at review time: whether legacy column drop ships in a later migration, and whether the dashboard UI surfaces the catalog (deferred; reads are contract-first for a future UI unit).

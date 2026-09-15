# Tasks: Property Catalog (WU11)

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 1,600–2,200 authored lines (SQL ~270, Python domain/repos/commands/reads/composition/harness ~1,700, seed ~180) |
| 800-line budget risk | High |
| Chained PRs recommended | Yes — single unit boundary WU11 on the feature-branch-chain, targeting after WU10 |
| 400-line budget risk | High |

Decision needed before apply: No. Delivery: auto-chain, feature-branch-chain; tests and evidence stay with the behavior they prove.

## Status note

Prior-session code exists for every phase below (migration `005`, domain, repositories, commands, reads, composition, `mvp_seed_005.sql`, harness) but is UNVERIFIED: a created file is not a passed harness. Do not check a box below until its verification command has actually been executed with the recorded exit code in `work-unit-11-evidence.md`.

## Phase Checklist

- [ ] Phase 1 — Migration `005_property_catalog.up.sql` extends `properties` (columns, CHECK widening, `reference_code` per-account uniqueness, data-preserving backfill), creates `property_amenities`, `property_amenity_rel`, `property_contacts`, `property_interests` with composite `(id, client_account_id)` FKs and `demo_seed_key` markers; never edits `001–004`.
  - Verify: `python infra/migrations/runner.py` applies 001→005 on fresh `production_runtime_mvp_wu11`; SQL oracles assert backfill (`status`/`price_amount`/`price_currency`/`address`/`rooms` non-null on legacy rows); FK/UNIQUE rejection probes; runner prerequisite gate (005 refuses without 004).
- [ ] Phase 2 — Domain (`api/domain/property_catalog.py`): frozen `PropertyRecord` + relation records, enum/money/URL guards reusing baseline validators, pure `PROPERTY_STATUS_TRANSITIONS` + `next_property_status`.
  - Verify: pure unit scenarios inside `python -m api.test_property_catalog_wu11` (no DB) + `python -m compileall api`.
- [ ] Phase 3 — Repositories (`api/adapters/postgres_property_catalog.py`): `PropertyCatalogRepository`, `PropertyAmenityRepository`, `PropertyContactRepository`, `PropertyInterestRepository`; context guard before SQL; mandatory predicate on every statement; composite-FK states mapped to non-enumerating errors.
  - Verify: A/B isolation and predicate scenarios in the real-DB harness.
- [ ] Phase 4 — Commands (`api/application/property_commands_wu11.py`): Create/Update/ChangeStatus/LinkContact/RegisterInterest over the WU5 reserve/replay/conflict machinery with audit+outbox; update partial merge with legacy-mirror sync and status-change refusal; interest idempotent per (property, contact) and never touches `leads`.
  - Verify: replay returns canonical, key-reuse conflict fails with zero mutation, illegal transition = one redacted denial audit and no success event/outbox, lead count invariant after interest.
- [ ] Phase 5 — Reads + composition (`api/queries/property_reads_wu11.py`, `api/composition_property_catalog_wu11.py`): filtered list (status/property_type/price range/city), detail (amenities+contacts+interests), `contacts/{id}/properties`, amenity catalogue; no route accepts account id; foreign and missing ids share one 404 shape; command routes under `/v1/crm/properties/...`.
  - Verify: GET-after-command durability, forged `clientAccountId` cannot change scope, direct-SQL oracles for every count/filter result, envelope 404/405 discipline.
- [ ] Phase 6 — Seed (`infra/migrations/validation/mvp_seed_005.sql`): marked enriched properties covering capturing, available, reserved, sold; amenity catalogue + links; accepted `mvp_seed.sql` untouched.
  - Verify: re-apply convergence (counts identical, no duplicates), fail-closed block on unmarked children, plain reset passes afterwards.
- [ ] Phase 7 — Regression + evidence: `python -m compileall api infra`; accepted WU3–WU10 harnesses on their documented bases; `npm` typecheck + tests (no catalog UI shipped this unit — web runtime client untouched); write `work-unit-11-evidence.md` with the PASS matrix, exact commands, exit codes, prior-bases-untouched snapshot; status "implemented, pending review".
  - Verify: every command run and reported honestly; no acceptance claim.

## Deferred (documented, not built)

Legacy-column drop (`location`/`bedrooms`/`price`/`currency`/`availability`) after accepted WU11; map UI, per-property reports, multi-titleholder UI, catalog UI wiring in `web/`, TS contract mirror for new read shapes if the UI unit needs them.

## Explicitly Out of Scope

Kapso/WhatsApp, LLM/LangGraph, RLS, productive auth, Docker, CI/CD, touching migrations `001–004`, touching accepted bases `production_runtime_mvp_wu2`–`wu10`.

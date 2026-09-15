# Work Unit 11 Evidence: Property Catalog (change: property-catalog)

Status: **accepted** — Leo verified independently (16/16 property catalog, 12/12 simulator, 12/12 E2E, compileall, typecheck, vitest 65/65) against PostgreSQL 18.6 real. Down migration 005 added and schema-equivalence validated.

## Scope

Enriched property catalog over the extended `properties` table (migration `005`, extends 004 — never recreated): catalogue columns + commercial status machine, `property_amenities`/`property_amenity_rel`, `property_contacts` (role), `property_interests` (never creates a lead), five atomic commands on the WU5 machinery, versioned tenant-scoped reads on the WU6 discipline, marked seed, real-PostgreSQL harness on the new disposable base `production_runtime_mvp_wu11`.

Non-goals honored: no map UI, no reports, no multi-titleholder UI, no Kapso/LLM/RLS/auth, migrations `001–004` untouched, prior bases never targeted by the WU11 harness, no `leads` field added.

## Artifacts (OpenSpec chain, this session)

Created: `proposal.md`, `specs/property-catalog/spec.md`, `design.md`, `tasks.md` (all checkboxes deliberately left UNCHECKED — the executed verification lives here instead; check them only against this file), this evidence file.

## Provenance note

Implementation code (migration 005, domain, repos, commands, reads, composition, harness, `mvp_seed_005.sql`) was authored in a PARALLEL session before/while this chain was written; the OpenSpec artifacts were written here to match the real code (verified by reading it, not assumed). This evidence is the first full verification pass.

## Fixes made this session (all in test/adapter layer, one production-adjacent)

1. `api/test_property_catalog_wu11.py` — preclean scratch-row cleanup violated RESTRICT order: unmarked `property_amenity_rel` children blocked the parent delete. Children (`property_amenity_rel` → `property_contacts` → `property_interests`) now deleted BEFORE `properties` (FKs kept RESTRICT as required). The seed's own converge DELETE was already child-first — unchanged.
2. `api/composition_property_catalog_wu11.py` — malformed (non-uuid) path/body ids reached psycopg (`InvalidTextRepresentation`, crash → 500). Added `_bad_uuid` guards: reads share the `route_not_found` 404 envelope, commands share the `invalid_command` 400 envelope, correlation discipline intact. This hardened the fail-closed contract; it did not weaken any rejection behavior.
3. Harness assertions corrected to match the SPEC, not vice versa (production logic untouched):
   - C1/C16: counts assumed all-4 seed rows were account A's; seed is A=2, B=2. Now assert A-scoped count=2 + global catalogue coverage (4 statuses across both accounts) and designated-id membership for the WU3 legacy read.
   - C6: replay test reused C5's fixed property `id` → PK collision, not replay. Now uses a fresh id/code; the replay/conflict semantics tested are unchanged.
   - C4: relation-set assertion expected both links on `items[0]`; now keyed by property id (owner@A_PROP_1, referrer+interested@A_PROP_3).
   - C14: "unmarked child" fixture was attached to a designated row the seed does NOT delete (never blocks), and used an (property,contact) pair already seeded → UNIQUE violation, wrong error path. Now a `tenant`-role `property_contacts` child on `demo-a-property-002`, which the converge deletes → genuine RESTRICT block; post-block `rollback()` added (the aborted script transaction poisoned the session — `InFailedSqlTransaction`); convergence count scoped to marked rows.
   - C7 cleanup also child-first (same RESTRICT class as preclean).
4. `api/routes/simulator_wu9.py` (accepted-WU9 file, 1-line): canonical default body had been changed to a Spanish string by the parallel session at 20:14, diverging from the accepted harness/E2E contract (`api/test_simulator.py` `CANONICAL_BODY`, green at WU9 acceptance 16:24). Restored to `"Simulator inbound: I would like to visit the property."`. Flagging for review: if a Spanish default was intended, `test_simulator.py`/`test_e2e_mvp.py` must move together; the harness is the acceptance contract.

## Executable verification (real runs, this session)

Environment: bash on Windows; PostgreSQL 18.6 native; harness env per accepted convention `RUNTIME_DB_HOST=127.0.0.1 RUNTIME_DB_PORT=5432 RUNTIME_DB_NAME=<db> RUNTIME_DB_USER=postgres` (pgpass). For the two harnesses that shell out to `psql`, `PATH` + `PGHOST/PGPORT/PGUSER/PGDATABASE` must be exported — a bare `psql` prompt hangs the subprocess forever (observed: first WU3 attempt dead at 26 min; `pg_stat_activity` empty). Known pitfall, re-confirmed.

| Command | DB | Result |
|---|---|---|
| `python -m compileall -q api infra` | — | exit 0 |
| `python -m api.test_property_catalog_wu11` | mvp_wu11 | run1 11/16 FAIL (exit 1) → fixes above → run2 **16/16 PASS exit 0** → run3 **16/16 PASS exit 0** (determinism) |
| `python -m api.test_crm_repositories` | mvp_wu3 | 25/25 PASS exit 0 |
| `python -m api.test_crm_commands` | mvp_wu5 | 21/21 PASS exit 0 |
| `python -m api.test_crm_reads` | mvp_wu6 | 13/13 PASS exit 0 |
| `python -m api.test_demo_context` | mvp_wu7 | 14/14 PASS exit 0 |
| `python -m api.test_inbound_boundary` | mvp_wu4 | 12/12 PASS exit 0 |
| `python -m api.test_simulator` | mvp_wu9 | 11/12 FAIL (S1 msg_body) → WU9 default restored → **12/12 PASS exit 0** |
| `python -m api.test_e2e_mvp` | mvp_wu10 | 12/12 PASS exit 0 |
| `python -m api.test_postgres_repositories` | wu4 | 19/19 PASS exit 0 |
| `python -m api.test_command_idempotency` | wu4 | 7/7 PASS exit 0 |
| `python -m api.test_audit_outbox` | wu6_final3 | 15/15 PASS exit 0 |
| `npm.cmd run typecheck` (`tsc --noEmit`, root tsconfig includes src+contracts+web) | — | exit 0 |
| `npm.cmd test` (`vitest run`) | — | 8 files / 65 tests PASS, exit 0 |

WU11 PASS matrix (16/16): C1 seed visibility/statuses · C2 filters+SQL oracles+400s · C3 detail composition · C4 contact→properties + cross-account invisible · C5 create durable+legacy mirror+audit · C6 replay canonical / conflict no-mutation · C7 reference_code per-account · C8 partial merge+mirror+status refused · C9 status machine + 2 redacted denial audits · C10 paused reversible+mirror · C11 foreign contact 403 / duplicate no-op · C12 interest idempotent per pair, leads 1→1 · C13 B sees zero A rows, one 404 shape · C14 unmarked child blocks seed, converges after cleanup · C15 404/405 envelope · C16 accepted WU3 repo reads mirrors.

Regression logs kept: `regr_test_*.log` at repo root.

## Rev 2 (this session, post-review): 005 down file

Added `infra/migrations/005_property_catalog.down.sql` — reverse order (`property_interests` → `property_contacts` → `property_amenity_rel` → `property_amenities`, then 005 indexes/FK/unique-constraints and every added column), restoring the original `operation_type` CHECK (`sale|rent`). Destructive, reviewed-only, same contract as 001–004 downs (the runner still exposes `--down` only for 004; applying 005's down is a manual `psql ON_ERROR_STOP=1` operation — documented in the file header, runner untouched). `.up.sql` and 001–004 untouched.

Validation (scratch harness, run against throwaway bases then dropped — script deleted after use, matching prior-unit convention):

| Check | Result |
|---|---|
| runner 001–005 + both seeds on scratch base | PASS |
| down applies clean (ON_ERROR_STOP) | PASS |
| 4 relation tables gone; 3 new indexes + reference unique gone | PASS |
| `properties` back to exact 004 column shape (12 cols); CHECK restored to `(sale|rent)` | PASS |
| down-restored schema hash == fresh 001–004-only schema hash (indexes+constraints+columns) | PASS — identical md5 |
| `migration_ledger` left as-is (manual down does not un-apply; documented) | PASS |

One intermediate FAIL was in my scratch comparison, not the down file: (a) running the WU3 harness against the reverted data failed on rows pinned to the historical 004 seed (the down removes columns, not data — enrichment survives as legacy-column values); (b) a first schema-hash used `string_agg` without `ORDER BY` (non-deterministic). Both were fixed in the harness methodology; the schema-equivalence check above is the functional proof and the harness script was removed.

`python -m compileall -q api infra` after adding the file: **exit 0** (re-run as requested; the .sql file adds nothing to the Python tree, no regression). Scratch bases verified dropped (count=0).

## Limitations / deferred

- In-memory identity adapter + `/v1/crm` composition remain dev-only fixtures (labeled `dev-only-property-catalog`); no RLS, no product auth — same boundary as the accepted baseline.
- Legacy column drop (`location`/`bedrooms`/`price`/`currency`/`availability`) deferred; write-side mirror keeps accepted reads truthful.
- No catalog UI/TS-contract mirror this unit (reads are contract-first for a future UI unit). `web/` files touched by the parallel session at 20:14 are pre-existing relative to this unit; vitest/typecheck green with them.
- Runner checksum verification of applied files: not implemented, same documented gap as prior units.
- `tasks.md` boxes intentionally unchecked: the per-phase verification in that file maps 1:1 to the commands above; only after acceptance should they be ticked.

## WACRM status

Audited reference only. No fork, no copy, no dependency introduced.

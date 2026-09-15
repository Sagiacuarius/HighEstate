# Design: Audit Screen

## Technical Approach

Pure read slice over the accepted `audit_records` table (003): new SQL adapter method + provider-neutral query service (WU6 split), exposed through an `audit_screen` APIRouter mounted onto the accepted WU8 app exactly the way WU9 mounted the simulator (shared session/resolver/cache state, no edits to accepted route internals). Contract mirror in `contracts/runtime-v1.ts`, typed client method, and one new dashboard view. No migration, no writes.

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Capture | None. Read-only over `audit_records` as written by WU5/WU9/WU11 + redacted denial audits | Brief: expose, don't capture. Acceptance requires accepted harnesses pass unmodified. |
| Migration | None. Reuse `audit_records_account_created_idx (client_account_id, created_at, id)`; no new index | It covers scope + keyset ordering exactly; filters add predicates on top of that scan — fine at demo volume. Declared limitation for real volume (partial indexes deferred, not invented now). |
| contact_id mapping | `EXISTS (SELECT 1 FROM leads l WHERE l.id = a.resource_id AND l.client_account_id = a.client_account_id AND l.contact_id = %s AND a.resource_type = 'lead')` OR `a.metadata->>'contact_id' = %s` | Audited writers: WU5 lead commands use resource_type 'lead' + lead id; WU11 link/interest audit extras carry `contact_id` in metadata. No new columns; `resource_id` is text, cast-compare with validated uuid string. |
| property_id mapping | `a.resource_type IN ('property','property_interest') AND a.resource_id = %s` OR lead-with-selected_property via the same EXISTS shape | Audited: every WU11 command sets resource_id to the **property** id (interest row included, per `property_commands_wu11.py` L366–369); lead↔property link lives in `leads.selected_property_id`. Message/conversation audits cannot be mapped to a property — documented gap, not invented. |
| user_id / action / range | Direct `actor_id`, exact `action`, inclusive `created_at >= from AND <= to` (both validated ISO-8601 via `datetime.fromisoformat`, range sanity checked before SQL) | Trivially backed by stored columns. |
| Pagination | Keyset `(created_at, id) < (cursor_ts, cursor_id)` DESC with opaque cursor = base64url(`iso_ts|id`); HMAC-free — server re-validates shape and re-applies the account predicate, so a foreign/valid-shape cursor can only ever page within scope. Tampered → 400 `invalid_query`. No offset mode. | Log grows; stable without snapshot. Cursor carries no authority. |
| limit | Default 25, max 100, validated integer | Mirrors accepted `_validate_activity_limit` discipline. |
| Redaction | Project the same fields as WU6 `activity()` — action, outcome, resource_type/resource_id, actor_id, correlation_id, idempotency_key, non-success `reason` class only, created_at. Never `metadata` wholesale; schema CHECK remains the backstop | Reuses accepted rules verbatim (brief: no new invention). |
| Foreign/ghost filter ids | Empty list (200), never 404 — filters do not enumerate; malformed uuid/date → 400 | Consistent with WU6 ignoring-scope attacks: a filter is a hint, scope is server-derived. |
| Mount point | `build_audit_screen_router()` APIRouter included on the WU8 app (`DEMO_PREFIX=/v1/demo`, route `GET /v1/demo/audit`), reusing app.state composition's resolver/identity + correlation middleware via router-local deps | WU9 precedent: additive router without editing accepted WU8 route bodies. Accepted `/v1/demo/activity` stays byte-identical. |
| UI | New `ViewKey` "auditoria" + nav item beside "Actividad"; filter bar (5 text/select/date controls + limit), table rows, `statusLabel()` for outcome, action string shown raw (it's the operator vocabulary), relative date via existing formatter, "Cargar más" cursor button; empty state; error state shows envelope message + correlation | Brief allows enriching "Actividad" or new view; new view keeps the accepted activity mini-feed and its WU6 contract untouched. |
| Cache | Not cached (operator screen, high write-churn, correctness > latency); documented deviation from cached reads | A stale audit view is worse than a slow one. Acceptance reads must reflect the last command. |

## Contracts, Invariants, and Flow

`GET /v1/demo/audit?contact_id&property_id&user_id&action&from&to&limit&cursor` → 200 `{ items: [AuditEntry], count, limit, has_more, next_cursor, account_id, as_of, correlation_id }`. Parser enforces: no `metadata` key in items (mirror rejects unknown nested objects it didn't declare — same strictness as the accepted read mirrors), enum outcomes, uuid-branded ids, ISO timestamps.

SQL shape (single statement, parameters only, account predicate first):

```sql
SELECT id, action, outcome, resource_type, resource_id, actor_id,
       correlation_id, idempotency_key, metadata->>'reason' AS reason, created_at
FROM audit_records a
WHERE a.client_account_id = %s                       -- always
  [AND (a.resource_type='lead' AND EXISTS (SELECT 1 FROM leads l
        WHERE l.id::text = a.resource_id AND l.client_account_id = a.client_account_id
        AND l.contact_id = %s)) OR a.metadata->>'contact_id' = %s]
  [AND ((a.resource_type IN ('property','property_interest') AND a.resource_id = %s)
        OR (a.resource_type='lead' AND EXISTS (SELECT 1 FROM leads l
        WHERE l.id::text = a.resource_id AND l.client_account_id = a.client_account_id
        AND l.selected_property_id = %s)))]
  [AND a.actor_id = %s] [AND a.action = %s]
  [AND a.created_at >= %s] [AND a.created_at <= %s]
  [AND (a.created_at, a.id) < (from-iso %s, from-uuid %s)]  -- cursor
ORDER BY a.created_at DESC, a.id DESC
LIMIT %s                                              -- limit+1 to detect has_more
```

(Bracketed blocks appended only when the filter validated; every value pre-validated in the service — psycopg parameters, never interpolation. EXISTS subqueries carry their own `client_account_id` equality so a crafted `resource_id` can't reach another account's lead.)

Denied/invalid paths: resolver denial → accepted 401/403 envelope; validation → 400 `invalid_query` (correlation header + body). No audit writes happen on reads (capture is command-side; a read must not pollute the trail it exposes — matches accepted WU6 reads).

## Files and Composition

- `api/adapters/postgres_audit_screen.py` — SQL query module (imports the accepted `_require` context guard and runner).
- `api/queries/audit_screen_wu12.py` — validation, cursor encode/decode, projection (reuses `_iso`/shape helpers, own module; accepted `crm_reads.py` untouched).
- `api/routes/audit_screen_wu12.py` — router + mount helper; wired in `composition_wu8_mvp` with one `include_router` line (additive, like WU9).
- `contracts/runtime-v1.ts` — `CrmAuditResponse`/`AuditEntry` + strict parser; `contracts/runtime-v1.test.ts` fixtures.
- `web/app/demo-api.ts` — `audit(filters, options)`; `web/app/page.tsx` — "Auditoría" view + filter bar; `demo.css` only if a class is missing.
- `api/test_audit_screen_wu12.py` — real-PostgreSQL harness (`run_all()` tuples, live entrypoint) on NEW disposable base `production_runtime_mvp_wu12` (runner 001–005 + `mvp_seed.sql` + `mvp_seed_005.sql` + a command-driven audit fixture: the log is empty on a fresh seed, so the harness runs accepted commands (WU5 inbound via simulator route, WU11 create/link/interest/status) through real compositions to generate audit rows, then filters — never inserts audit rows by hand).
- No `infra/migrations/` change.

## Testing Strategy

Harness scenarios: R1 scope-only + forged `clientAccountId` ignored + NULL-account row invisible; R2 contact filter via lead + via metadata (both writers); R3 property filter via resource_type property/interest + via lead.selected_property_id; R4 user_id/action exact; R5 from/to inclusive boundaries; R6 paging 3×cursor over 7 rows (no dup/gap, has_more correctness, tampered cursor 400); R7 invalid enums/dates/uuids → 400 envelope; R8 no `metadata`/body/secret in full JSON dump; R9 foreign/grey filter ids → empty 200 non-enumerating; R10 accepted `/v1/demo/activity` response unchanged (regression pin); R11 A/B isolation. UI: vitest jsdom renders the real page with stubbed fetch asserting table/filters/empty/load-more (WU8 pattern); build with `NEXT_PUBLIC_*` set as both build and serve. Regression: compileall + WU3–WU11 harnesses on their documented bases + typecheck + npm test; new base only; `work-unit-12-evidence.md` with exit codes; status "implemented, pending review".

## Risks and Rollback

Over-reporting if `resource_id::text` comparisons drift (text column vs uuid) — pinned by casting inside parameterized EXISTS on both sides and oracle SQL checks. Cursor collision at identical timestamps — ordered by `(created_at, id)` composite, id as tiebreak. Rollback: remove router mount + new modules + view + contract section; nothing persistent to unwind.

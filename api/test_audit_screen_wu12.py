"""Audit Screen (change: audit-screen, WU12) real-PostgreSQL harness.

Runs against production_runtime_mvp_wu12 (disposable: runner 001-005 +
mvp_seed.sql + mvp_seed_005.sql). Never touches earlier accepted bases.

The audit trail is generated exclusively by RUNNING the accepted command
surfaces (WU9 simulator inbound on the WU8 app, WU11 property
create/link/interest/status-change on the catalog app, WU6 cross-context
denial via the accepted tenant-resource command) — never by hand-inserting
audit rows. The only exception is proving that a server-produced
NULL-account denial row (written by the accepted audited command's own
denial recorder) is invisible to scoped reads.

Covers: server-derived scope + forged clientAccountId ignored + NULL-account
invisible; contact filter via lead relation AND via metadata; property filter
via resource_id AND via lead.selected_property_id; user/action exact filters;
inclusive date-range boundaries; keyset paging with no dup/gap + tampered
cursor 400; validation 400 envelope (bad uuid/date/range/limit); no metadata
or raw bodies in any dump; foreign/grey filter ids -> empty 200; accepted
/v1/demo/activity unchanged and consistent with the audit projection; A/B
isolation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import load_database_configuration
from api.composition_property_catalog_wu11 import (
    CMD_CREATE,
    CMD_INTEREST,
    CMD_LINK,
    CMD_STATUS,
    PROPERTIES_ROUTE,
    build_property_catalog_application,
)
from api.composition_wu8_mvp import build_mvp_wu8_application
from api.identity.demo_identity import AbsentDemoIdentityAdapter, DemoIdentityAdapter
from api.routes.audit_screen_wu12 import AUDIT_PATH
from api.routes.simulator_wu9 import SIMULATE_PATH

ROOT = Path(__file__).resolve().parent.parent
SEED_FILES = [ROOT / "infra" / "migrations" / "validation" / "mvp_seed.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_005.sql"]

AGENCY_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
USER_SEEDED = "10000000-0000-0000-0000-000000000001"
A_LEAD = "65000000-0000-0000-0000-000000000001"
A_CONTACT = "61000000-0000-0000-0000-000000000001"
A_PROP_1 = "62000000-0000-0000-0000-000000000001"   # selected by A's seed lead
AMENITY_PATIO = "6a000000-0000-0000-0000-000000000003"
GHOST = "0000dead-0000-4000-8000-000000000000"

NEW_PROP_ID = "6d100000-0000-0000-0000-0000000000a1"   # created via command
B_PROP_ID = "6d200000-0000-0000-0000-0000000000b1"


class Sql:
    def __init__(self, kwargs: dict):
        self._kwargs = kwargs
        self.conn: psycopg.AsyncConnection | None = None

    async def open(self) -> None:
        self.conn = await psycopg.AsyncConnection.connect(
            **self._kwargs, autocommit=True, row_factory=dict_row)

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()

    async def q(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self.conn.cursor() as cur:
            await cur.execute(sql, params)
            if cur.description is None:
                return []
            return await cur.fetchall()

    async def one(self, sql: str, params: tuple = ()):
        rows = await self.q(sql, params)
        return rows[0] if rows else None

    async def count(self, sql: str, params: tuple = ()) -> int:
        row = await self.one(sql, params)
        return int(row["n"]) if row else -1


async def run_seed(db: Sql) -> None:
    for path in SEED_FILES:
        body = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                         if not line.lstrip().startswith("\\"))
        async with db.conn.cursor() as cur:
            await cur.execute(body)


async def preclean(db: Sql) -> None:
    """Deterministic baseline: same rules as the accepted WU9/WU11 harnesses —
    children first, restored marked rows, then the seed scripts converge."""
    for stmt in (
        # The catalog child tables are demo-owned on this base (only seed 005
        # and commands write them); mvp_seed.sql's converge deletes the
        # designated properties that 005-marked amenity/contact/interest rows
        # reference, so ALL of them must clear BEFORE either seed re-applies.
        "DELETE FROM property_amenity_rel",
        "DELETE FROM property_contacts",
        "DELETE FROM property_interests",
        "DELETE FROM properties WHERE demo_seed_key IS NULL",
        "DELETE FROM follow_ups WHERE demo_seed_key IS NULL",
        "DELETE FROM messages WHERE demo_seed_key IS NULL",
        "UPDATE leads SET stage_id = '64000000-0000-0000-0000-000000000001', "
        "status='new', updated_at = now() WHERE demo_seed_key = 'demo-a-lead-001'",
        "UPDATE leads SET stage_id = '64000000-0000-0000-0000-000000000002', "
        "status='qualified', updated_at = now() WHERE demo_seed_key = 'demo-b-lead-001'",
        "DELETE FROM leads WHERE demo_seed_key IS NULL",
        "DELETE FROM pipeline_stages WHERE demo_seed_key IS NULL",
        "DELETE FROM conversations WHERE demo_seed_key IS NULL",
        "DELETE FROM contacts WHERE demo_seed_key IS NULL",
        "UPDATE conversations SET unread_count = 1, updated_at = created_at "
        "WHERE id = '66000000-0000-0000-0000-000000000001'",
        "UPDATE conversations SET unread_count = 0, updated_at = created_at "
        "WHERE id = '66000000-0000-0000-0000-000000000002'",
        "DELETE FROM command_idempotency",
        "DELETE FROM audit_records",
        "DELETE FROM outbox_events",
    ):
        params = (NEW_PROP_ID,) if "%s" in stmt else ()
        await db.q(stmt, params)
    await run_seed(db)


def envelope_ok(response, status: int, correlation: str, code: str) -> bool:
    if response.status_code != status:
        return False
    err = (response.json().get("error") or {})
    return (err.get("code") == code
            and err.get("correlation_id") == correlation
            and response.headers.get("x-correlation-id") == correlation)


def _get(client, url, correlation):
    return client.get(url, headers={"X-Correlation-Id": correlation})


def _post(client, route, body, correlation, key=None, **path_ids):
    headers = {"X-Correlation-Id": correlation}
    if key:
        headers["X-Idempotency-Key"] = key
    url = route
    for name, value in path_ids.items():
        url = url.replace("{" + name + "}", value)
    return client.post(url, json=body, headers=headers)


def _audit(client, correlation, **filters):
    qs = "&".join(f"{k}={v}" for k, v in filters.items() if v is not None)
    url = f"{AUDIT_PATH}?{qs}" if qs else AUDIT_PATH
    return _get(client, url, correlation)


def http_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    cfg = load_database_configuration()
    db = Sql(cfg)
    asyncio.run(db.open())
    try:
        asyncio.run(preclean(db))
        # WU8 app: dashboard reads + simulator + audit router (A and B).
        wu8_a = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_A, DemoIdentityAdapter()))
        wu8_b = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_B, DemoIdentityAdapter()))
        # WU11 app: property commands (A and B).
        wu11_a = TestClient(build_property_catalog_application(cfg, ACCOUNT_A, DemoIdentityAdapter()))
        wu11_b = TestClient(build_property_catalog_application(cfg, ACCOUNT_B, DemoIdentityAdapter()))

        with wu8_a as a, wu8_b as b, wu11_a as pa, wu11_b as pb:

            # A0. Fresh seeded base: audit trail is EMPTY (read must not
            # fabricate, seed does not audit). Then generate the trail with
            # accepted commands only.
            r_empty = _audit(a, "wu12-a0")
            ok = (r_empty.status_code == 200 and r_empty.json()["items"] == []
                  and r_empty.json()["total"] == 0
                  and r_empty.json()["correlation_id"] == "wu12-a0")
            record("A0 empty trail on fresh base; envelope meta + correlation", ok,
                   f"total={r_empty.json().get('total')}")

            create_body = {
                "id": NEW_PROP_ID, "reference_code": "WU12-AUD-1",
                "title": "Depo audit WU12", "operation_type": "sale",
                "status": "capturing", "price_amount": "150000",
                "price_currency": "USD", "property_type": "apartment",
                "address": "Sarmiento 100", "city": "Rosario", "rooms": 2,
                "photos": ["https://demo.invalid/a.jpg"],
                "amenity_ids": [AMENITY_PATIO],
            }
            c1 = _post(pa, CMD_CREATE, create_body, "wu12-cmd-create")
            c2 = _post(pa, CMD_LINK, {"contact_id": A_CONTACT, "role": "referrer"},
                       "wu12-cmd-link", property_id=NEW_PROP_ID)
            c3 = _post(pa, CMD_INTEREST, {"contact_id": A_CONTACT, "origin": "manual"},
                       "wu12-cmd-interest", property_id=NEW_PROP_ID)
            c4 = _post(pa, CMD_STATUS, {"status": "available"},
                       "wu12-cmd-status", property_id=NEW_PROP_ID)
            c_illegal = _post(pa, CMD_STATUS, {"status": "sold"},
                              "wu12-cmd-illegal", property_id=NEW_PROP_ID)  # capturing? no: available->sold illegal
            c5 = _post(pb, CMD_CREATE, {**create_body, "id": B_PROP_ID,
                                        "reference_code": "WU12-B-1"},
                       "wu12-cmd-create-b")
            sim = _post(a, SIMULATE_PATH, {"body": "wu12 fixture inbound",
                                           "receivedAt": "2026-09-14T09:00:00.000Z"},
                        "wu12-cmd-inbound")
            trail_ok = all(x.status_code == 200 for x in (c1, c2, c3, c4, c5, sim)) \
                and c_illegal.status_code == 403
            if not trail_ok:
                record("A0b accepted commands produced the trail", False,
                       f"create={c1.status_code} link={c2.status_code} "
                       f"interest={c3.status_code} status={c4.status_code} "
                       f"b={c5.status_code} sim={sim.status_code} "
                       f"illegal={c_illegal.status_code}")

            # R1. Scope: A sees exactly its account's rows (SQL oracle), B's
            # create invisible; forged clientAccountId cannot change scope;
            # a server-produced NULL-account denial row stays invisible.
            from api.commands.audited_update_tenant_resource import AuditedUpdateTenantResource
            from api.adapters.postgres_core import CrossTenantAccessDenied
            from api.tenant_boundary import (
                Agency, ClientAccount, Membership, TenantContext, User)
            # A membership that contradicts the target account makes the
            # accepted denial recorder store client_account_id NULL (its own
            # fail-closed rule): agency/user are real, so the NULL-account row
            # is written for real and must stay invisible to scoped reads.
            bad_ctx = TenantContext(
                user=User(USER_SEEDED, "demo-user"),
                agency=Agency(AGENCY_A, "Agencia A"),
                client_account=ClientAccount(ACCOUNT_A, AGENCY_A, "A"),
                membership=Membership("99999999-0000-0000-0000-000000000099",
                                      AGENCY_A, GHOST,
                                      frozenset({"client_account:write"})),
                correlation_id="wu12-xctx")
            try:
                asyncio.run(AuditedUpdateTenantResource(dict(cfg)).execute(
                    bad_ctx, resource_id=GHOST, request_hash="h",
                    data={"v": 1}, idempotency_key=f"wu12-x-{uuid4().hex[:6]}"))
                denial_written = False
            except CrossTenantAccessDenied:
                denial_written = True
            null_rows = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records "
                "WHERE client_account_id IS NULL AND correlation_id='wu12-xctx'"))
            r_a = _audit(a, "wu12-r1a").json()
            r_a_forged = _audit(a, "wu12-r1b",
                                **{"clientAccountId": ACCOUNT_B}).json()
            sql_a = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE client_account_id=%s",
                (ACCOUNT_A,)))
            leaked = "wu12-cmd-create-b" in json.dumps(r_a)
            ok = (denial_written and null_rows == 1
                  and r_a["total"] == sql_a
                  and all(i["correlation_id"] != "wu12-xctx" for i in r_a["items"])
                  and all(i["correlation_id"] != "wu12-cmd-create-b" for i in r_a["items"])
                  and not leaked
                  and r_a_forged["items"] == r_a["items"]
                  and r_a_forged["account_id"] == ACCOUNT_A)
            record("R1 server scope: A-only, forged account ignored, NULL-account invisible",
                   ok, f"total={r_a['total']} sql={sql_a} null={null_rows}")

            # R2. contact_id filter: lead-path (inbound crm.inbound.process,
            # resource lead A) AND metadata-path (link carries
            # metadata.contact_id). Oracle: independent predicate SQL.
            # NOTE (flagged for review): the accepted interest writer's audit
            # extras carry interest_id/created, NOT contact_id (design.md's
            # "link/interest extras carry contact_id" is inexact for interest),
            # so the interest row surfaces under the property filter (R3), not
            # the contact filter. Fixing the writer is out of scope (capture is
            # accepted); the mapping is pinned to what the writers actually store.
            oracle_contact = asyncio.run(db.q(
                "SELECT correlation_id FROM audit_records WHERE client_account_id=%s AND ("
                " (resource_type='lead' AND EXISTS (SELECT 1 FROM leads l"
                "   WHERE l.id::text = resource_id AND l.client_account_id = audit_records.client_account_id"
                "   AND l.contact_id = %s))"
                " OR metadata->>'contact_id' = %s) ORDER BY correlation_id",
                (ACCOUNT_A, A_CONTACT, A_CONTACT)))
            want = sorted(r["correlation_id"] for r in oracle_contact)
            got_contact = sorted(i["correlation_id"] for i in
                                 _audit(a, "wu12-r2", contact_id=A_CONTACT).json()["items"])
            got_other = sorted(i["correlation_id"] for i in
                               _audit(a, "wu12-r2b", contact_id=GHOST).json()["items"])
            ok = (len(want) >= 2 and "wu12-cmd-inbound" in want
                  and "wu12-cmd-link" in want
                  and got_contact == want and got_other == [])
            record("R2 contact filter via lead relation AND metadata (oracle SQL)", ok,
                   f"n={len(want)}")

            # R3. property_id filter: resource match (create/status/link/
            # interest rows on NEW_PROP_ID) AND lead.selected_property_id path
            # (seed lead A points at A_PROP_1 -> inbound row surfaces there).
            got_prop = _audit(a, "wu12-r3", property_id=NEW_PROP_ID).json()["items"]
            got_prop_ids = {i["correlation_id"] for i in got_prop}
            got_lead_prop = _audit(a, "wu12-r3b", property_id=A_PROP_1).json()["items"]
            lead_prop_ids = {i["correlation_id"] for i in got_lead_prop}
            oracle_prop = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE client_account_id=%s AND ("
                " (resource_type IN ('property','property_interest') AND resource_id=%s)"
                " OR (resource_type='lead' AND EXISTS (SELECT 1 FROM leads l"
                "   WHERE l.id::text = resource_id AND l.client_account_id = audit_records.client_account_id"
                "   AND l.selected_property_id = %s)))",
                (ACCOUNT_A, NEW_PROP_ID, str(NEW_PROP_ID))))
            oracle_lead_prop = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE client_account_id=%s AND ("
                " (resource_type IN ('property','property_interest') AND resource_id=%s)"
                " OR (resource_type='lead' AND EXISTS (SELECT 1 FROM leads l"
                "   WHERE l.id::text = resource_id AND l.client_account_id = audit_records.client_account_id"
                "   AND l.selected_property_id = %s)))",
                (ACCOUNT_A, A_PROP_1, A_PROP_1)))
            # Denied command audits carry resource_id NULL (accepted writer:
            # _record_denial never sets it), so the illegal status change must
            # NOT surface under the property filter — assert its absence.
            ok = (len(got_prop) == oracle_prop == 4
                  and {"wu12-cmd-create", "wu12-cmd-link", "wu12-cmd-interest",
                       "wu12-cmd-status"} == got_prop_ids
                  and "wu12-cmd-illegal" not in got_prop_ids
                  and len(got_lead_prop) == oracle_lead_prop
                  and "wu12-cmd-inbound" in lead_prop_ids)
            record("R3 property filter via resource match AND lead.selected_property_id", ok,
                   f"prop={len(got_prop)} leadprop={len(got_lead_prop)}")

            # R4. user_id (actor) + action exact filters.
            got_user = _audit(a, "wu12-r4a", user_id=USER_SEEDED).json()
            other_user = _audit(a, "wu12-r4b", user_id=GHOST).json()
            got_action = _audit(a, "wu12-r4c", action="crm.property.status_change").json()
            sql_action = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE client_account_id=%s "
                "AND action='crm.property.status_change'", (ACCOUNT_A,)))
            ok = (all(i["actor_id"] == USER_SEEDED for i in got_user["items"])
                  and got_user["total"] > 0 and other_user["items"] == []
                  and len(got_action["items"]) == sql_action >= 2  # legal + denied status rows
                  and all(i["action"] == "crm.property.status_change"
                          for i in got_action["items"]))
            record("R4 user_id=actor_id and exact action filters (oracle SQL)", ok,
                   f"action={len(got_action['items'])}/{sql_action}")

            # R5. Date range boundaries inclusive on both ends.
            items_all = _audit(a, "wu12-r5all").json()["items"]
            bound = items_all[-1]["created_at"]  # oldest row's timestamp
            got_to = _audit(a, "wu12-r5to", **{"to": bound}).json()
            got_from = _audit(a, "wu12-r5from", **{"from": items_all[0]["created_at"]}).json()
            sql_to = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE client_account_id=%s "
                "AND created_at <= %s::timestamptz", (ACCOUNT_A, bound)))
            sql_from = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE client_account_id=%s "
                "AND created_at >= %s::timestamptz", (ACCOUNT_A, items_all[0]["created_at"])))
            past = _audit(a, "wu12-r5past", **{"from": "2099-01-01T00:00:00Z"}).json()
            ok = (got_to["total"] == sql_to and got_from["total"] == sql_from
                  and past["items"] == []
                  and any(i["created_at"] == bound for i in got_to["items"]))
            record("R5 from/to inclusive boundaries (oracle SQL) + empty future", ok,
                   f"to={got_to['total']} from={got_from['total']}")

            # R6. Keyset paging: page through with limit=1; union equals the
            # single big pull; has_more/next_cursor transitions correct.
            big = _audit(a, "wu12-r6big", limit=100).json()["items"]
            paged: list[dict] = []
            cursor = None
            pages = 0
            page_err = ""
            while pages < 20:
                resp = _audit(a, f"wu12-r6p{pages}", limit=1,
                              **({"cursor": cursor} if cursor else {}))
                body = resp.json()
                if resp.status_code != 200:
                    page_err = f"http {resp.status_code}"
                    break
                if body["count"] != 1 and pages < len(big):
                    page_err = f"page {pages} count={body['count']}"
                    break
                paged.extend(body["items"])
                pages += 1
                last_page = not body["has_more"]
                cursor = body["next_cursor"]
                if last_page:
                    if cursor is not None:
                        page_err = "next_cursor present on final page"
                    break
            tampered = _get(a, f"{AUDIT_PATH}?cursor=zzzz-not-a-cursor", "wu12-r6tamper")
            ok = (not page_err and [i["audit_id"] for i in paged]
                  == [i["audit_id"] for i in big] and len(big) > 3
                  and envelope_ok(tampered, 400, "wu12-r6tamper", "invalid_query"))
            record("R6 keyset paging no dup/gap + final page has no cursor + tampered 400",
                   ok, page_err or f"pages={pages} total={len(big)}")

            # R7. Validation fail-closed before SQL: bad uuid/date/range/limit.
            bad_uuid = _audit(a, "wu12-r7a", contact_id="not-a-uuid")
            bad_date = _audit(a, "wu12-r7b", **{"from": "nope"})
            naive_date = _audit(a, "wu12-r7c", **{"from": "2026-01-01T00:00"})
            bad_range = _audit(a, "wu12-r7d", **{"from": "2030-01-01T00:00:00Z",
                                                  "to": "2020-01-01T00:00:00Z"})
            bad_limit = _audit(a, "wu12-r7e", limit=0)
            over_limit = _audit(a, "wu12-r7f", limit=9999)
            bad_action = _audit(a, "wu12-r7g", action="bad action!")
            ok = all(envelope_ok(r, 400, c, "invalid_query") for r, c in (
                (bad_uuid, "wu12-r7a"), (bad_date, "wu12-r7b"),
                (naive_date, "wu12-r7c"), (bad_range, "wu12-r7d"),
                (bad_limit, "wu12-r7e"), (over_limit, "wu12-r7f"),
                (bad_action, "wu12-r7g")))
            record("R7 invalid uuid/date/range/limit/action -> 400 envelope", ok, "")

            # R8. No metadata/raw bodies anywhere; denial rows expose reason only.
            dump = json.dumps(_audit(a, "wu12-r8", limit=100).json())
            denied_row = next(i for i in json.loads(dump)["items"]
                              if i["correlation_id"] == "wu12-cmd-illegal")
            ok = ("metadata" not in dump and "password" not in dump
                  and "raw_body" not in dump
                  and denied_row["outcome"] == "denied"
                  and denied_row["reason"] == "property_status_transition_not_allowed")
            record("R8 zero metadata in dump; denied row exposes only reason class", ok,
                   denied_row["reason"])

            # R9. Foreign/grey filter ids: empty 200, never 404, no leak.
            # The response echoes validated filters in `filters` (by design —
            # the applied filter set), so the no-leak check targets items only.
            r_foreign_prop = _audit(a, "wu12-r9a", property_id=B_PROP_ID)
            r_ghost = _audit(a, "wu12-r9b", contact_id=GHOST)
            foreign_body = r_foreign_prop.json()
            ok = (r_foreign_prop.status_code == 200
                  and foreign_body["items"] == []
                  and r_ghost.status_code == 200 and r_ghost.json()["items"] == []
                  # scope stays A even though the filter value belongs to B
                  and foreign_body["account_id"] == ACCOUNT_A
                  and B_PROP_ID not in json.dumps(foreign_body["items"]))
            record("R9 foreign/grey filter ids -> empty 200 (no enumeration)", ok, "")

            # R10. Accepted /v1/demo/activity unchanged: same shape keys and
            # same account rows as the audit projection for shared fields.
            act = _get(a, "/v1/demo/activity?limit=100", "wu12-r10")
            act_body = act.json()
            aud = _audit(a, "wu12-r10b", limit=100).json()
            act_map = {i["correlation_id"]: i for i in act_body["items"]}
            shared = [i for i in aud["items"] if i["correlation_id"] in act_map]
            consistent = all(
                act_map[i["correlation_id"]]["action"] == i["action"]
                and act_map[i["correlation_id"]]["outcome"] == i["outcome"]
                and act_map[i["correlation_id"]]["resource_type"] == i["resource_type"]
                and act_map[i["correlation_id"]]["correlation_id"] == i["correlation_id"]
                for i in shared)
            ok = (act.status_code == 200 and consistent
                  and {"items", "total", "limit", "account_id", "as_of",
                       "correlation_id"} <= set(act_body)
                  and act_body["total"] == len(aud["items"]))
            record("R10 accepted activity route intact + consistent with audit projection", ok,
                   f"shared={len(shared)}/{len(aud['items'])}")

            # R11. B sees only its own rows. Note: substring checks against
            # "wu12-cmd-create" would false-positive on B's own
            # "wu12-cmd-create-b"; compare exact correlation fields instead.
            b_view = _audit(b, "wu12-r11").json()
            a_exacts = {"wu12-cmd-create", "wu12-cmd-link", "wu12-cmd-interest",
                        "wu12-cmd-status", "wu12-cmd-inbound", "wu12-cmd-illegal"}
            b_corrs = {i["correlation_id"] for i in b_view["items"]}
            ok = (b_view["account_id"] == ACCOUNT_B and b_view["total"] >= 1
                  and not (b_corrs & a_exacts)
                  and "wu12-cmd-create-b" in b_corrs
                  and all(i["actor_id"] in (None, USER_SEEDED)
                          for i in b_view["items"]))
            record("R11 B session: own rows only, zero A correlations", ok,
                   f"b_total={b_view['total']} corrs={sorted(b_corrs)[:4]}")

            # R12. Envelope discipline on the audit mount: wrong method 405,
            # unknown sibling route 404 (accepted app-level handlers), and no
            # identity -> 401 authentication_required (header/body consistent).
            r_405 = a.post(AUDIT_PATH, json={}, headers={"X-Correlation-Id": "wu12-r12a"})
            r_404 = _get(a, "/v1/demo/audit-nope", "wu12-r12b")
            none_app = TestClient(build_mvp_wu8_application(
                cfg, ACCOUNT_A, AbsentDemoIdentityAdapter()))
            with none_app as unauth:
                r_401 = _get(unauth, AUDIT_PATH, "wu12-r12c")
            ok = (envelope_ok(r_405, 405, "wu12-r12a", "method_not_allowed")
                  and envelope_ok(r_404, 404, "wu12-r12b", "route_not_found")
                  and envelope_ok(r_401, 401, "wu12-r12c", "authentication_required"))
            record("R12 401/404/405 accepted envelope discipline on audit mount", ok,
                   f"{r_405.status_code}/{r_404.status_code}/{r_401.status_code} "
                   f"{r_405.json().get('error', {}).get('code')}")
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_audit_screen_wu12():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AUDIT-SCREEN WU12 SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)

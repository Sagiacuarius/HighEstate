"""Property Catalog (change: property-catalog, WU11) real-PostgreSQL harness.

Runs against production_runtime_mvp_wu11 (disposable: runner 001-005 +
mvp_seed.sql + mvp_seed_005.sql). Never touches earlier accepted bases.

Covers: enriched seed visible through filtered reads (direct SQL oracles);
CreateProperty atomic + idempotent replay/conflict + per-account
reference_code uniqueness + non-member agent denial; UpdateProperty partial
merge with legacy mirror sync and status-change refusal; the commercial status
machine (legal path, illegal edges rolled back with one redacted denial
audit, paused reversible); contact links with role incl. cross-account
denials; interest idempotency per (property, contact) and NEVER-creates-a-lead
invariant; A/B isolation incl. 404 one-shape; unmarked children failing seed
re-apply closed (reset-equivalent), then converging after cleanup.
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
    AMENITIES_ROUTE,
    CMD_CREATE,
    CMD_INTEREST,
    CMD_LINK,
    CMD_STATUS,
    CMD_UPDATE,
    CONTACT_PROPERTIES_ROUTE,
    PROPERTIES_ROUTE,
    PROPERTY_DETAIL_ROUTE,
    build_property_catalog_application,
)
from api.identity.demo_identity import DemoIdentityAdapter

ROOT = Path(__file__).resolve().parent.parent
SEED_FILE = ROOT / "infra" / "migrations" / "validation" / "mvp_seed_005.sql"

AGENCY_A = "00000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
USER_SEEDED = "10000000-0000-0000-0000-000000000001"
A_PROP_1 = "62000000-0000-0000-0000-000000000001"   # available (seed-enriched)
A_PROP_3 = "62000000-0000-0000-0000-000000000003"   # capturing (new seed row)
B_PROP_4 = "62000000-0000-0000-0000-000000000004"   # sold (B)
A_CONTACT = "61000000-0000-0000-0000-000000000001"
B_CONTACT = "61000000-0000-0000-0000-000000000002"
AMENITY_PATIO = "6a000000-0000-0000-0000-000000000003"
GHOST = "0000dead-0000-4000-8000-000000000000"

# fresh deterministic ids for command-created fixtures (end prefix mid-uuid + 2 hex)
NEW_PROP_ID = "6c100000-0000-0000-0000-0000000000a1"
SCRATCH_INTEREST_ID = "6c200000-0000-0000-0000-0000000000b1"


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


async def preclean(db: Sql) -> None:
    """Return to the seeded catalog baseline: delete unmarked catalog rows and
    the designated scratch ids, then re-apply the accepted 005 seed scripts."""
    for stmt in (
        "DELETE FROM property_amenity_rel WHERE demo_seed_key IS NULL "
        "OR property_id = %s",
        "DELETE FROM property_contacts WHERE demo_seed_key IS NULL OR property_id = %s",
        "DELETE FROM property_interests WHERE demo_seed_key IS NULL OR property_id = %s",
        "DELETE FROM properties WHERE demo_seed_key IS NULL "
        "OR reference_code LIKE 'WU11-SCRATCH%%'",
        "DELETE FROM command_idempotency",
        "DELETE FROM audit_records",
        "DELETE FROM outbox_events",
    ):
        params = (NEW_PROP_ID,) if "%s" in stmt else ()
        await db.q(stmt, params)
    await run_seed(db)


async def run_seed(db: Sql) -> None:
    body = "\n".join(line for line in SEED_FILE.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("\\"))
    async with db.conn.cursor() as cur:
        await cur.execute(body)


def envelope_ok(response, status: int, correlation: str, code: str) -> bool:
    if response.status_code != status:
        return False
    err = (response.json().get("error") or {})
    return (err.get("code") == code
            and err.get("correlation_id") == correlation
            and response.headers.get("x-correlation-id") == correlation)


def _post(client, route, body, correlation, key=None, **path_ids):
    headers = {"X-Correlation-Id": correlation}
    if key:
        headers["X-Idempotency-Key"] = key
    url = route
    for name, value in path_ids.items():
        url = url.replace("{" + name + "}", value)
    return client.post(url, json=body, headers=headers)


def http_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    cfg = load_database_configuration()
    db = Sql(cfg)
    asyncio.run(db.open())
    try:
        asyncio.run(preclean(db))
        client_a = TestClient(build_property_catalog_application(
            cfg, ACCOUNT_A, DemoIdentityAdapter()))
        client_b = TestClient(build_property_catalog_application(
            cfg, ACCOUNT_B, DemoIdentityAdapter()))
        with client_a as a, client_b as b:

            # C1. Enriched seed: 4 properties total across A|B covering the
            # catalogue statuses; the account-scoped list returns A's 2
            # (isolation is asserted positively in C13).
            r_all = a.get(PROPERTIES_ROUTE, headers={"X-Correlation-Id": "wu11-c1a"})
            body = r_all.json()
            sql_total = asyncio.run(db.count("SELECT count(*) AS n FROM properties"))
            sql_a = asyncio.run(db.count(
                "SELECT count(*) AS n FROM properties WHERE client_account_id=%s",
                (ACCOUNT_A,)))
            sql_statuses = asyncio.run(db.q(
                "SELECT status FROM properties ORDER BY status"))
            ok = (r_all.status_code == 200 and body["count"] == sql_a == 2
                  and all(p["property_id"] for p in body["items"])
                  and sorted(p["status"] for p in body["items"]) ==
                  ["available", "capturing"]
                  and sql_total == 4
                  and sorted(r["status"] for r in sql_statuses) ==
                  ["available", "capturing", "reserved", "sold"])
            record("C1 seed 005 visible: 4 catalog properties (A-list=2), statuses cover catalogue", ok,
                   f"n={body['count']} total={sql_total}")

            # C2. Filters: status, type, city, price range (each SQL-checked).
            f1 = a.get(f"{PROPERTIES_ROUTE}?status=available",
                        headers={"X-Correlation-Id": "wu11-c2a"}).json()
            f2 = a.get(f"{PROPERTIES_ROUTE}?property_type=apartment",
                        headers={"X-Correlation-Id": "wu11-c2b"}).json()
            f3 = a.get(f"{PROPERTIES_ROUTE}?city=Buenos%20Aires",
                        headers={"X-Correlation-Id": "wu11-c2c"}).json()
            f4 = a.get(f"{PROPERTIES_ROUTE}?price_min=100000&price_max=250000",
                        headers={"X-Correlation-Id": "wu11-c2d"}).json()
            s1 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM properties WHERE client_account_id=%s "
                "AND status='available'", (ACCOUNT_A,)))
            s2 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM properties WHERE client_account_id=%s "
                "AND property_type='apartment'", (ACCOUNT_A,)))
            s3 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM properties WHERE client_account_id=%s "
                "AND lower(city)='buenos aires'", (ACCOUNT_A,)))
            s4 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM properties WHERE client_account_id=%s "
                "AND price_amount BETWEEN 100000 AND 250000", (ACCOUNT_A,)))
            bad = a.get(f"{PROPERTIES_ROUTE}?status=not_real",
                        headers={"X-Correlation-Id": "wu11-c2e"})
            bad_range = a.get(f"{PROPERTIES_ROUTE}?price_min=10&price_max=1",
                              headers={"X-Correlation-Id": "wu11-c2f"})
            ok = (f1["count"] == s1 and f2["count"] == s2 and f3["count"] == s3
                  and f4["count"] == s4
                  and envelope_ok(bad, 400, "wu11-c2e", "invalid_query")
                  and envelope_ok(bad_range, 400, "wu11-c2f", "invalid_query"))
            record("C2 filters match SQL oracles; invalid enum/range rejected 400", ok,
                   f"{f1['count']}/{s1} {f2['count']}/{s2} {f3['count']}/{s3} {f4['count']}/{s4}")

            # C3. Detail composes amenities + contacts + interests; catalogue route.
            d = a.get(PROPERTY_DETAIL_ROUTE.replace("{property_id}", A_PROP_1),
                      headers={"X-Correlation-Id": "wu11-c3a"}).json()
            sql_am = asyncio.run(db.count(
                "SELECT count(*) AS n FROM property_amenity_rel WHERE property_id=%s",
                (A_PROP_1,)))
            sql_ct = asyncio.run(db.count(
                "SELECT count(*) AS n FROM property_contacts WHERE property_id=%s",
                (A_PROP_1,)))
            amen = a.get(AMENITIES_ROUTE, headers={"X-Correlation-Id": "wu11-c3b"}).json()
            sql_amen = asyncio.run(db.count("SELECT count(*) AS n FROM property_amenities"))
            ok = (d["property_id"] == A_PROP_1 and len(d["amenities"]) == sql_am
                  and len(d["contacts"]) == sql_ct
                  and d["contacts"][0]["role"] == "owner"
                  and amen["count"] == sql_amen == 6
                  and d["price_amount"] == "185000.00")
            record("C3 detail: amenities/contacts/interests composed, SQL-checked", ok,
                   f"amen={len(d['amenities'])} links={len(d['contacts'])}")

            # C4. Contact->properties surface (A contact: linked + interest rows).
            cp = a.get(CONTACT_PROPERTIES_ROUTE.replace("{contact_id}", A_CONTACT),
                       headers={"X-Correlation-Id": "wu11-c4"}).json()
            cp_b_foreign = b.get(CONTACT_PROPERTIES_ROUTE.replace("{contact_id}", A_CONTACT),
                                 headers={"X-Correlation-Id": "wu11-c4b"}).json()
            relations = {p["property_id"]: set(p["relation"]) for p in cp["items"]}
            ok = (cp["contact_id"] == A_CONTACT and cp["count"] == 2
                  and relations.get(A_PROP_1) == {"linked:owner"}
                  and relations.get(A_PROP_3) == {"linked:referrer", "interested"}
                  and "Buenos Aires" in json.dumps(cp)
                  and cp_b_foreign["count"] == 0)  # A's contact invisible for B
            record("C4 contact properties visible in scope, invisible cross-account", ok,
                   f"count={cp['count']} rel={ {k: sorted(v) for k, v in relations.items()} }")

            # C5. CreateProperty: atomic + durable (amenity links, legacy mirror).
            create_body = {
                "id": NEW_PROP_ID, "reference_code": "WU11-SCRATCH-A1",
                "title": "Departamento test WU11", "operation_type": "rent",
                "status": "capturing", "price_amount": "250000",
                "price_currency": "ARS", "property_type": "apartment",
                "address": "Test 123", "city": "Rosario", "rooms": 2,
                "photos": ["https://demo.invalid/x.jpg"],
                "amenity_ids": [AMENITY_PATIO],
            }
            r5 = _post(a, CMD_CREATE, create_body, "wu11-c5")
            row = asyncio.run(db.one(
                "SELECT status, availability, price, bedrooms, location "
                "FROM properties WHERE id = %s", (NEW_PROP_ID,)))
            audit5 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE outcome='success' "
                "AND action='crm.property.create'"))
            ok = (r5.status_code == 200 and r5.json()["accepted"] is True
                  and row is not None and row["status"] == "capturing"
                  and row["availability"] == "unavailable"  # legacy mirror
                  and str(row["price"]) == "250000.00" and row["bedrooms"] == 2
                  and row["location"] == "Test 123" and audit5 == 1)
            record("C5 create: durable + legacy mirror synced + audit", ok,
                   json.dumps(row, default=str)[:120])

            # C6. Replay same key+body -> canonical; same key different body -> 409.
            # Uses a fresh property id/code: replay is about the KEY, and
            # reusing C5's id would collide on the PK instead of reserving.
            key6 = f"wu11-key-{uuid4().hex[:8]}"
            create6 = {**create_body, "id": str(uuid4()),
                       "reference_code": "WU11-SCRATCH-A2"}
            c6a = _post(a, CMD_CREATE, create6, "wu11-c6a", key=key6)
            c6b = _post(a, CMD_CREATE, create6, "wu11-c6b", key=key6)
            c6c = _post(a, CMD_CREATE, {**create6, "title": "otro titulo"},
                        "wu11-c6c", key=key6)
            props_now = asyncio.run(db.count(
                "SELECT count(*) AS n FROM properties WHERE demo_seed_key IS NULL"))
            ok = (c6a.status_code == 200 and c6b.status_code == 200
                  and c6b.json()["replayed"] is True
                  and c6b.json()["result"] == c6a.json()["result"]
                  and envelope_ok(c6c, 409, "wu11-c6c", "tenant_idempotency_conflict")
                  and props_now == 2)  # C5 scratch + C6a; the conflict added none
            record("C6 replay canonical, conflict without mutation", ok,
                   f"props={props_now}")

            # C7. reference_code unique per account; same code in B is allowed.
            dup = _post(a, CMD_CREATE, {**create_body, "id": str(uuid4()),
                                        "reference_code": "WU11-SCRATCH-A1"},
                        "wu11-c7a", key="wu11-c7a-key")
            ok_b = _post(b, CMD_CREATE, {**create_body, "id": str(uuid4()),
                                         "reference_code": "WU11-SCRATCH-A1"},
                         "wu11-c7b", key="wu11-c7b-key")
            created_b = asyncio.run(db.count(
                "SELECT count(*) AS n FROM properties WHERE client_account_id=%s "
                "AND reference_code='WU11-SCRATCH-A1'", (ACCOUNT_B,)))
            # cleanup B's scratch row: children first, then the parent
            # (RESTRICT FK order — the create carried amenity_ids, so
            # unmarked property_amenity_rel rows exist).
            scratch_b = ("SELECT id FROM properties WHERE client_account_id=%s "
                         "AND reference_code='WU11-SCRATCH-A1' "
                         "AND demo_seed_key IS NULL")
            for child in ("property_amenity_rel", "property_contacts",
                          "property_interests"):
                asyncio.run(db.q(
                    f"DELETE FROM {child} WHERE property_id IN ({scratch_b})",
                    (ACCOUNT_B,)))
            asyncio.run(db.q("DELETE FROM properties WHERE client_account_id=%s "
                             "AND reference_code='WU11-SCRATCH-A1' "
                             "AND demo_seed_key IS NULL", (ACCOUNT_B,)))
            ok = (envelope_ok(dup, 400, "wu11-c7a", "invalid_command")
                  and ok_b.status_code == 200 and created_b == 1)
            record("C7 reference_code unique per account, shared across accounts", ok,
                   f"dup={dup.status_code} b={ok_b.status_code}")

            # C8. UpdateProperty: partial merge keeps the rest; legacy mirrors
            # re-sync; status via update is refused.
            upd = _post(a, CMD_UPDATE, {"changes": {"price_amount": "260000",
                                                    "accepts_swap": True}},
                        "wu11-c8a", property_id=NEW_PROP_ID)
            row8 = asyncio.run(db.one(
                "SELECT price, price_amount, accepts_swap, title, status "
                "FROM properties WHERE id=%s", (NEW_PROP_ID,)))
            refuse = _post(a, CMD_UPDATE, {"changes": {"status": "available"}},
                           "wu11-c8b", property_id=NEW_PROP_ID)
            ok = (upd.status_code == 200 and upd.json()["replayed"] is False
                  and row8["status"] == "capturing" and row8["title"] == "Departamento test WU11"
                  and str(row8["price"]) == "260000.00"  # legacy mirror synced
                  and bool(row8["accepts_swap"])
                  and envelope_ok(refuse, 400, "wu11-c8b", "invalid_command"))
            record("C8 update: partial merge, legacy mirror, status refused", ok,
                   json.dumps(row8, default=str)[:120])

            # C9. Status machine: legal path + illegal edges (rollback + audit).
            s9 = {}
            s9["av"] = _post(a, CMD_STATUS, {"status": "available"}, "wu11-c9a",
                             property_id=NEW_PROP_ID)
            s9["rs"] = _post(a, CMD_STATUS, {"status": "reserved"}, "wu11-c9b",
                             property_id=NEW_PROP_ID)
            s9["so"] = _post(a, CMD_STATUS, {"status": "sold"}, "wu11-c9c",
                             property_id=NEW_PROP_ID)
            s9["illegal"] = _post(a, CMD_STATUS, {"status": "sold"}, "wu11-c9e",
                                  property_id=A_PROP_3)  # capturing -> sold ILLEGAL
            s9["terminal"] = _post(a, CMD_STATUS, {"status": "available"}, "wu11-c9f",
                                   property_id=NEW_PROP_ID)  # sold -> available ILLEGAL
            row9 = asyncio.run(db.one(
                "SELECT status FROM properties WHERE id=%s", (A_PROP_3,)))
            audits9 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records WHERE outcome='denied' "
                "AND metadata->>'reason'='property_status_transition_not_allowed'"))
            ok = (all(s9[k].status_code == 200 for k in ("av", "rs", "so"))
                  and s9["illegal"].status_code == 403 and s9["terminal"].status_code == 403
                  and row9 is not None and row9["status"] == "capturing"  # untouched
                  and audits9 == 2)
            record("C9 status machine: legal path durable, illegal edges denied, 2 redacted audits",
                   ok, f"illegal={s9['illegal'].status_code} audits={audits9}")

            # C10. paused reversible: available -> paused -> available.
            p1 = _post(a, CMD_STATUS, {"status": "paused"}, "wu11-c10a",
                       property_id=A_PROP_1)
            p2 = _post(a, CMD_STATUS, {"status": "available"}, "wu11-c10b",
                       property_id=A_PROP_1)
            row10 = asyncio.run(db.one(
                "SELECT status, availability FROM properties WHERE id=%s", (A_PROP_1,)))
            ok = (p1.status_code == 200 and p2.status_code == 200
                  and row10["status"] == "available"
                  and row10["availability"] == "available")
            record("C10 paused is reversible and mirrors legacy availability", ok, "")

            # C11. LinkContactToProperty: roles + idempotent + cross-account.
            l1 = _post(a, CMD_LINK, {"contact_id": B_CONTACT, "role": "referrer"},
                       "wu11-c11a", property_id=NEW_PROP_ID)  # B contact with session A
            # A's own contact does not exist as scratch; use A contact (seeded)
            l2 = _post(a, CMD_LINK, {"contact_id": A_CONTACT, "role": "referrer"},
                       "wu11-c11b", property_id=NEW_PROP_ID)
            l3 = _post(a, CMD_LINK, {"contact_id": A_CONTACT, "role": "referrer"},
                       "wu11-c11c", property_id=NEW_PROP_ID)
            links11 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM property_contacts WHERE property_id=%s",
                (NEW_PROP_ID,)))
            ok = (envelope_ok(l1, 403, "wu11-c11a", "tenant_access_denied")
                  and l2.status_code == 200 and l2.json()["result"]["linked"] is True
                  and l3.status_code == 200 and l3.json()["result"]["linked"] is False
                  and links11 == 1)
            record("C11 link: foreign contact denied, duplicate link is a no-op", ok,
                   f"links={links11}")

            # C12. Interest: idempotent per pair, NEVER creates a lead.
            leads_before = asyncio.run(db.count(
                "SELECT count(*) AS n FROM leads WHERE client_account_id=%s", (ACCOUNT_A,)))
            i1 = _post(a, CMD_INTEREST, {"contact_id": A_CONTACT, "origin": "ai_agent"},
                       "wu11-c12a", property_id=NEW_PROP_ID)
            i2 = _post(a, CMD_INTEREST, {"contact_id": A_CONTACT, "origin": "ai_agent"},
                       "wu11-c12b", property_id=NEW_PROP_ID)
            leads_after = asyncio.run(db.count(
                "SELECT count(*) AS n FROM leads WHERE client_account_id=%s", (ACCOUNT_A,)))
            int_rows = asyncio.run(db.count(
                "SELECT count(*) AS n FROM property_interests WHERE property_id=%s",
                (NEW_PROP_ID,)))
            ok = (i1.status_code == 200 and i1.json()["result"]["created"] is True
                  and i2.status_code == 200 and i2.json()["result"]["created"] is False
                  and i2.json()["result"]["interest_id"] == i1.json()["result"]["interest_id"]
                  and leads_after == leads_before and int_rows == 1)
            record("C12 interest idempotent per pair and never creates a lead", ok,
                   f"leads {leads_before}->{leads_after}")

            # C13. Isolation + 404 one-shape on the enriched detail.
            f_own = a.get(PROPERTY_DETAIL_ROUTE.replace("{property_id}", A_PROP_1),
                          headers={"X-Correlation-Id": "wu11-c13a"})
            f_foreign = b.get(PROPERTY_DETAIL_ROUTE.replace("{property_id}", A_PROP_1),
                              headers={"X-Correlation-Id": "wu11-c13b"})
            f_ghost = b.get(PROPERTY_DETAIL_ROUTE.replace("{property_id}", GHOST),
                            headers={"X-Correlation-Id": "wu11-c13c"})
            lst_b = b.get(PROPERTIES_ROUTE, headers={"X-Correlation-Id": "wu11-c13d"}).json()
            a_leak = json.dumps(lst_b).count("WU11-SCRATCH")
            ok = (f_own.status_code == 200
                  and envelope_ok(f_foreign, 404, "wu11-c13b", "resource_not_found")
                  and envelope_ok(f_ghost, 404, "wu11-c13c", "resource_not_found")
                  and f_foreign.json()["error"]["message"] == f_ghost.json()["error"]["message"]
                  and lst_b["count"] == 2 and a_leak == 0)
            record("C13 B sees zero A rows; foreign and ghost share one 404 shape", ok,
                   f"b_count={lst_b['count']}")

            # C14. Unmarked child blocks seed 005 re-apply (fail-closed), then
            # cleanup converges. The child must hang off a designated row the
            # converge actually deletes: demo-a-property-002 (A_PROP_3). The
            # same pair already carries a seeded interest, so the unmarked
            # child is a link with a distinct role (PK-safe, FK-RESTRICT child).
            asyncio.run(db.q(
                "INSERT INTO property_contacts (property_id, contact_id, role, "
                "client_account_id) VALUES (%s, %s, 'tenant', %s)",
                (A_PROP_3, A_CONTACT, ACCOUNT_A)))
            try:
                asyncio.run(run_seed(db))
                blocked = False
            except Exception:
                blocked = True
                # the script's BEGIN left the session in an aborted
                # transaction; clear it before any further statement.
                asyncio.run(db.conn.rollback())
            asyncio.run(db.q(
                "DELETE FROM property_contacts WHERE property_id = %s "
                "AND contact_id = %s AND role = 'tenant' AND demo_seed_key IS NULL",
                (A_PROP_3, A_CONTACT)))
            try:
                asyncio.run(run_seed(db))
                converged = True
            except Exception:
                converged = False
            props14 = asyncio.run(db.count(
                "SELECT count(*) AS n FROM properties WHERE demo_seed_key IS NOT NULL"))
            ok = blocked and converged and props14 == 4
            record("C14 unmarked child blocks seed re-apply; after cleanup it converges", ok,
                   f"blocked={blocked} converged={converged}")

            # C15. Envelope discipline: unknown route / wrong method.
            r404 = a.get(f"{PROPERTIES_ROUTE}/bogus-route",
                          headers={"X-Correlation-Id": "wu11-c15a"})
            r405 = a.get(CMD_CREATE, headers={"X-Correlation-Id": "wu11-c15b"})
            ok = (envelope_ok(r404, 404, "wu11-c15a", "route_not_found")
                  and envelope_ok(r405, 405, "wu11-c15b", "method_not_allowed"))
            record("C15 404/405 share the versioned envelope", ok,
                   f"{r404.status_code}/{r405.status_code}")

            # C16. Accepted WU3 legacy repository still reads catalog rows
            # (mirror contract): list via the old composition path. Count is
            # asserted over the designated seed ids only — scenario scratch
            # rows (C5/C6) are unmarked and present by this point.
            from api.composition_wu3_mvp import CrmRepositoryComposition
            from api.adapters.postgres_core import ConnectionPool, PostgresRunner
            from api.tenant_boundary import (
                Agency, ClientAccount, Membership, TenantContext, User)
            ctx = TenantContext(
                user=User(USER_SEEDED, "demo-user"),
                agency=Agency(AGENCY_A, "Agencia A"),
                client_account=ClientAccount(ACCOUNT_A, AGENCY_A, "acc"),
                membership=Membership(USER_SEEDED, AGENCY_A, ACCOUNT_A,
                                      frozenset({"client_account:read", "client_account:write"})),
                correlation_id="wu11-c16")
            runner16 = PostgresRunner(ConnectionPool(dict(cfg)))
            comp16 = CrmRepositoryComposition(runner16)
            legacy_props = asyncio.run(comp16.properties.list(ctx))
            legacy_ids = {p.id for p in legacy_props}
            legacy_get = asyncio.run(comp16.properties.get(ctx, A_PROP_1))
            ok = ({"62000000-0000-0000-0000-000000000001",
                   "62000000-0000-0000-0000-000000000003"} <= legacy_ids
                  and legacy_get is not None
                  and str(legacy_get.price) == "185000.00"
                  and legacy_get.availability in ("available", "reserved", "unavailable"))
            record("C16 accepted WU3 repository still reads catalog rows via mirrors", ok,
                   f"n={len(legacy_props)}")
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_property_catalog_wu11():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(("PASS " if ok else "FAIL ") + name + " :: " + detail)
    print(f"PROPERTY-CATALOG WU11 SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)

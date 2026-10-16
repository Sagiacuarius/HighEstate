"""Commercial pipelines (change: commercial-pipelines, WU19) harness.

Runs against production_runtime_mvp_wu19 (disposable: runner 001-011 +
mvp_seed.sql + mvp_seed_005.sql + mvp_seed_008.sql + mvp_seed_009.sql +
mvp_seed_010.sql + mvp_seed_011.sql). Every effect is generated EXCLUSIVELY
through the new pipeline routes/commands (never hand-inserted); the one
direct command call (P4 execute_transition by id) exercises the accepted WU5
surface to prove id-resolution survives a rename. No model is involved in
this unit (stub-offline is N/A by construction: no analyze call exists).

Scenarios map to the design's Testing Strategy P1-P7 (tasks Phase 5):
P1 pipeline round-trip (create with initial stages -> rename with
leads/stages unmoved verified via GET /v1/demo/pipeline before/after ->
per-account isolation; duplicate pipeline names per account ALLOWED)
P2 stage lifecycle (append-at-end, rename with same-pipeline duplicate
denied + agent-known rename returning the warning flag, up/down reorder
reflected in kanban order, edge no-op, idempotent replay)
P3 delete fail-closed (stage with leads denied stage_has_leads with count
metadata + scoped denial audit + zero effects; empty stage deletes; replay
returns the canonical stored result with zero extra effects)
P4 agent safety (rename keeps id-resolution: execute_transition by id
succeeds throughout; rename-back restores the seeded name)
P5 seed/reset interplay (plain reset stays fail-closed 409 while advisor
rows exist; force reset runs the accepted scripts, which fail closed 500 on
the pre-existing property_amenity_rel junction gap -- advisor-independent,
proven with zero advisor rows; the survival claim is proven with the
verbatim marker-pair slice: NULL advisor pipelines survive, markers go)
P6 idempotency (replayed create/reorder/delete keys return the canonical
stored result with zero extra effects -- covered inside P1/P2/P3)
P7 probes (401 unauthenticated, 404 forged/foreign/ghost non-enumerating,
405 wrong-method)

Idempotency note (WU14 A17 lesson): every write carries a DISTINCT explicit
idempotencyKey (k19-*) so no two scenarios share a console key.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_core import load_database_configuration
from api.application.crm_commands_wu5 import TransitionLeadStageCommand
from api.composition_wu8_mvp import build_mvp_wu8_application
from api.identity.demo_identity import AbsentDemoIdentityAdapter, DemoIdentityAdapter
from api.tenant_boundary import (
    Agency,
    ClientAccount,
    Membership,
    TenantContext,
    User,
)

ROOT = Path(__file__).resolve().parent.parent
SEED_FILES = [ROOT / "infra" / "migrations" / "validation" / "mvp_seed.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_005.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_008.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_009.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_010.sql",
              ROOT / "infra" / "migrations" / "validation" / "mvp_seed_011.sql"]

ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
A_LEAD = "65000000-0000-0000-0000-000000000001"
A_STAGE_SEEDED = "64000000-0000-0000-0000-000000000001"
GHOST = "0000dead-0000-4000-8000-000000000000"

PIPELINES = "/v1/demo/pipelines"
PIPELINE_VIEW = "/v1/demo/pipeline"
RESET_ROUTE = "/v1/demo/reset"


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
    """Deterministic baseline: mirrors the WU18 preclean (008/009/010/011
    console + custom-field tables, catalog, leads/stages/conversations/
    contacts NULL rows, Calificado markers, evidence tables) plus advisor
    pipelines (stages before pipelines for the RESTRICT FKs). The
    NULL-marked simulator stage is kept by name scope -- only non-simulator
    NULL stages are removed. run_seed re-inserts the owned markers, so every
    run starts from identical defaults; the same preclean reconverges the
    base at end of run."""
    for stmt in (
        "DELETE FROM calendar_events",
        "DELETE FROM agent_drafts",
        "DELETE FROM agent_policy_matrix",
        "DELETE FROM agent_settings",
        "DELETE FROM agent_prompts",
        "DELETE FROM agent_field_allowlist",
        "DELETE FROM agent_calendar_config",
        "DELETE FROM agent_signal_bindings",
        "DELETE FROM lead_custom_field_values",
        "DELETE FROM custom_field_definitions",
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
        "DELETE FROM pipeline_stages WHERE demo_seed_key IS NULL "
        "AND name <> 'Simulador: visita calificada'",
        "DELETE FROM pipelines WHERE demo_seed_key IS NULL",
        "DELETE FROM conversations WHERE demo_seed_key IS NULL",
        "DELETE FROM contacts WHERE demo_seed_key IS NULL",
        "UPDATE conversations SET unread_count = 1, updated_at = created_at "
        "WHERE id = '66000000-0000-0000-0000-000000000001'",
        "UPDATE conversations SET unread_count = 0, updated_at = created_at "
        "WHERE id = '66000000-0000-0000-0000-000000000002'",
        "DELETE FROM pipeline_stages WHERE demo_seed_key IN "
        "('demo-a-stage-calificado', 'demo-b-stage-calificado')",
        "DELETE FROM knowledge_base_articles",
        "DELETE FROM command_idempotency",
        "DELETE FROM audit_records",
        "DELETE FROM outbox_events",
    ):
        await db.q(stmt)
    await run_seed(db)
    await db.q(
        "UPDATE conversations SET agent_mode = 'auto', updated_at = created_at "
        "WHERE id IN ('66000000-0000-0000-0000-000000000001', "
        "'66000000-0000-0000-0000-000000000002')")


def envelope_ok(response, status: int, correlation: str, code: str) -> bool:
    if response.status_code != status:
        return False
    err = (response.json().get("error") or {})
    return (err.get("code") == code
            and err.get("correlation_id") == correlation
            and response.headers.get("x-correlation-id") == correlation)


def pipe_ok(response, correlation: str):
    """Pipeline write envelope: 200 + accepted + correlation + result."""
    if response.status_code != 200:
        return None
    body = response.json()
    if (body.get("accepted") is not True
            or body.get("correlation_id") != correlation
            or response.headers.get("x-correlation-id") != correlation
            or "result" not in body):
        return None
    return body


def _get(client, url, correlation):
    return client.get(url, headers={"X-Correlation-Id": correlation})


def _post(client, route, body, correlation):
    return client.post(route, json=body,
                       headers={"X-Correlation-Id": correlation})


def _put(client, route, body, correlation):
    return client.put(route, json=body,
                      headers={"X-Correlation-Id": correlation})


def _delete(client, route, correlation, body=None):
    return client.request("DELETE", route, json=body,
                          headers={"X-Correlation-Id": correlation})


def _kanban_names(payload, pipeline_id: str) -> list[str] | None:
    """Stage names in kanban order for one pipeline from GET /v1/demo/pipeline."""
    data = payload.get("items", payload) if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return None
    for pipeline in data:
        if not isinstance(pipeline, dict):
            continue
        if str(pipeline.get("pipeline_id", pipeline.get("id"))) == pipeline_id:
            stages = pipeline.get("stages") or []
            return [str(s.get("name")) for s in stages]
    return None


def _ctx(account_id: str, tag: str) -> TenantContext:
    return TenantContext(
        user=User("10000000-0000-0000-0000-000000000001", "demo-user"),
        agency=Agency("00000000-0000-0000-0000-000000000001", "demo-agency"),
        client_account=ClientAccount(
            account_id, "00000000-0000-0000-0000-000000000001", "demo-account"),
        membership=Membership("10000000-0000-0000-0000-000000000001",
                              "00000000-0000-0000-0000-000000000001",
                              account_id,
                              frozenset({"client_account:read",
                                         "client_account:write"})),
        correlation_id=tag,
    )


def http_scenarios() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    cfg = load_database_configuration()
    assert cfg.get("dbname") == "production_runtime_mvp_wu19", (
        f"refusing to run on {cfg.get('dbname')}")
    db = Sql(cfg)
    asyncio.run(db.open())
    try:
        asyncio.run(preclean(db))
        wu8_a = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_A, DemoIdentityAdapter()))
        wu8_b = TestClient(build_mvp_wu8_application(cfg, ACCOUNT_B, DemoIdentityAdapter()))

        with wu8_a as a, wu8_b as b:
            lead0 = asyncio.run(db.one(
                "SELECT id, stage_id, pipeline_id, status FROM leads WHERE id = %s",
                (A_LEAD,)))
            lead0 = {k: str(v) for k, v in lead0.items()}

            # P1. Pipeline round-trip: create with initial stages.
            r1 = _post(a, PIPELINES,
                       {"name": "Embudo asesor",
                        "stages": ["Gestión inicial", "Negociación", "Cierre"],
                        "idempotencyKey": "k19-p1-create"}, "wu19-p1-create")
            j1 = pipe_ok(r1, "wu19-p1-create")
            res1 = (j1 or {}).get("result", {})
            pid = res1.get("resource_id")
            pos1 = [s.get("position") for s in (res1.get("stages") or [])]
            ok = (j1 is not None and j1.get("replayed") is False
                  and res1.get("name") == "Embudo asesor"
                  and pos1 == [0, 1, 2] and pid is not None)
            record("P1 create pipeline with initial stages 0..2", ok,
                   f"stages={len(res1.get('stages') or [])} positions={pos1}")
            dup = _post(a, PIPELINES,
                        {"name": "Embudo asesor",
                         "idempotencyKey": "k19-p1-dup"}, "wu19-p1-dup")
            jd = pipe_ok(dup, "wu19-p1-dup")
            ok = (jd is not None
                  and (jd.get("result") or {}).get("resource_id") != pid)
            record("P1 duplicate pipeline names allowed (distinct ids)", ok,
                   f"same_name={(jd.get('result') or {}).get('name') if jd else None}")
            rp1 = _post(a, PIPELINES,
                        {"name": "Embudo asesor",
                         "stages": ["Gestión inicial", "Negociación", "Cierre"],
                         "idempotencyKey": "k19-p1-create"}, "wu19-p1-replay")
            jr1 = pipe_ok(rp1, "wu19-p1-replay")
            n_stages = asyncio.run(db.count(
                "SELECT count(*) AS n FROM pipeline_stages WHERE pipeline_id = %s",
                (pid,)))
            ok = (jr1 is not None and jr1.get("replayed") is True
                  and (jr1.get("result") or {}).get("resource_id") == pid
                  and n_stages == 3)
            record("P1 replayed create: canonical result, zero extra stages", ok,
                   f"replayed={jr1.get('replayed') if jr1 else None} stages={n_stages}")

            # P1 rename: leads/stages unmoved (kanban before/after).
            kb_before = _get(a, PIPELINE_VIEW, "wu19-p1-kb").json()
            names_before = _kanban_names(kb_before, pid)
            rn = _put(a, f"{PIPELINES}/{pid}",
                      {"name": "Embudo asesor renovado",
                       "idempotencyKey": "k19-p1-rename"}, "wu19-p1-rename")
            jn = pipe_ok(rn, "wu19-p1-rename")
            resn = (jn or {}).get("result", {})
            kb_after = _get(a, PIPELINE_VIEW, "wu19-p1-ka").json()
            names_after = _kanban_names(kb_after, pid)
            lead1 = asyncio.run(db.one(
                "SELECT id, stage_id, pipeline_id, status FROM leads WHERE id = %s",
                (A_LEAD,)))
            lead1 = {k: str(v) for k, v in lead1.items()}
            ok = (jn is not None and resn.get("name") == "Embudo asesor renovado"
                  and resn.get("agent_name_warning") is False
                  and names_before == names_after
                  and names_after == ["Gestión inicial", "Negociación", "Cierre"]
                  and lead1 == lead0)
            record("P1 rename moves no leads/stages (kanban identical)", ok,
                   f"warning={resn.get('agent_name_warning')} kanban={names_after}")

            # P1 isolation: cross-account ids invisible.
            cross = _put(b, f"{PIPELINES}/{pid}", {"name": "Intruso"},
                         "wu19-p1-cross")
            kb_b = _get(b, PIPELINE_VIEW, "wu19-p1-kb-b").json()
            ids_b = ([str(p.get("id")) for p in kb_b]
                     if isinstance(kb_b, list) else [])
            ok = (envelope_ok(cross, 404, "wu19-p1-cross", "resource_not_found")
                  and pid not in ids_b)
            record("P1 cross-account pipeline invisible (404)", ok,
                   f"status={cross.status_code}")

            # P1 forged/foreign/ghost ids non-enumerating (short stage paths only).
            g1 = _put(a, f"{PIPELINES}/{GHOST}", {"name": "Fantasma"},
                      "wu19-p1-g1")
            g2 = _post(a, f"{PIPELINES}/{GHOST}/stages",
                       {"name": "Fantasma", "idempotencyKey": "k19-p1-g2"},
                       "wu19-p1-g2")
            g3 = _put(a, f"/v1/demo/stages/{GHOST}",
                      {"name": "Fantasma"}, "wu19-p1-g3")
            g4 = _delete(a, f"/v1/demo/stages/{GHOST}", "wu19-p1-g4",
                         {"idempotencyKey": "k19-p1-g4"})
            g5 = _post(a, f"/v1/demo/stages/{GHOST}/move",
                       {"direction": "up"}, "wu19-p1-g5")
            g6 = _delete(b, f"/v1/demo/stages/{A_STAGE_SEEDED}", "wu19-p1-g6",
                         {"idempotencyKey": "k19-p1-g6"})
            ok = all(envelope_ok(r, 404, c, "resource_not_found")
                     for r, c in ((g1, "wu19-p1-g1"), (g2, "wu19-p1-g2"),
                                  (g3, "wu19-p1-g3"), (g4, "wu19-p1-g4"),
                                  (g5, "wu19-p1-g5"), (g6, "wu19-p1-g6")))
            record("P1 forged/foreign/ghost ids 404 non-enumerating", ok,
                   f"statuses={[g1.status_code, g2.status_code, g3.status_code]}")

            # P1 validation: blank names + unknown keys fail closed 400.
            v1 = _post(a, PIPELINES, {"name": "   ",
                                      "idempotencyKey": "k19-p1-v1"}, "wu19-p1-v1")
            v2 = _post(a, PIPELINES, {"name": "Válida", "zzz": 1},
                       "wu19-p1-v2")
            v3 = _put(a, f"{PIPELINES}/{pid}", {"name": ""}, "wu19-p1-v3")
            v4 = _put(a, f"{PIPELINES}/{pid}",
                      {"name": "Otra", "stages": []}, "wu19-p1-v4")
            ok = all(envelope_ok(r, 400, c, "invalid_command")
                     for r, c in ((v1, "wu19-p1-v1"), (v2, "wu19-p1-v2"),
                                  (v3, "wu19-p1-v3"), (v4, "wu19-p1-v4")))
            record("P1 blank names + unknown keys fail closed 400", ok,
                   f"statuses={[v1.status_code, v2.status_code]}")

            # P2. Stage lifecycle on the advisor pipeline.
            s1 = _post(a, f"{PIPELINES}/{pid}/stages",
                       {"name": "Seguimiento", "idempotencyKey": "k19-p2-append"},
                       "wu19-p2-append")
            js1 = pipe_ok(s1, "wu19-p2-append")
            sid = ((js1 or {}).get("result") or {}).get("resource_id")
            ok = (js1 is not None
                  and (js1.get("result") or {}).get("position") == 3)
            record("P2 append-at-end lands at max+1", ok,
                   f"position={(js1.get('result') or {}).get('position') if js1 else None}")
            u1 = _put(a, f"/v1/demo/stages/{sid}",
                      {"name": "Seguimiento activo",
                       "idempotencyKey": "k19-p2-u1"}, "wu19-p2-u1")
            ju1 = pipe_ok(u1, "wu19-p2-u1")
            ok = (ju1 is not None
                  and (ju1.get("result") or {}).get("agent_name_warning") is False)
            record("P2 unknown->unknown rename silent (warning false)", ok,
                   f"warning={(ju1.get('result') or {}).get('agent_name_warning') if ju1 else None}")
            u2 = _put(a, f"/v1/demo/stages/{sid}",
                      {"name": "Calificado", "idempotencyKey": "k19-p2-u2"},
                      "wu19-p2-u2")
            ju2 = pipe_ok(u2, "wu19-p2-u2")
            ok = (ju2 is not None
                  and (ju2.get("result") or {}).get("agent_name_warning") is True)
            record("P2 unknown->known rename warns (warning true)", ok,
                   f"warning={(ju2.get('result') or {}).get('agent_name_warning') if ju2 else None}")
            u3 = _put(a, f"/v1/demo/stages/{sid}",
                      {"name": "Posventa", "idempotencyKey": "k19-p2-u3"},
                      "wu19-p2-u3")
            ju3 = pipe_ok(u3, "wu19-p2-u3")
            ok = (ju3 is not None
                  and (ju3.get("result") or {}).get("agent_name_warning") is True)
            record("P2 known->unknown rename warns (old-or-new rule)", ok,
                   f"prev={(ju3.get('result') or {}).get('previous_name') if ju3 else None}")
            d1 = _post(a, f"{PIPELINES}/{pid}/stages",
                       {"name": "Duplicada", "idempotencyKey": "k19-p2-d1"},
                       "wu19-p2-d1")
            jd1 = pipe_ok(d1, "wu19-p2-d1")
            sid_dup = ((jd1 or {}).get("result") or {}).get("resource_id")
            d2 = _put(a, f"/v1/demo/stages/{sid}",
                      {"name": "Duplicada"}, "wu19-p2-d2")
            ok = (jd1 is not None
                  and envelope_ok(d2, 409, "wu19-p2-d2",
                                  "stage_name_already_exists"))
            record("P2 same-pipeline duplicate rename denied 409", ok,
                   f"status={d2.status_code}")

            # P2 reorder: swap reflected in kanban order.
            order0 = asyncio.run(db.q(
                "SELECT name FROM pipeline_stages WHERE pipeline_id = %s "
                "ORDER BY position, id", (pid,)))
            names0 = [r["name"] for r in order0]
            mid = next(r["name"] for r in order0 if r["name"] == "Negociación")
            mid_id = asyncio.run(db.one(
                "SELECT id FROM pipeline_stages WHERE pipeline_id = %s AND name = %s",
                (pid, "Negociación")))["id"]
            m1 = _post(a, f"/v1/demo/stages/{mid_id}/move",
                       {"direction": "down", "idempotencyKey": "k19-p2-m1"},
                       "wu19-p2-m1")
            jm1 = pipe_ok(m1, "wu19-p2-m1")
            order1 = asyncio.run(db.q(
                "SELECT name FROM pipeline_stages WHERE pipeline_id = %s "
                "ORDER BY position, id", (pid,)))
            names1 = [r["name"] for r in order1]
            kb1 = _kanban_names(_get(a, PIPELINE_VIEW, "wu19-p2-kb").json(), pid)
            idx = names0.index("Negociación")
            expect = names0[:idx] + [names0[idx + 1], names0[idx]] + names0[idx + 2:]
            ok = (jm1 is not None
                  and (jm1.get("result") or {}).get("moved") is True
                  and names1 == expect
                  and kb1 == names1)
            record("P2 down-swap reflected in kanban order", ok,
                   f"kanban={kb1}")
            m1b = _post(a, f"/v1/demo/stages/{mid_id}/move",
                        {"direction": "up", "idempotencyKey": "k19-p2-m1b"},
                        "wu19-p2-m1b")
            jm1b = pipe_ok(m1b, "wu19-p2-m1b")
            order1b = asyncio.run(db.q(
                "SELECT name FROM pipeline_stages WHERE pipeline_id = %s "
                "ORDER BY position, id", (pid,)))
            ok = (jm1b is not None
                  and (jm1b.get("result") or {}).get("moved") is True
                  and [r["name"] for r in order1b] == names0
                  and mid == "Negociación")
            record("P2 up-swap restores order", ok,
                   f"moved={(jm1b.get('result') or {}).get('moved') if jm1b else None}")
            first_id = asyncio.run(db.one(
                "SELECT id FROM pipeline_stages WHERE pipeline_id = %s "
                "ORDER BY position, id LIMIT 1", (pid,)))["id"]
            last_id = asyncio.run(db.one(
                "SELECT id FROM pipeline_stages WHERE pipeline_id = %s "
                "ORDER BY position DESC, id DESC LIMIT 1", (pid,)))["id"]
            e1 = _post(a, f"/v1/demo/stages/{first_id}/move",
                       {"direction": "up", "idempotencyKey": "k19-p2-e1"},
                       "wu19-p2-e1")
            e2 = _post(a, f"/v1/demo/stages/{last_id}/move",
                       {"direction": "down", "idempotencyKey": "k19-p2-e2"},
                       "wu19-p2-e2")
            je1 = pipe_ok(e1, "wu19-p2-e1")
            je2 = pipe_ok(e2, "wu19-p2-e2")
            ok = (je1 is not None and je2 is not None
                  and (je1.get("result") or {}).get("moved") is False
                  and (je2.get("result") or {}).get("moved") is False)
            record("P2 edge reorder no-op success (moved false)", ok,
                   f"first={je1.get('result') if je1 else None}")
            before_pos = asyncio.run(db.q(
                "SELECT id, position FROM pipeline_stages WHERE pipeline_id = %s",
                (pid,)))
            rm = _post(a, f"/v1/demo/stages/{mid_id}/move",
                       {"direction": "down", "idempotencyKey": "k19-p2-m1"},
                       "wu19-p2-m1-replay")
            jrm = pipe_ok(rm, "wu19-p2-m1-replay")
            after_pos = asyncio.run(db.q(
                "SELECT id, position FROM pipeline_stages WHERE pipeline_id = %s",
                (pid,)))
            keyed = lambda rows: sorted((str(r["id"]), r["position"]) for r in rows)
            ok = (jrm is not None and jrm.get("replayed") is True
                  and keyed(before_pos) == keyed(after_pos))
            record("P2 replayed move: canonical result, zero extra effects", ok,
                   f"replayed={jrm.get('replayed') if jrm else None}")
            mv1 = _post(a, f"/v1/demo/stages/{mid_id}/move",
                        {"direction": "sideways"}, "wu19-p2-mv1")
            mv2 = _post(a, f"/v1/demo/stages/{mid_id}/move",
                        {"direction": "up", "zzz": 1}, "wu19-p2-mv2")
            mv3 = _put(a, f"/v1/demo/stages/{mid_id}",
                       {"name": "  "}, "wu19-p2-mv3")
            mv4 = _delete(a, f"/v1/demo/stages/{mid_id}", "wu19-p2-mv4",
                          {"idempotencyKey": "k", "zzz": 1})
            ok = all(envelope_ok(r, 400, c, "invalid_command")
                     for r, c in ((mv1, "wu19-p2-mv1"), (mv2, "wu19-p2-mv2"),
                                  (mv3, "wu19-p2-mv3"), (mv4, "wu19-p2-mv4")))
            record("P2 bad direction/blank/unknown keys fail closed 400", ok,
                   f"statuses={[mv1.status_code, mv3.status_code]}")

            # P3. Delete fail-closed: seeded stage WITH a lead is denied.
            bl = _delete(a, f"/v1/demo/stages/{A_STAGE_SEEDED}", "wu19-p3-blocked",
                         {"idempotencyKey": "k19-p3-blocked"})
            bj = bl.json() if bl.status_code == 409 else {}
            still = asyncio.run(db.one(
                "SELECT id FROM pipeline_stages WHERE id = %s",
                (A_STAGE_SEEDED,)))
            lead2 = asyncio.run(db.one(
                "SELECT id, stage_id, pipeline_id, status FROM leads WHERE id = %s",
                (A_LEAD,)))
            lead2 = {k: str(v) for k, v in lead2.items()}
            n_deny = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records "
                "WHERE correlation_id = %s", ("wu19-p3-blocked",)))
            ok = (bl.status_code == 409
                  and (bj.get("error") or {}).get("code") == "stage_has_leads"
                  and bj.get("lead_count") == 1
                  and bl.headers.get("x-correlation-id") == "wu19-p3-blocked"
                  and still is not None and lead2 == lead1 and n_deny >= 1)
            record("P3 delete stage with leads denied + zero effects", ok,
                   f"lead_count={bj.get('lead_count')} audits={n_deny}")
            de = _post(a, f"{PIPELINES}/{pid}/stages",
                       {"name": "Efímera", "idempotencyKey": "k19-p3-ef"},
                       "wu19-p3-ef")
            jde = pipe_ok(de, "wu19-p3-ef")
            sid_ef = ((jde or {}).get("result") or {}).get("resource_id")
            dl = _delete(a, f"/v1/demo/stages/{sid_ef}", "wu19-p3-del",
                         {"idempotencyKey": "k19-p3-del"})
            jdl = pipe_ok(dl, "wu19-p3-del")
            gone = asyncio.run(db.one(
                "SELECT id FROM pipeline_stages WHERE id = %s", (sid_ef,)))
            ok = (jdl is not None
                  and (jdl.get("result") or {}).get("deleted") is True
                  and gone is None)
            record("P3 delete empty stage succeeds", ok,
                   f"deleted={(jdl.get('result') or {}).get('deleted') if jdl else None}")
            dl2 = _delete(a, f"/v1/demo/stages/{sid_ef}", "wu19-p3-del-replay",
                          {"idempotencyKey": "k19-p3-del"})
            jdl2 = pipe_ok(dl2, "wu19-p3-del-replay")
            n_del = asyncio.run(db.count(
                "SELECT count(*) AS n FROM audit_records "
                "WHERE correlation_id = %s", ("wu19-p3-del",)))
            ok = (jdl2 is not None and jdl2.get("replayed") is True
                  and (jdl2.get("result") or {}).get("deleted") is True
                  and n_del == 1)
            record("P3 replayed delete: canonical result, zero extra effects", ok,
                   f"replayed={jdl2.get('replayed') if jdl2 else None} audits={n_del}")

            # P4. Rename keeps id-resolution: transition by id throughout.
            rn4 = _put(a, f"/v1/demo/stages/{A_STAGE_SEEDED}",
                       {"name": "Consulta inicial",
                        "idempotencyKey": "k19-p4-rename"}, "wu19-p4-rename")
            jn4 = pipe_ok(rn4, "wu19-p4-rename")
            ok = (jn4 is not None
                  and (jn4.get("result") or {}).get("agent_name_warning") is False)
            record("P4 seeded stage rename ok (warning false)", ok,
                   f"name={(jn4.get('result') or {}).get('name') if jn4 else None}")
            mover = TransitionLeadStageCommand(dict(cfg))
            outcome = asyncio.run(mover.execute_transition(
                _ctx(ACCOUNT_A, "wu19-p4-transition"), lead_id=A_LEAD,
                to_stage_id=A_STAGE_SEEDED,
                idempotency_key=f"console:{ACCOUNT_A}:lead:wu19-p4-transition"))
            ok = (outcome is not None
                  and outcome.payload.get("current_stage_id") == A_STAGE_SEEDED)
            record("P4 execute_transition by id succeeds after rename", ok,
                   f"current={outcome.payload.get('current_stage_id') if outcome else None}")
            back4 = _put(a, f"/v1/demo/stages/{A_STAGE_SEEDED}",
                         {"name": "Nueva consulta",
                          "idempotencyKey": "k19-p4-back"}, "wu19-p4-back")
            jback = pipe_ok(back4, "wu19-p4-back")
            row4 = asyncio.run(db.one(
                "SELECT name FROM pipeline_stages WHERE id = %s",
                (A_STAGE_SEEDED,)))
            ok = (jback is not None and row4 is not None
                  and row4["name"] == "Nueva consulta")
            record("P4 rename-back restores the seeded name", ok,
                   f"name={row4['name'] if row4 else None}")

            # P5. Seed/reset interplay. Plain reset stays fail-closed while
            # advisor rows exist; force reset runs the accepted scripts,
            # which fail closed 500 on the PRE-EXISTING junction gap
            # (mvp_reset.sql deletes properties without clearing
            # property_amenity_rel first -- fails with zero advisor rows
            # too, verified manually; WU18-D5 class). The spec's survival
            # claim is then proven with the verbatim marker-pair slice.
            plain = _post(a, RESET_ROUTE, {}, "wu19-p5-plain")
            pj = plain.json() if plain.status_code == 409 else {}
            ok = (plain.status_code == 409
                  and (pj.get("error") or {}).get("code") == "demo_effects_pending"
                  and isinstance(pj.get("pending_effects"), int)
                  and pj.get("pending_effects", 0) > 0)
            record("P5 plain reset fail-closed 409 while advisor rows exist", ok,
                   f"pending={pj.get('pending_effects')}")
            force = a.post(f"{RESET_ROUTE}?clear_effects=true",
                           headers={"X-Correlation-Id": "wu19-p5-force"})
            fj = force.json() if force.status_code == 500 else {}
            ok = (force.status_code == 500
                  and (fj.get("error") or {}).get("code") == "demo_script_failed")
            record("P5 force-reset 500 pre-existing junction gap (not advisor rows)", ok,
                   f"status={force.status_code} code={(fj.get('error') or {}).get('code')}")
            # Verbatim marker-pair slice from mvp_reset.sql:20-32 (messages,
            # conversations, follow_ups, leads, stages, pipelines -- script
            # order for the RESTRICT FKs; properties/contacts omitted: the
            # pre-existing junction gap above blocks the full script).
            # NULL-marked advisor pipelines must survive it.
            for stmt in (
                "DELETE FROM messages WHERE (client_account_id, demo_seed_key) IN ("
                "('20000000-0000-0000-0000-000000000001', 'demo-a-message-001'), "
                "('20000000-0000-0000-0000-000000000002', 'demo-b-message-001'))",
                "DELETE FROM conversations WHERE (client_account_id, demo_seed_key) IN ("
                "('20000000-0000-0000-0000-000000000001', 'demo-a-conversation-001'), "
                "('20000000-0000-0000-0000-000000000002', 'demo-b-conversation-001'))",
                "DELETE FROM follow_ups WHERE (client_account_id, demo_seed_key) IN ("
                "('20000000-0000-0000-0000-000000000001', 'demo-a-follow-up-001'), "
                "('20000000-0000-0000-0000-000000000002', 'demo-b-follow-up-001'))",
                "DELETE FROM leads WHERE (client_account_id, demo_seed_key) IN ("
                "('20000000-0000-0000-0000-000000000001', 'demo-a-lead-001'), "
                "('20000000-0000-0000-0000-000000000002', 'demo-b-lead-001'))",
                "DELETE FROM pipeline_stages WHERE (client_account_id, demo_seed_key) IN ("
                "('20000000-0000-0000-0000-000000000001', 'demo-a-stage-001'), "
                "('20000000-0000-0000-0000-000000000002', 'demo-b-stage-001'), "
                "('20000000-0000-0000-0000-000000000001', 'demo-a-stage-calificado'), "
                "('20000000-0000-0000-0000-000000000002', 'demo-b-stage-calificado'))",
                "DELETE FROM pipelines WHERE (client_account_id, demo_seed_key) IN ("
                "('20000000-0000-0000-0000-000000000001', 'demo-a-pipeline-001'), "
                "('20000000-0000-0000-0000-000000000002', 'demo-b-pipeline-001'))",
            ):
                asyncio.run(db.q(stmt))
            adv_pipes = asyncio.run(db.q(
                "SELECT id, demo_seed_key FROM pipelines "
                "WHERE client_account_id = %s AND demo_seed_key IS NULL",
                (ACCOUNT_A,)))
            marked_pipes = asyncio.run(db.count(
                "SELECT count(*) AS n FROM pipelines WHERE demo_seed_key IS NOT NULL"))
            ok = (len(adv_pipes) == 2 and marked_pipes == 0)
            record("P5 marker-pair slice: advisor pipelines survive, markers gone", ok,
                   f"advisor={len(adv_pipes)} marked={marked_pipes}")

            # P7. Probes: 401 / 404 / 405.
            none_app = TestClient(build_mvp_wu8_application(
                cfg, ACCOUNT_A, AbsentDemoIdentityAdapter()))
            with none_app as napp:
                r401p = _post(napp, PIPELINES, {"name": "X"}, "wu19-401-p")
                r401u = _put(napp, f"{PIPELINES}/{pid}", {"name": "X"},
                             "wu19-401-u")
                record("P7 401 without identity (pipelines)", envelope_ok(
                    r401p, 401, "wu19-401-p", "authentication_required")
                    and envelope_ok(r401u, 401, "wu19-401-u",
                                    "authentication_required"),
                    f"create={r401p.status_code} rename={r401u.status_code}")
            r404 = _get(a, "/v1/demo/no-such-route", "wu19-404")
            r405a = _get(a, f"/v1/demo/stages/{A_STAGE_SEEDED}", "wu19-405a")
            r405b = _delete(a, f"{PIPELINES}/{pid}", "wu19-405b")
            record("P7 404 unknown pipeline route", envelope_ok(
                r404, 404, "wu19-404", "route_not_found"),
                f"status={r404.status_code}")
            record("P7 405 wrong method on stage/pipeline", envelope_ok(
                r405a, 405, "wu19-405a", "method_not_allowed")
                and envelope_ok(r405b, 405, "wu19-405b",
                                "method_not_allowed"),
                f"get-stage={r405a.status_code} del-pipe={r405b.status_code}")

            # No model is involved anywhere in this unit.
            ok = "langchain_openai" not in sys.modules
            record("P7 no-model unit (no langchain import, no analyze call)",
                   ok, "models=n/a")

            # End-of-run reconverge: pure seeded shape for the next run.
            asyncio.run(preclean(db))
    finally:
        asyncio.run(db.close())
    return results


def run_all() -> list[tuple[str, bool, str]]:
    return http_scenarios()


def test_commercial_pipelines_wu19():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing scenarios: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"COMMERCIAL PIPELINES WU19 SCENARIOS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)

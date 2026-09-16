"""Console settings/allowlist read-path units (change: agent-console, WU16).

Phase 2: the DB overlay (load_console_settings / load_field_allowlist)
applies with rows present, and the fallback == code-constants oracle holds
with rows deleted (delete+restore on account B only, try/finally — the wu16
base is shared and must be left clean). Pure evaluate() overrides are
pinned with no DB.
"""

from __future__ import annotations

import asyncio

from api.adapters.postgres_agent_console import (
    LEGACY_FIELD_ALLOWLIST,
    load_console_settings,
    load_field_allowlist,
)
from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.application.agent_policy import (
    AUTO_ACTION_TYPES,
    AUTO_EVENT_CREATION,
    PolicySignals,
    evaluate,
)
from api.tenant_boundary import (
    Agency,
    ClientAccount,
    Membership,
    TenantContext,
    User,
)

AGENCY = "00000000-0000-0000-0000-000000000001"
USER = "10000000-0000-0000-0000-000000000001"
ACCOUNT_A = "20000000-0000-0000-0000-000000000001"
ACCOUNT_B = "20000000-0000-0000-0000-000000000002"
PERMS = frozenset({"client_account:read", "client_account:write"})


def _ctx(account_id: str, tag: str) -> TenantContext:
    return TenantContext(
        user=User(USER, "demo-user"),
        agency=Agency(AGENCY, "demo-agency"),
        client_account=ClientAccount(account_id, AGENCY, "demo-account"),
        membership=Membership(USER, AGENCY, account_id, PERMS),
        correlation_id=tag,
    )


# Scenario corpus for the fallback oracle (defaults vs loaded overlay).
CORPUS = [
    ("faq hit", PolicySignals(intent="ask_question", kb_match=True), ["send_reply"]),
    ("search match", PolicySignals(intent="search_properties", matched_count=2), ["send_reply"]),
    ("visit slot", PolicySignals(intent="schedule_visit", slot_found=True), ["create_event"]),
    ("handoff", PolicySignals(intent="handoff"), ["handoff_to_human"]),
]


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    async def _run() -> None:
        cfg = load_database_configuration()
        runner = PostgresRunner(ConnectionPool(dict(cfg)))

        # S1 rows present (read-only): seeded overlay equals the constants.
        loaded = await load_console_settings(_ctx(ACCOUNT_A, "s1"), runner)
        record("S1 seeded auto_types == AUTO_ACTION_TYPES",
               loaded.auto_types == AUTO_ACTION_TYPES, f"auto={sorted(loaded.auto_types)}")
        record("S1 seeded calendar_allowed == AUTO_EVENT_CREATION",
               loaded.calendar_allowed == bool(AUTO_EVENT_CREATION),
               f"calendar_allowed={loaded.calendar_allowed}")
        record("S1 seeded global_auto is True", loaded.global_auto is True, "")
        record("S1 seeded handoff verdict escalate",
               loaded.matrix.get("handoff_to_human") == "escalate"
               and len(loaded.matrix) == 7, f"matrix={loaded.matrix}")

        # S2 overlay verdicts apply: loaded overrides reproduce defaults.
        corpus_ok = True
        for label, signals, actions in CORPUS:
            want = evaluate(signals, actions)
            got = evaluate(signals, actions, auto_types=loaded.auto_types,
                           calendar_allowed=loaded.calendar_allowed)
            corpus_ok = corpus_ok and got == want
            record(f"S2 overlay {label} reproduces default verdict",
                   got == want, f"verdict={got.verdict}/{got.reason}")
        record("S2 overlay corpus all reproduce defaults", corpus_ok, "")

        # S3 pure overrides take effect (no DB).
        narrowed = evaluate(PolicySignals(intent="ask_question", kb_match=True),
                            ["send_reply"],
                            auto_types=frozenset(AUTO_ACTION_TYPES) - {"send_reply"},
                            calendar_allowed=True)
        record("S3 narrowed auto_types drops send_reply from auto side",
               narrowed.verdict == "auto" and "send_reply" not in narrowed.auto_execute_types,
               f"auto={sorted(narrowed.auto_execute_types)}")
        regated = evaluate(PolicySignals(intent="schedule_visit", slot_found=True),
                           ["create_event"], calendar_allowed=False)
        record("S3 calendar_allowed=False re-gates to calendar_gated_kill_switch",
               regated.verdict == "escalate" and regated.reason == "calendar_gated_kill_switch",
               f"verdict={regated.verdict}/{regated.reason}")
        glob = await load_console_settings(_ctx(ACCOUNT_A, "s3"), runner)
        record("S3 loader deterministic (two loads equal)",
               glob == loaded, "")

        # S4 allowlist rows present (read-only): seeded stage_id + status.
        allow = await load_field_allowlist(_ctx(ACCOUNT_A, "s4"), runner)
        record("S4 seeded allowlist == {stage_id, status}",
               allow == frozenset({"stage_id", "status"}), f"allow={sorted(allow)}")

        # S5 fallback oracle: delete B rows, verdicts must match constants.
        settings_rows = await runner.query(
            "SELECT * FROM agent_settings WHERE client_account_id = %s", (ACCOUNT_B,))
        matrix_rows = await runner.query(
            "SELECT * FROM agent_policy_matrix WHERE client_account_id = %s", (ACCOUNT_B,))
        allow_rows = await runner.query(
            "SELECT * FROM agent_field_allowlist WHERE client_account_id = %s", (ACCOUNT_B,))
        assert settings_rows and len(matrix_rows) == 7 and allow_rows, "seed rows missing for B"
        try:
            await runner.execute(
                "DELETE FROM agent_policy_matrix WHERE client_account_id = %s", (ACCOUNT_B,))
            await runner.execute(
                "DELETE FROM agent_settings WHERE client_account_id = %s", (ACCOUNT_B,))
            await runner.execute(
                "DELETE FROM agent_field_allowlist WHERE client_account_id = %s", (ACCOUNT_B,))
            fallback = await load_console_settings(_ctx(ACCOUNT_B, "s5"), runner)
            record("S5 fallback auto_types == AUTO_ACTION_TYPES",
                   fallback.auto_types == AUTO_ACTION_TYPES,
                   f"auto={sorted(fallback.auto_types)}")
            record("S5 fallback calendar_allowed == AUTO_EVENT_CREATION",
                   fallback.calendar_allowed == bool(AUTO_EVENT_CREATION), "")
            record("S5 fallback global_auto True", fallback.global_auto is True, "")
            oracle_ok = True
            for label, signals, actions in CORPUS:
                want = evaluate(signals, actions)
                got = evaluate(signals, actions, auto_types=fallback.auto_types,
                               calendar_allowed=fallback.calendar_allowed)
                oracle_ok = oracle_ok and got == want
            record("S5 fallback verdict table == constants oracle", oracle_ok, "")
            fall_allow = await load_field_allowlist(_ctx(ACCOUNT_B, "s5"), runner)
            record("S5 fallback allowlist == legacy {stage_id, status}",
                   fall_allow == frozenset(LEGACY_FIELD_ALLOWLIST)
                   == frozenset({"stage_id", "status"}),
                   f"allow={sorted(fall_allow)}")
        finally:
            for row in settings_rows:
                await runner.execute(
                    "INSERT INTO agent_settings (id, client_account_id, demo_seed_key, "
                    "global_auto, calendar_auto_enabled) VALUES (%s, %s, %s, %s, %s)",
                    (row["id"], row["client_account_id"], row["demo_seed_key"],
                     row["global_auto"], row["calendar_auto_enabled"]))
            for row in matrix_rows:
                await runner.execute(
                    "INSERT INTO agent_policy_matrix (id, client_account_id, action_type, "
                    "demo_seed_key, verdict) VALUES (%s, %s, %s, %s, %s)",
                    (row["id"], row["client_account_id"], row["action_type"],
                     row["demo_seed_key"], row["verdict"]))
            for row in allow_rows:
                await runner.execute(
                    "INSERT INTO agent_field_allowlist (id, client_account_id, column_name, "
                    "demo_seed_key) VALUES (%s, %s, %s, %s)",
                    (row["id"], row["client_account_id"], row["column_name"],
                     row["demo_seed_key"]))

        # S6 restored clean: seed counts back.
        counts = {}
        for table, want in (("agent_settings", 2), ("agent_policy_matrix", 14),
                            ("agent_field_allowlist", 4)):
            rows = await runner.query(f"SELECT count(*) AS n FROM {table}", ())
            counts[table] = int(rows[0]["n"])
            record(f"S6 {table} restored to {want}", counts[table] == want,
                   f"count={counts[table]}")

    asyncio.run(_run())
    return results


def test_agent_console_settings():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT-CONSOLE-SETTINGS UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)

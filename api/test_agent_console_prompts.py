"""Console prompt compose/preview units (change: agent-console, WU16).

Phase 3: the composer keeps the skeleton code-only (no skeleton parameter
anywhere), the seeded v1 preview is byte-identical to build_system_prompt(),
a custom tone differs only in the tone layer, and empty tone fails closed
to the default. The prompts-table read is read-only (no writes, no cleanup).
"""

from __future__ import annotations

import asyncio
import inspect

from api.adapters.postgres_agent_console import preview_prompt
from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.application.agent_persona import (
    AGENT_GUARDRAILS,
    AGENT_IDENTITY,
    AGENT_LANGUAGE,
    AGENT_ROLE,
    AGENT_TONE,
    build_system_prompt,
    compose_prompt,
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
PERMS = frozenset({"client_account:read", "client_account:write"})


def _ctx(tag: str) -> TenantContext:
    return TenantContext(
        user=User(USER, "demo-user"),
        agency=Agency(AGENCY, "demo-agency"),
        client_account=ClientAccount(ACCOUNT_A, AGENCY, "demo-account"),
        membership=Membership(USER, AGENCY, ACCOUNT_A, PERMS),
        correlation_id=tag,
    )


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    default = build_system_prompt()
    record("R1 default composer non-empty", bool(default), f"len={len(default)}")
    record("R2 compose_prompt() == build_system_prompt()",
           compose_prompt() == default, "")
    record("R2 explicit defaults byte-identical",
           build_system_prompt(tone=AGENT_TONE, instructions="") == default, "")

    # Skeleton layers present byte-identical in the default text.
    for label, needle in (("identity", AGENT_IDENTITY), ("role", AGENT_ROLE),
                          ("tone", AGENT_TONE), ("language", AGENT_LANGUAGE),
                          ("guardrails", AGENT_GUARDRAILS)):
        record(f"R3 skeleton layer {label} present byte-identical",
               needle in default, "")

    # Custom tone differs only in the tone layer.
    custom = compose_prompt("serio y breve", "")
    record("R4 custom tone differs from default", custom != default, "")
    record("R4 custom tone text present, default tone absent",
           "serio y breve" in custom and AGENT_TONE not in custom, "")
    record("R4 guardrails byte-identical under custom tone",
           AGENT_GUARDRAILS in custom, "")
    record("R4 identity/role byte-identical under custom tone",
           AGENT_IDENTITY in custom and AGENT_ROLE in custom, "")

    # Instructions layer is additive and keeps the skeleton.
    with_extra = compose_prompt(AGENT_TONE, "Prioriza visitas.")
    record("R5 instructions appended", "Prioriza visitas." in with_extra, "")
    record("R5 skeleton intact with instructions",
           AGENT_GUARDRAILS in with_extra, "")
    record("R5 default carries no instructions layer",
           "Instrucciones adicionales" not in default, "")

    # Empty tone fails closed to the default (documented).
    record("R6 empty tone -> default",
           compose_prompt("", "") == default
           and build_system_prompt(tone="") == default, "")
    record("R6 whitespace tone -> default", compose_prompt("   ", "") == default, "")

    # No skeleton parameter anywhere on the composer surface.
    for fn in (build_system_prompt, compose_prompt):
        params = inspect.signature(fn).parameters
        names = set(params)
        kinds_ok = all(p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
                       or p.kind == inspect.Parameter.KEYWORD_ONLY for p in params.values())
        record(f"R7 {fn.__name__} exposes only tone/instructions",
               names == {"tone", "instructions"} and kinds_ok, f"params={sorted(names)}")
        record(f"R7 {fn.__name__} defaults byte-identical",
               params["tone"].default == AGENT_TONE
               and params["instructions"].default == "", "")
    record("R7 build_system_prompt tone/instructions are keyword-only",
           all(inspect.signature(build_system_prompt).parameters[n].kind
               == inspect.Parameter.KEYWORD_ONLY for n in ("tone", "instructions")), "")

    record("R8 composer deterministic", compose_prompt("x", "y") == compose_prompt("x", "y"), "")

    async def _run() -> None:
        cfg = load_database_configuration()
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        preview = await preview_prompt(_ctx("r9"), runner)
        record("R9 seeded v1 preview byte-identical to build_system_prompt()",
               preview == default, f"len={len(preview)}")
        again = await preview_prompt(_ctx("r9b"), runner)
        record("R9 preview deterministic", again == preview, "")

    asyncio.run(_run())
    return results


def test_agent_console_prompts():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT-CONSOLE-PROMPTS UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)

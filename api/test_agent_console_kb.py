"""Console KB helper units (change: agent-console, WU16).

Phase 4: the deactivated_at predicate hides soft-deactivated articles from
retrieval (default NULL = visible, zero behavior change for seeded rows), the
pure score_question mirrors the SQL overlap for the self-test display, and
cross-account rows stay invisible. One throwaway probe article (demo_seed_key
NULL, deleted in finally) — the wu16 base is left clean.
"""

from __future__ import annotations

import asyncio
import uuid

from api.adapters.postgres_agent_console import score_question
from api.adapters.postgres_core import (
    ConnectionPool,
    PostgresRunner,
    load_database_configuration,
)
from api.adapters.postgres_knowledge_base import (
    KnowledgeBaseRepository,
    normalize_tokens,
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

PROBE_QUESTION = "Como agendo una visita zxqblarg flibbert?"
PROBE_KEYWORDS = ["zxqblarg", "flibbert", "visita"]


def _ctx(account_id: str, tag: str) -> TenantContext:
    return TenantContext(
        user=User(USER, "demo-user"),
        agency=Agency(AGENCY, "demo-agency"),
        client_account=ClientAccount(account_id, AGENCY, "demo-account"),
        membership=Membership(USER, AGENCY, account_id, PERMS),
        correlation_id=tag,
    )


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    # Pure scorer mirrors the SQL set-intersection (dedupes, counts).
    tokens = normalize_tokens(PROBE_QUESTION)
    record("K0 scorer hits the probe overlap",
           score_question(PROBE_KEYWORDS, tokens) == 3, f"score={score_question(PROBE_KEYWORDS, tokens)}")
    record("K0 scorer dedupes repeated tokens",
           score_question(PROBE_KEYWORDS, tokens + tokens) == 3, "")
    record("K0 scorer zero on unrelated query",
           score_question(PROBE_KEYWORDS, normalize_tokens("Cual es el sentido de la vida?")) == 0, "")
    record("K0 scorer tolerates empty inputs",
           score_question([], tokens) == 0 and score_question(PROBE_KEYWORDS, []) == 0
           and score_question(None, None) == 0, "")

    async def _run() -> None:
        cfg = load_database_configuration()
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        repo = KnowledgeBaseRepository(runner)
        probe_id = str(uuid.uuid4())
        try:
            await runner.execute(
                "INSERT INTO knowledge_base_articles (id, client_account_id, demo_seed_key, "
                "category, question, keywords, answer) VALUES (%s, %s, NULL, %s, %s, %s, %s)",
                (probe_id, ACCOUNT_A, "visitas", PROBE_QUESTION, PROBE_KEYWORDS, "Probe answer."),
            )
            got = await repo.find_best_match(_ctx(ACCOUNT_A, "k1"), "zxqblarg flibbert")
            record("K1 active probe article matches",
                   got is not None and got["id"] == probe_id,
                   f"adapter={got['id'] if got else None}")
            cross = await repo.find_best_match(_ctx(ACCOUNT_B, "k2"), "zxqblarg flibbert")
            record("K2 cross-account invisible (B sees no A probe)",
                   cross is None, f"adapter={cross['id'] if cross else None}")
            await runner.execute(
                "UPDATE knowledge_base_articles SET deactivated_at = now() WHERE id = %s",
                (probe_id,),
            )
            gone = await repo.find_best_match(_ctx(ACCOUNT_A, "k3"), "zxqblarg flibbert")
            record("K3 deactivated article no longer matches",
                   gone is None, f"adapter={gone['id'] if gone else None}")
        finally:
            await runner.execute(
                "DELETE FROM knowledge_base_articles WHERE id = %s", (probe_id,))
        rows = await runner.query("SELECT count(*) AS n FROM knowledge_base_articles", ())
        record("K4 probe cleaned up (corpus back to 32)",
               int(rows[0]["n"]) == 32, f"count={rows[0]['n']}")

    asyncio.run(_run())
    return results


def test_agent_console_kb():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT-CONSOLE-KB UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)

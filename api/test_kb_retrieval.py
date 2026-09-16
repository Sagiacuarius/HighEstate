"""KB retrieval oracle test (change: agent-autonomy, WU15, read-only).

Compares the SQL ranking in `KnowledgeBaseRepository.find_best_match`
against a pure-Python twin implementing the same scoring (overlap count,
tie-break `(hits, category, id)`) on the seeded `mvp_seed_008.sql` corpus
(base `production_runtime_mvp_wu15`).

Covers: hits on expensas/garantia/visitas terms from the seed keywords,
A-vs-B isolation (B-only terms never return A rows), paraphrase-miss
returns None (escalate path, never fabricated), empty query returns None,
and zero DB mutation (article count before == after).
"""

from __future__ import annotations

import asyncio

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


def _ctx(account_id: str, tag: str) -> TenantContext:
    return TenantContext(
        user=User(USER, "demo-user"),
        agency=Agency(AGENCY, "demo-agency"),
        client_account=ClientAccount(account_id, AGENCY, "demo-account"),
        membership=Membership(USER, AGENCY, account_id, PERMS),
        correlation_id=tag,
    )


def _twin_best(corpus: list[dict], account_id: str, query: str) -> str | None:
    """Pure-Python twin of the SQL ranking: overlap count, then category, id."""
    tokens = set(normalize_tokens(query))
    if not tokens:
        return None
    scored: list[tuple[int, str, str]] = []
    for row in corpus:
        if str(row["client_account_id"]) != account_id:
            continue
        keywords: set[str] = set()
        for raw in (row["keywords"] or []):
            keywords.update(normalize_tokens(str(raw)))
        hits = len(keywords & tokens)
        if hits > 0:
            scored.append((hits, str(row["category"]), str(row["id"])))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return scored[0][2]


async def _fetch_corpus(runner: PostgresRunner) -> list[dict]:
    return await runner.query(
        "SELECT id, client_account_id, category, keywords FROM knowledge_base_articles", ())


async def _count(runner: PostgresRunner) -> int:
    rows = await runner.query("SELECT count(*) AS n FROM knowledge_base_articles", ())
    return int(rows[0]["n"])


# (label, account, query, pinned expectation or None for oracle-only)
QUERIES: list[tuple[str, str, str, str | None]] = [
    ("expensas hit A", ACCOUNT_A, "Que incluyen las expensas del departamento?", "6f000000-0000-0000-0000-000000000001"),
    ("garantia tie-break A", ACCOUNT_A, "Aceptan garantia de fiador para alquilar?", "6f000000-0000-0000-0000-000000000005"),
    ("garantia accented A", ACCOUNT_A, "Piden garantía de fiador?", "6f000000-0000-0000-0000-000000000005"),
    ("visitas hit A", ACCOUNT_A, "Como coordino una visita a una propiedad?", "6f000000-0000-0000-0000-000000000015"),
    ("pileta B-only under A", ACCOUNT_A, "pileta quincho terreno", None),
    ("pileta hit B", ACCOUNT_B, "pileta quincho terreno", "6f000000-0000-0000-0000-000000000027"),
    ("videollamada B-only under A", ACCOUNT_A, "videollamada whatsapp distancia", None),
    ("videollamada hit B", ACCOUNT_B, "videollamada whatsapp distancia", "6f000000-0000-0000-0000-000000000032"),
    ("expensas A-term under B", ACCOUNT_B, "encargado edificio mantenimiento", None),
    ("paraphrase miss A", ACCOUNT_A, "Cual es el sentido de la vida?", None),
    ("paraphrase miss B", ACCOUNT_B, "Cual es el sentido de la vida?", None),
    ("empty query A", ACCOUNT_A, "", None),
    ("empty query B", ACCOUNT_B, "   ", None),
]


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    async def _run() -> None:
        cfg = load_database_configuration()
        runner = PostgresRunner(ConnectionPool(dict(cfg)))
        repo = KnowledgeBaseRepository(runner)
        before = await _count(runner)
        corpus = await _fetch_corpus(runner)
        owner = {str(row["id"]): str(row["client_account_id"]) for row in corpus}
        record("K0 seeded corpus present on this base",
               before == 32 and len(corpus) == 32,
               f"count={before} dbname={cfg.get('dbname')}")

        for label, account, query, pinned in QUERIES:
            got = await repo.find_best_match(_ctx(account, f"kb-{label}"), query)
            got_id = got["id"] if got else None
            want = _twin_best(corpus, account, query)
            oracle_ok = got_id == want
            pin_ok = (pinned is None and got_id is None) or (pinned is not None and got_id == pinned)
            if pinned is None and label.startswith(("pileta B-only", "videollamada B-only")):
                # Isolation: must be None (no A row may match B-only terms).
                record(f"K {label} -> None (oracle agrees)", oracle_ok and pin_ok,
                       f"adapter={got_id} twin={want}")
            elif pinned is None:
                record(f"K {label} -> None (oracle agrees)", oracle_ok and got_id is None,
                       f"adapter={got_id} twin={want}")
            else:
                record(f"K {label} -> ...{pinned[-4:]} (oracle agrees)",
                       oracle_ok and pin_ok, f"adapter={got_id} twin={want}")
            # Cross-account invisibility: a hit always belongs to the context account.
            if got_id is not None:
                record(f"K isolation {label}: hit owned by the context account",
                       owner.get(got_id) == account,
                       f"adapter={got_id} owner={owner.get(got_id)}")

        after = await _count(runner)
        record("K99 zero DB mutation (count before == after)",
               before == after, f"before={before} after={after}")

    asyncio.run(_run())
    return results


def test_kb_retrieval():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"KB-RETRIEVAL WU15 UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)

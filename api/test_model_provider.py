"""Model provider unit tests (WU14, no network, no DB).

Covers the schema boundary: validate_interpretation accepts bounded valid
objects and fails closed on anything outside them; the stub classifies
deterministically and its own output validates; the persona renders.
"""

from __future__ import annotations

import asyncio
import sys

from api.application.agent_persona import build_system_prompt
from api.application.model_provider import (
    InterpretationError,
    OpenCodeGoProvider,
    ProviderError,
    StubModelProvider,
    validate_interpretation,
)


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    # U1. valid minimal interpretation passes.
    ok_case = validate_interpretation({"intent": "general", "criteria": {},
                                       "reply_text": "Hola."})
    record("U1 valid minimal passes", ok_case["intent"] == "general")

    # U2. full valid criteria is preserved with normalized types.
    full = validate_interpretation({
        "intent": "search_properties",
        "criteria": {"operation_type": "rent", "property_type": "apartment",
                     "city": " Rosario ", "budget_max": 150000, "rooms_min": 2.0,
                     "bathrooms_min": 1, "parking": True,
                     "preferred_date": "2026-09-22", "preferred_time": "10:00"},
        "reply_text": "Busco.",
    })
    ok = (full["criteria"]["city"] == "Rosario" and full["criteria"]["rooms_min"] == 2
          and full["criteria"]["budget_max"] == 150000)
    record("U2 full criteria normalized (trim, int coercion)", ok, str(full["criteria"]))

    # U3. unknown top-level key fails closed.
    try:
        validate_interpretation({"intent": "general", "criteria": {},
                                 "reply_text": "x", "extra": 1})
        record("U3 unknown top-level rejected", False)
    except InterpretationError:
        record("U3 unknown top-level rejected", True)

    # U4. unknown intent + bad enums + bad primitives fail closed.
    bad_cases = [
        {"intent": "hack_the_planet", "criteria": {}, "reply_text": "x"},
        {"intent": "general", "criteria": {}, "reply_text": ""},
        {"intent": "general", "criteria": {"operation_type": "lease"}, "reply_text": "x"},
        {"intent": "general", "criteria": {"budget_max": "150000"}, "reply_text": "x"},
        {"intent": "general", "criteria": {"budget_max": -5}, "reply_text": "x"},
        {"intent": "general", "criteria": {"rooms_min": 1.5}, "reply_text": "x"},
        {"intent": "general", "criteria": {"parking": "yes"}, "reply_text": "x"},
        {"intent": "general", "criteria": {"preferred_time": "25:00"}, "reply_text": "x"},
        {"intent": "general", "criteria": {"preferred_date": "2026-13-40"}, "reply_text": "x"},
    ]
    fails = 0
    for case in bad_cases:
        try:
            validate_interpretation(case)
        except InterpretationError:
            fails += 1
    record("U4 invalid shapes rejected (9 cases)", fails == 9, f"rejected={fails}/9")

    # U5. stub deterministic across identical calls + output validates.
    stub = StubModelProvider()
    a = asyncio.run(stub.generate("sys", "Busco depto en Rosario, 2 ambientes, presupuesto hasta 150000"))
    b = asyncio.run(stub.generate("sys", "Busco depto en Rosario, 2 ambientes, presupuesto hasta 150000"))
    va = validate_interpretation(a)
    ok = (a == b and va["intent"] == "search_properties"
          and va["criteria"].get("property_type") == "apartment"
          and va["criteria"].get("city") == "Rosario"
          and va["criteria"].get("rooms_min") == 2
          and va["criteria"].get("budget_max") == 150000.0)
    record("U5 stub search classification + extraction deterministic", ok, str(va["criteria"]))

    # U6. stub schedule_visit extracts preferred date/time.
    s = validate_interpretation(asyncio.run(stub.generate(
        "sys", "Quiero coordinar una visita el 2026-09-22 a las 10:00")))
    ok = (s["intent"] == "schedule_visit"
          and s["criteria"].get("preferred_date") == "2026-09-22"
          and s["criteria"].get("preferred_time") == "10:00")
    record("U6 stub schedule_visit + preferred date/time", ok, str(s["criteria"]))

    # U7. stub reschedule beats visit keywords.
    r = validate_interpretation(asyncio.run(stub.generate(
        "sys", "¿Podemos reprogramar la visita del martes?")))
    record("U7 stub reschedule wins over visit", r["intent"] == "reschedule", r["intent"])

    # U8. persona prompt renders identity + rules and is non-empty.
    prompt = build_system_prompt()
    record("U8 persona renders (Sofía + JSON contract)",
           "Sofía" in prompt and '"intent"' in prompt and len(prompt) > 200)

    # U9. stub-only path never imports the LangChain transport (offline demo
    # stays honest: no key, no network, no langchain import).
    record("U9 stub runs import no langchain_openai transport",
           "langchain_openai" not in sys.modules,
           f"present={'langchain_openai' in sys.modules}")

    # U10. chain failures fail closed (faked chain: no network, no langchain
    # import here either): transport errors -> ProviderError, schema-violating
    # model output -> InterpretationError.
    provider = OpenCodeGoProvider(api_key="test-key")

    class _TransportBoom:
        async def ainvoke(self, _inputs):
            raise RuntimeError("boom")

    class _BadSchemaMessage:
        content = ('{"intent": "hack_the_planet", "criteria": {}, '
                   '"reply_text": "x"}')

    class _BadSchemaChain:
        async def ainvoke(self, _inputs):
            return _BadSchemaMessage()

    provider._interpretation_chain = lambda: _TransportBoom()  # type: ignore[method-assign]
    try:
        asyncio.run(provider.generate("sys", "hola"))
        transport_ok, transport_detail = False, "no error raised"
    except ProviderError:
        transport_ok, transport_detail = True, "ProviderError"
    except Exception as error:
        transport_ok, transport_detail = False, f"wrong={type(error).__name__}"
    provider._interpretation_chain = lambda: _BadSchemaChain()  # type: ignore[method-assign]
    try:
        asyncio.run(provider.generate("sys", "hola"))
        schema_ok, schema_detail = False, "no error raised"
    except InterpretationError:
        schema_ok, schema_detail = True, "InterpretationError"
    except Exception as error:
        schema_ok, schema_detail = False, f"wrong={type(error).__name__}"
    record("U10 chain failure fail-closed (transport->ProviderError, "
           "bad schema->InterpretationError)",
           transport_ok and schema_ok,
           f"transport={transport_detail} schema={schema_detail}")

    return results


def test_model_provider():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"MODEL-PROVIDER WU14 UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)

"""Agent signal extraction units (change: agent-custom-field-writes, WU18).

Pure: no DB, no network. Pins `extract_signals` — each of the 7 signals
resolves exactly once from a crafted body/criteria, unresolvable keys stay
absent (never guessed, never empty string), and repeated calls are
byte-deterministic.
"""

from __future__ import annotations

from api.application.agent_signals import SIGNAL_KEYS, extract_signals


def _interp(**criteria) -> dict:
    return {"intent": "search_properties", "criteria": criteria,
            "reply_text": "hola"}


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, str(detail)))

    record("S0 catalog holds the 7 owner-approved signals",
           SIGNAL_KEYS == frozenset({"phone", "email", "presupuesto_max",
                                     "barrio", "tipo_propiedad",
                                     "fecha_objetivo", "medio_de_contacto"}),
           f"keys={sorted(SIGNAL_KEYS)}")

    out = extract_signals(_interp(budget_max=250000), "hola")
    record("S1 presupuesto_max renders integral budgets as plain digits",
           out.get("presupuesto_max") == "250000", f"out={out}")

    out = extract_signals(_interp(budget_max=250000.0), "hola")
    record("S1b float-integral budget has no decimal point",
           out.get("presupuesto_max") == "250000", f"out={out}")

    out = extract_signals(_interp(city="Palermo"), "hola")
    record("S2 barrio reuses validated criteria city",
           out.get("barrio") == "Palermo", f"out={out}")

    out = extract_signals(_interp(property_type="departamento"), "hola")
    record("S3 tipo_propiedad reuses validated criteria property_type",
           out.get("tipo_propiedad") == "departamento", f"out={out}")

    out = extract_signals(_interp(preferred_date="2026-11-04"), "hola")
    record("S4 fecha_objetivo reuses validated criteria preferred_date",
           out.get("fecha_objetivo") == "2026-11-04", f"out={out}")

    out = extract_signals(_interp(), "mi telefono es +54 9 11 1234-5678")
    record("S5 phone resolves from body and normalizes to digits",
           out.get("phone") == "+5491112345678", f"out={out}")

    out = extract_signals(_interp(), "escribime a Ana.Lopez@ejemplo.com.ar")
    record("S6 email resolves from body",
           out.get("email") == "Ana.Lopez@ejemplo.com.ar", f"out={out}")

    out = extract_signals(_interp(), "hablamos por whatsapp al 1123456789")
    record("S7 medio_de_contacto is phone on explicit channel word",
           out.get("medio_de_contacto") == "phone"
           and out.get("phone") == "1123456789", f"out={out}")

    out = extract_signals(_interp(), "te mando todo por correo a a@b.com")
    record("S7b medio_de_contacto is email on explicit channel word",
           out.get("medio_de_contacto") == "email"
           and out.get("email") == "a@b.com", f"out={out}")

    out = extract_signals(_interp(), "mi numero es 1123456789")
    record("S7c medio_de_contacto falls back to the resolved channel",
           out.get("medio_de_contacto") == "phone", f"out={out}")

    out = extract_signals(_interp(), "hola, busco algo lindo")
    record("S8 empty body/criteria resolves nothing (never guessed)",
           out == {}, f"out={out}")

    out = extract_signals(_interp(budget_max=-5, city="  ",
                                  property_type="",
                                  preferred_date="2026-13-99"),
                          "sin datos de contacto")
    record("S9 invalid criteria stay absent (never empty string)",
           out == {}, f"out={out}")

    out = extract_signals(_interp(), "llamame al 123")
    record("S10 short digit run is not a phone number",
           "phone" not in out, f"out={out}")

    probe = _interp(budget_max=180000, city="Belgrano",
                    property_type="casa", preferred_date="2026-12-01")
    body = "whatsapp +54 11 5555-6666 o juan@mail.com, escribime"
    first = extract_signals(probe, body)
    second = extract_signals(dict(probe), str(body))
    record("S11 byte-deterministic across repeated calls",
           first == second and list(first) == list(second)
           and first.get("presupuesto_max") == "180000"
           and first.get("barrio") == "Belgrano"
           and first.get("tipo_propiedad") == "casa"
           and first.get("fecha_objetivo") == "2026-12-01",
           f"out={first}")

    return results


def test_agent_signals():
    outcomes = run_all()
    failures = [name for name, ok, _ in outcomes if not ok]
    assert failures == [], f"failing units: {failures}"


if __name__ == "__main__":
    outcomes = run_all()
    passed = sum(1 for _, ok, _ in outcomes if ok)
    for name, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'} {name} :: {detail}")
    print(f"AGENT-SIGNALS UNITS: {passed}/{len(outcomes)} passed")
    raise SystemExit(0 if passed == len(outcomes) else 1)

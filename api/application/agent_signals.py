"""Agent signal catalog + deterministic extraction (change: agent-custom-field-writes).

Pure, stdlib only: no I/O, no model text, no DB, no clock. `extract_signals`
maps an already-validated interpretation plus the raw inbound body to a
`{signal_key: value}` dict over the closed 7-signal catalog. Unresolvable
keys are ABSENT (never guessed, never empty string); byte-deterministic for
identical inputs (fixed insertion order, first body match wins).

Source of each signal:

- presupuesto_max <- criteria["budget_max"] (validated positive number).
  Exact format: plain base-10 digits with no separators; integral values
  render with no decimal point ("250000", never "250000.0"); a non-integral
  value renders via str(value) ("250000.5").
- barrio <- criteria["city"] (validated bounded string), stripped.
- tipo_propiedad <- criteria["property_type"] (validated enum member), as-is.
- fecha_objetivo <- criteria["preferred_date"] (validated YYYY-MM-DD real
  date), as-is.
- phone <- first body match of PHONE_PATTERN (below) whose digit count is
  in [7, 15]; value normalized to an optional leading "+" plus digits only
  (e.g. "+54 9 11 1234-5678" -> "+5491112345678").
- email <- first body match of EMAIL_PATTERN (below), as-is.
- medio_de_contacto <- explicit channel words first, resolved channel as
  fallback: "phone" if a phone word is present (and no email word), "email"
  if an email word is present (and no phone word); when both or neither
  word class is present, "phone" if phone resolved else "email" if email
  resolved, else absent.

Bounded body patterns (only the first 4000 body chars are scanned):

- PHONE_PATTERN = r"\\+?\\d[\\d\\s.\\-()]{5,22}\\d" — a bounded run (no nested
  quantifiers, no backtracking risk); the candidate is accepted only when
  its digit count is within [7, 15].
- EMAIL_PATTERN = r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9]"
  r"(?:[A-Za-z0-9.-]{0,61}[A-Za-z0-9])?\\.[A-Za-z]{2,}" — local part capped
  at 64 chars, domain labels bounded, TLD >= 2 letters.

Explicit channel word lists (matched case-insensitively as substrings):

- PHONE_CHANNEL_WORDS = ("whatsapp", "llamada", "llamame", "llamáme",
  "llamarme", "llamar", "telefono", "teléfono", "celular", "movil",
  "móvil", "sms", "phone").
- EMAIL_CHANNEL_WORDS = ("email", "e-mail", "correo", "mail").
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

SIGNAL_KEYS = frozenset({
    "phone",
    "email",
    "presupuesto_max",
    "barrio",
    "tipo_propiedad",
    "fecha_objetivo",
    "medio_de_contacto",
})

_BODY_MAX = 4000

_PHONE_RE = re.compile(r"\+?\d[\d\s.\-()]{5,22}\d")
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9]"
    r"(?:[A-Za-z0-9.-]{0,61}[A-Za-z0-9])?\.[A-Za-z]{2,}"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PHONE_CHANNEL_WORDS = (
    "whatsapp", "llamada", "llamame", "llamáme", "llamarme", "llamar",
    "telefono", "teléfono", "celular", "movil", "móvil", "sms", "phone",
)
EMAIL_CHANNEL_WORDS = ("email", "e-mail", "correo", "mail")


def _is_real_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _criteria(interpretation: Any) -> dict[str, Any]:
    if not isinstance(interpretation, dict):
        return {}
    criteria = interpretation.get("criteria")
    return criteria if isinstance(criteria, dict) else {}


def _extract_phone(body: str) -> str | None:
    for match in _PHONE_RE.finditer(body):
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if 7 <= len(digits) <= 15:
            return ("+" if raw.lstrip().startswith("+") else "") + digits
    return None


def _extract_email(body: str) -> str | None:
    match = _EMAIL_RE.search(body)
    return match.group(0) if match else None


def extract_signals(interpretation: dict, body: str) -> dict[str, str]:
    """Deterministic {signal_key: value}; unresolvable keys are absent."""
    criteria = _criteria(interpretation)
    text = body if isinstance(body, str) else ""
    scanned = text[:_BODY_MAX]
    out: dict[str, str] = {}

    budget = criteria.get("budget_max")
    if _is_real_number(budget) and budget > 0:
        out["presupuesto_max"] = (
            str(int(budget)) if float(budget).is_integer() else str(budget)
        )

    city = criteria.get("city")
    if isinstance(city, str) and city.strip():
        out["barrio"] = city.strip()

    property_type = criteria.get("property_type")
    if isinstance(property_type, str) and property_type.strip():
        out["tipo_propiedad"] = property_type.strip()

    preferred_date = criteria.get("preferred_date")
    if isinstance(preferred_date, str) and _DATE_RE.match(preferred_date):
        try:
            date.fromisoformat(preferred_date)
        except ValueError:
            pass
        else:
            out["fecha_objetivo"] = preferred_date

    phone = _extract_phone(scanned)
    if phone is not None:
        out["phone"] = phone
    email = _extract_email(scanned)
    if email is not None:
        out["email"] = email

    lowered = scanned.lower()
    phone_word = any(word in lowered for word in PHONE_CHANNEL_WORDS)
    email_word = any(word in lowered for word in EMAIL_CHANNEL_WORDS)
    if phone_word and not email_word:
        out["medio_de_contacto"] = "phone"
    elif email_word and not phone_word:
        out["medio_de_contacto"] = "email"
    elif phone is not None:
        out["medio_de_contacto"] = "phone"
    elif email is not None:
        out["medio_de_contacto"] = "email"
    return out

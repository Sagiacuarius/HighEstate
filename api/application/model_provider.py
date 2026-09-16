"""ModelProvider port + adapters (WU14).

The single blast-radius boundary between the LLM and the system: a provider
receives (system, user) text and returns raw JSON to be schema-validated. It
never sees the DB, context objects, or write paths.

Adapters:
- OpenCodeGoProvider: OpenAI-compatible chat completions against the
  opencode-go gateway (https://opencode.ai/zen/go/v1/chat/completions),
  Bearer OPENCODE_GO_API_KEY, JSON response mode, temperature 0.
- StubModelProvider: deterministic keyword classifier (no network). Used by
  the tests and as the automatic fallback when the API key is absent.

`select_model_provider()` decides from the environment; the chosen identity is
recorded on every draft (`model_provider`, `model_name`).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol

from api.domain.agent_draft import CRITERIA_KEYS, INTENTS
from api.domain.property_catalog import OPERATION_TYPES_V11, PROPERTY_TYPES

ZEN_GO_CHAT_URL = "https://opencode.ai/zen/go/v1/chat/completions"
DEFAULT_GO_MODEL = "deepseek-v4-pro"
API_KEY_ENV = "OPENCODE_GO_API_KEY"

# The only top-level keys the interpretation may carry.
INTERPRETATION_KEYS = frozenset({"intent", "criteria", "reply_text"})


class InterpretationError(ValueError):
    """The model output does not match the bounded schema (fail closed)."""


class ProviderError(RuntimeError):
    """Transport/parse failure talking to the model (fail closed)."""


class ModelProvider(Protocol):
    name: str
    model: str

    async def generate(self, system: str, user: str) -> Any:
        ...


# --- schema validation ------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _is_real_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_criteria(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise InterpretationError("criteria must be an object")
    unknown = set(raw) - set(CRITERIA_KEYS)
    if unknown:
        raise InterpretationError(f"unknown criteria keys: {sorted(unknown)}")
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            continue
        if key == "operation_type":
            if value not in OPERATION_TYPES_V11:
                raise InterpretationError("criteria.operation_type is invalid")
        elif key == "property_type":
            if value not in PROPERTY_TYPES:
                raise InterpretationError("criteria.property_type is invalid")
        elif key in ("city",):
            if (not isinstance(value, str) or not value.strip()
                    or len(value.strip()) > 120):
                raise InterpretationError("criteria.city is invalid")
            value = value.strip()
        elif key == "budget_max":
            if not _is_real_number(value) or value <= 0:
                raise InterpretationError("criteria.budget_max must be a positive number")
        elif key in ("rooms_min", "bathrooms_min"):
            if not _is_real_number(value) or value < 0 or int(value) != value:
                raise InterpretationError(f"criteria.{key} must be a non-negative integer")
            value = int(value)
        elif key == "parking":
            if not isinstance(value, bool):
                raise InterpretationError("criteria.parking must be a boolean")
        elif key == "preferred_date":
            if not isinstance(value, str) or not _DATE_RE.match(value):
                raise InterpretationError("criteria.preferred_date must be YYYY-MM-DD")
            from datetime import date
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise InterpretationError("criteria.preferred_date is not a real date") from error
        elif key == "preferred_time":
            if not isinstance(value, str) or not _TIME_RE.match(value):
                raise InterpretationError("criteria.preferred_time must be HH:MM")
        out[key] = value
    return out


def validate_interpretation(raw: Any) -> dict[str, Any]:
    """Strictly normalize a raw model object into the bounded interpretation.

    Raises InterpretationError on anything outside the schema; the caller must
    persist nothing when this raises.
    """
    if not isinstance(raw, dict):
        raise InterpretationError("interpretation must be an object")
    unknown = set(raw) - INTERPRETATION_KEYS
    if unknown:
        raise InterpretationError(f"unknown top-level keys: {sorted(unknown)}")
    intent = raw.get("intent")
    if intent not in INTENTS:
        raise InterpretationError("intent is not one of the allowed values")
    criteria = _validate_criteria(raw.get("criteria"))
    reply = raw.get("reply_text")
    if not isinstance(reply, str) or not reply.strip() or len(reply) > 4000:
        raise InterpretationError("reply_text must be a non-empty string (max 4000)")
    return {"intent": intent, "criteria": criteria, "reply_text": reply.strip()}


# --- real provider -----------------------------------------------------------


class OpenCodeGoProvider:
    """OpenAI-compatible chat completions against the opencode-go gateway."""

    name = "opencode-go"

    def __init__(self, api_key: str, *, model: str = DEFAULT_GO_MODEL,
                 timeout_seconds: float = 30.0):
        self._api_key = api_key
        self.model = model
        self._timeout = timeout_seconds

    async def generate(self, system: str, user: str) -> Any:
        import httpx

        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(ZEN_GO_CHAT_URL, json=payload, headers=headers)
        except Exception as error:  # network/timeout: typed, no detail leak
            raise ProviderError(f"provider request failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise ProviderError(f"provider returned HTTP {response.status_code}")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as error:
            raise ProviderError("provider response is not valid JSON content") from error


# --- deterministic stub ------------------------------------------------------


class StubModelProvider:
    """Deterministic keyword interpretation. No network, no secrets."""

    name = "stub"

    def __init__(self, *, model: str = "stub-keyword-v1"):
        self.model = model

    async def generate(self, system: str, user: str) -> Any:
        return self.interpret(user)

    @staticmethod
    def interpret(body: str) -> dict[str, Any]:
        text = body.lower()
        intent = _classify(text)
        criteria = _extract(text)
        return {"intent": intent, "criteria": criteria,
                "reply_text": _reply(intent, criteria)}


_CITY_HINTS = ("palermo", "belgrano", "caballito", "nuñez", "nunez", "centro",
               "rosario", "córdoba", "cordoba", "mendoza", "martínez", "martinez",
               "victoria", "tigre", "pinamar", "cariló", "carilo")


def _classify(text: str) -> str:
    if any(k in text for k in ("reprogr", "mover la visita", "mover la cita",
                               "cambiar la visita", "cambiar la cita",
                               "correr la visita")):
        return "reschedule"
    if any(k in text for k in ("visita", "visitar", "agendar", "agendá", "turno",
                               "coordinar", "conocer la propiedad", "ir a ver")):
        return "schedule_visit"
    if any(k in text for k in ("alquiler", "alquilo", "alquilar", "venta", "compro",
                               "comprar", "departamento", "depto", "casa", "ambientes",
                               "dormitorios", "busc", "propiedad", "propiedades",
                               "monoambiente", "ph", "lote", "terreno", "oficina",
                               "local comercial", "cochera")):
        return "search_properties"
    if any(k in text for k in ("prefiero", "mejor", "actualizar", "cambio mis",
                               "ahora quiero", "mis datos")):
        return "update_preferences"
    if "?" in text or any(k in text for k in ("cuánto", "cuanto", "cómo", "incluye",
                                              "tiene", "es")):
        return "ask_question"
    return "general"


def _extract(text: str) -> dict[str, Any]:
    criteria: dict[str, Any] = {}
    if any(k in text for k in ("alquiler", "alquilo", "alquilar", "rent", "renta")):
        criteria["operation_type"] = "rent"
    elif any(k in text for k in ("comprar", "compro", "venta", "sale", "adquirir")):
        criteria["operation_type"] = "sale"
    elif any(k in text for k in ("temporario", "temporal", "por unos meses")):
        criteria["operation_type"] = "temporary_rent"
    if any(k in text for k in ("departamento", "depto", "monoambiente", "apartamento")):
        criteria["property_type"] = "apartment"
    elif "casa" in text:
        criteria["property_type"] = "house"
    elif re.search(r"\bph\b", text):
        criteria["property_type"] = "ph"
    elif any(k in text for k in ("lote", "terreno")):
        criteria["property_type"] = "land"
    for city in _CITY_HINTS:
        if city in text:
            criteria["city"] = city.capitalize()
            break
    m = re.search(r"(\d+)\s*(?:ambientes?\b|amb\b|dormitorios?\b|habitaciones?\b)", text)
    if m:
        criteria["rooms_min"] = int(m.group(1))
    m = re.search(r"(\d+)\s*baños?", text)
    if m:
        criteria["bathrooms_min"] = int(m.group(1))
    m = re.search(r"(?:budget|presupuesto|hasta|max)[^\d]{0,12}(\d{3,9})", text)
    if m:
        criteria["budget_max"] = float(m.group(1))
    if any(k in text for k in ("cochera", "estacionamiento", "garage", "parking")):
        criteria["parking"] = True
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if m:
        criteria["preferred_date"] = m.group(1)
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if m:
        criteria["preferred_time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
    return criteria


_REPLY_LINES = {
    "search_properties": "Ya estoy buscando propiedades que coincidan con lo que pedís y te las acerco para que las revises.",
    "schedule_visit": "Perfecto, coordinamos la visita. Te confirmo horarios apenas el asesor los revise.",
    "reschedule": "Sin problema, vemos la nueva fecha para la visita y te confirmamos apenas esté definido.",
    "ask_question": "Buena pregunta. La derivo al asesor y te respondo apenas lo confirme.",
    "update_preferences": "Tomo el cambio de preferencias para ajustar la búsqueda.",
    "general": "Gracias por escribir. Quedo atenta a lo que necesites.",
}


def _reply(intent: str, criteria: dict[str, Any]) -> str:
    base = _REPLY_LINES[intent]
    return f"¡Hola! {base} — Sofía"


def select_model_provider() -> ModelProvider:
    """Key present → real gateway; absent → deterministic stub (demo default)."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    if key:
        model = os.environ.get("OPENCODE_GO_MODEL", "").strip() or DEFAULT_GO_MODEL
        return OpenCodeGoProvider(key, model=model)
    return StubModelProvider()

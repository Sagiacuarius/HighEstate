"""ModelProvider port + adapters (WU14).

The single blast-radius boundary between the LLM and the system: a provider
receives (system, user) text and returns the schema-validated interpretation. It
never sees the DB, context objects, or write paths.

Adapters:
- OpenCodeGoProvider: LangChain-backed transport (`langchain_openai.ChatOpenAI`
  built lazily inside the class) against the opencode-go gateway
  (https://opencode.ai/zen/go/v1, `/chat/completions` appended by the
  OpenAI-compatible client), api_key OPENCODE_GO_API_KEY, JSON response mode,
  temperature 0. The module top-level stays langchain-free so the stub path
  never imports it. Interpretation = RunnableSequence `chat_prompt | llm`,
  content string -> json.loads -> the unchanged `validate_interpretation`
  still gating everything; FAQ tone composition = `answer_prompt | llm |
  StrOutputParser`, bounded before persistence.
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

ZEN_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
ZEN_GO_CHAT_URL = ZEN_GO_BASE_URL + "/chat/completions"
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
    """LangChain-backed interpretation against the opencode-go gateway.

    Transport only: `chat_prompt | llm` (RunnableSequence) returns the raw
    model content string; `json.loads` + `validate_interpretation` still gate
    everything fail-closed. The langchain imports live inside the methods so
    the module top-level (and the stub path) never imports langchain.
    """

    name = "opencode-go"

    def __init__(self, api_key: str, *, model: str = DEFAULT_GO_MODEL,
                 timeout_seconds: float = 30.0):
        self._api_key = api_key
        self.model = model
        self._timeout = timeout_seconds

    def _llm(self):
        """Lazily built ChatOpenAI (keeps the module top-level langchain-free)."""
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=self.model,
            api_key=self._api_key,
            base_url=ZEN_GO_BASE_URL,
            temperature=0,
            model_kwargs={"response_format": {"type": "json_object"}},
            request_timeout=self._timeout,
        )

    def _interpretation_chain(self):
        """RunnableSequence `chat_prompt | llm` for the bounded interpretation."""
        from langchain_core.prompts import ChatPromptTemplate

        chat_prompt = ChatPromptTemplate.from_messages([
            ("system", "{system}"),
            ("human", "{user}"),
        ])
        return chat_prompt | self._llm()

    async def generate(self, system: str, user: str) -> Any:
        chain = self._interpretation_chain()
        try:
            message = await chain.ainvoke({"system": system, "user": user})
        except Exception as error:  # network/timeout: typed, no detail leak
            raise ProviderError(f"provider request failed: {type(error).__name__}") from error
        try:
            content = message.content
            if isinstance(content, list):  # content blocks: join text parts
                content = "".join(
                    part.get("text", "") if isinstance(part, dict)
                    else str(getattr(part, "text", part))
                    for part in content
                )
            raw = json.loads(content)
        except Exception as error:
            raise ProviderError("provider response is not valid JSON content") from error
        # InterpretationError propagates fail-closed (schema unchanged).
        return validate_interpretation(raw)

    async def compose_answer(self, *, ground_text: str, user_question: str) -> str:
        """FAQ tone composition over grounded text only.

        `answer_prompt | llm | StrOutputParser`; the result is bounded
        (non-empty, max 2000 chars) before the caller may persist it.
        """
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.prompts import ChatPromptTemplate

        answer_prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You compose a short customer reply in Rioplatense Spanish using "
             "only the grounded context below. Never invent facts outside the "
             "context. Keep it under 2000 characters."),
            ("human", "Context:\n{context}\n\nQuestion:\n{question}"),
        ])
        chain = answer_prompt | self._llm() | StrOutputParser()
        try:
            text = await chain.ainvoke({"context": ground_text,
                                        "question": user_question})
        except Exception as error:  # network/timeout: typed, no detail leak
            raise ProviderError(f"provider request failed: {type(error).__name__}") from error
        text = text.strip() if isinstance(text, str) else str(text).strip()
        if not text or len(text) > 2000:
            raise ProviderError("provider answer out of bounds")
        return text


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

"""Agent Console write commands (change: agent-console, WU16).

On the accepted `CrmCommandBase` template (canonical hash, reserve -> apply ->
audit + outbox -> one transaction; replay returns the stored result), mirroring
`agent_commands_wu14.py` / `agent_commands_wu15.py` exactly (same envelope
mapping at the route layer: denied -> 404/403, validation -> 400,
conflict -> 409):

- UpdateAgentSettingsCommand upserts the per-account settings row + policy
  matrix rows (operation `agent.settings.update`).
- CreateAgentPromptVersionCommand appends a prompt version and flips
  `is_active` under a per-account advisory lock; revert is an INSERT copying
  the target content, never an UPDATE (operation `agent.prompt.version`).
  Any payload key outside {tone, instructions} fails closed at the route;
  the composer keeps the skeleton code-only (no skeleton parameter exists).
- CreateKbArticleCommand / UpdateKbArticleCommand / DeactivateKbArticleCommand
  (operations `agent.kb.create` / `agent.kb.update` / `agent.kb.deactivate`):
  category inside the 8-value CHECK domain, soft-deactivate sets
  `deactivated_at`, console rows carry `demo_seed_key NULL`.
- UpdateCalendarConfigCommand upserts the per-account scheduling window
  (operation `agent.calendar.update`); invalid windows fail closed 400.
- UpdateFieldAllowlistCommand replaces the per-account allowlist
  (operation `agent.allowlist.update`); members inside the CHECK domain.

Idempotency keys are `console:{scope}:{entity}:{key}` (built at the route).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from api.application.agent_persona import AGENT_TONE, compose_prompt
from api.application.agent_policy import AUTO_ACTION_TYPES, HANDOFF_ACTION
from api.application.crm_commands_wu5 import (
    CrmCommandBase,
    CrmCommandDenied,
    CrmCommandValidationError,
)
from api.tenant_boundary import TenantContext

# The 7 CHECK-domain action names of agent_policy_matrix: the AUTO whitelist
# plus the mechanics-only handoff type (mirrors the console read adapter).
CONSOLE_ACTION_TYPES = frozenset(AUTO_ACTION_TYPES) | {HANDOFF_ACTION}

VERDICTS = frozenset({"auto", "escalate"})

# 8-value CHECK domain of knowledge_base_articles.category (migration 008).
KB_CATEGORIES = frozenset({
    "expensas", "escritura", "garantia", "documentos",
    "financiacion", "amenities", "requisitos", "visitas",
})

# CHECK domain of agent_field_allowlist.column_name (migration 009).
ALLOWLIST_COLUMNS = frozenset({"stage_id", "status", "selected_property_id"})

# Skeleton immutability (owner decision 2a): the only accepted prompt payload
# keys. Anything else fails closed before reaching the composer.
PROMPT_KEYS = frozenset({"tone", "instructions"})

_QUESTION_MAX = 500
_ANSWER_MAX = 4000
_KEYWORDS_MAX = 32
_KEYWORD_MAX = 64
_LABEL_MAX = 200


def _require_text(value: Any, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CrmCommandValidationError(f"{name} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum:
        raise CrmCommandValidationError(f"{name} exceeds {maximum} characters")
    return text


def _require_keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise CrmCommandValidationError("keywords must be a list of strings")
    if len(value) > _KEYWORDS_MAX:
        raise CrmCommandValidationError(
            f"keywords exceeds {_KEYWORDS_MAX} entries")
    cleaned: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise CrmCommandValidationError(
                "keywords must be a list of strings")
        if len(entry.strip()) > _KEYWORD_MAX:
            raise CrmCommandValidationError("keyword exceeds 64 characters")
        cleaned.append(entry.strip())
    return cleaned


def _parse_hour(value: Any, name: str) -> int:
    """Accept an int hour or "HH:MM" text; anything else fails closed."""
    if isinstance(value, bool):
        raise CrmCommandValidationError(f"{name} must be an hour or HH:MM")
    if isinstance(value, int):
        hour, minute = value, 0
    elif isinstance(value, str):
        parts = value.strip().split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            raise CrmCommandValidationError(
                f"{name} must be an hour or HH:MM")
        if len(parts) > 2 or minute not in range(0, 60):
            raise CrmCommandValidationError(
                f"{name} must be an hour or HH:MM")
    else:
        raise CrmCommandValidationError(f"{name} must be an hour or HH:MM")
    if hour not in range(0, 25):
        raise CrmCommandValidationError(f"{name} is out of range")
    return hour * 60 + minute


def _format_window(total_minutes: int) -> str:
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _parse_timezone(value: Any) -> str:
    """Validate an IANA timezone name; anything else fails closed (400)."""
    if (isinstance(value, bool) or not isinstance(value, str)
            or not value.strip()):
        raise CrmCommandValidationError(
            "timezone must be a non-empty IANA timezone string")
    name = value.strip()
    try:
        ZoneInfo(name)
    except Exception:
        raise CrmCommandValidationError(
            f"timezone {name!r} is not a valid IANA timezone") from None
    return name


class UpdateAgentSettingsCommand(CrmCommandBase):
    """Upsert per-account settings + policy matrix (operation
    `agent.settings.update`)."""

    operation = "agent.settings.update"
    audit_resource_type = "agent_settings"
    outbox_event_type = "AgentSettingsUpdated"

    async def execute_settings(self, context: TenantContext | None, *,
                               global_auto: Any = None,
                               calendar_auto_enabled: Any = None,
                               matrix: Any = None,
                               idempotency_key: str):
        if (global_auto is None and calendar_auto_enabled is None
                and not matrix):
            raise CrmCommandValidationError("nothing to update")
        if global_auto is not None and not isinstance(global_auto, bool):
            raise CrmCommandValidationError("global_auto must be a boolean")
        if (calendar_auto_enabled is not None
                and not isinstance(calendar_auto_enabled, bool)):
            raise CrmCommandValidationError(
                "calendar_auto_enabled must be a boolean")
        entries = self._validate_matrix(matrix)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"global_auto": global_auto,
                             "calendar_auto_enabled": calendar_auto_enabled,
                             "matrix": entries},
            global_auto=global_auto,
            calendar_auto_enabled=calendar_auto_enabled,
            matrix=entries,
        )

    @staticmethod
    def _validate_matrix(matrix: Any) -> list[dict[str, str]]:
        if matrix is None:
            return []
        if not isinstance(matrix, list):
            raise CrmCommandValidationError("matrix must be a list")
        entries: list[dict[str, str]] = []
        for entry in matrix:
            if not isinstance(entry, dict):
                raise CrmCommandValidationError(
                    "matrix entries must be objects")
            action = entry.get("action_type")
            verdict = entry.get("verdict")
            if action not in CONSOLE_ACTION_TYPES:
                raise CrmCommandValidationError(
                    "matrix action_type is invalid")
            if verdict not in VERDICTS:
                raise CrmCommandValidationError(
                    "matrix verdict must be auto or escalate")
            entries.append({"action_type": action, "verdict": verdict})
        return entries

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT global_auto, calendar_auto_enabled FROM agent_settings "
            "WHERE client_account_id = %s",
            (account_id,),
        )
        current = await cur.fetchone()
        global_auto = (intent["global_auto"] if intent["global_auto"]
                       is not None else (bool(current["global_auto"])
                                         if current else True))
        calendar_allowed = (intent["calendar_auto_enabled"]
                            if intent["calendar_auto_enabled"] is not None
                            else (bool(current["calendar_auto_enabled"])
                                  if current else True))
        await cur.execute(
            """
            INSERT INTO agent_settings
              (id, client_account_id, demo_seed_key,
               global_auto, calendar_auto_enabled, updated_at)
            VALUES (%s, %s, NULL, %s, %s, now())
            ON CONFLICT (client_account_id) DO UPDATE SET
              global_auto = EXCLUDED.global_auto,
              calendar_auto_enabled = EXCLUDED.calendar_auto_enabled,
              updated_at = now()
            """,
            (str(uuid4()), account_id, global_auto, calendar_allowed),
        )
        for entry in intent["matrix"]:
            await cur.execute(
                """
                INSERT INTO agent_policy_matrix
                  (id, client_account_id, action_type, demo_seed_key, verdict)
                VALUES (%s, %s, %s, NULL, %s)
                ON CONFLICT (client_account_id, action_type) DO UPDATE SET
                  verdict = EXCLUDED.verdict
                """,
                (str(uuid4()), account_id,
                 entry["action_type"], entry["verdict"]),
            )
        await cur.execute(
            "SELECT action_type, verdict FROM agent_policy_matrix "
            "WHERE client_account_id = %s",
            (account_id,),
        )
        full = {row["action_type"]: row["verdict"]
                for row in await cur.fetchall()}
        matrix = [{"action_type": action,
                   "verdict": full.get(action,
                                       "auto" if action in AUTO_ACTION_TYPES
                                       else "escalate")}
                  for action in sorted(CONSOLE_ACTION_TYPES)]
        return {"resource_id": account_id,
                "global_auto": global_auto,
                "calendar_auto_enabled": calendar_allowed,
                "matrix": matrix,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"global_auto": payload["global_auto"],
                "calendar_auto_enabled": payload["calendar_auto_enabled"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"],
                "global_auto": payload["global_auto"],
                "calendar_auto_enabled": payload["calendar_auto_enabled"]}


class CreateAgentPromptVersionCommand(CrmCommandBase):
    """Append a prompt version + flip active under a per-account lock;
    revert inserts a copy of the target, never an UPDATE (operation
    `agent.prompt.version`)."""

    operation = "agent.prompt.version"
    audit_resource_type = "agent_prompt"
    outbox_event_type = "AgentPromptVersionCreated"

    async def execute_create(self, context: TenantContext | None, *,
                             tone: Any, instructions: Any,
                             idempotency_key: str):
        if not isinstance(tone, str):
            raise CrmCommandValidationError("tone must be a string")
        if not isinstance(instructions, str):
            raise CrmCommandValidationError("instructions must be a string")
        # Empty/whitespace tone fails closed to the accepted default voice,
        # mirroring the composer (never an empty stored tone).
        effective_tone = tone.strip() or AGENT_TONE
        clean_instructions = instructions.strip()
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"tone": effective_tone,
                             "instructions": clean_instructions},
            tone=effective_tone, instructions=clean_instructions,
            revert_from=None,
        )

    async def execute_revert(self, context: TenantContext | None, *,
                             version: Any, idempotency_key: str):
        if isinstance(version, bool) or not isinstance(version, int):
            raise CrmCommandValidationError("version must be a positive int")
        if version < 1:
            raise CrmCommandValidationError("version must be a positive int")
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"revert_from": version},
            tone=None, instructions=None, revert_from=version,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"agent-prompts:{account_id}",),
        )
        if intent["revert_from"] is not None:
            await cur.execute(
                "SELECT tone, instructions FROM agent_prompts "
                "WHERE client_account_id = %s AND version = %s",
                (account_id, intent["revert_from"]),
            )
            target = await cur.fetchone()
            if target is None:
                raise CrmCommandDenied("prompt_version_not_found_in_scope")
            tone = target["tone"]
            instructions = target["instructions"] or ""
        else:
            tone = intent["tone"]
            instructions = intent["instructions"]
        await cur.execute(
            "SELECT max(version) AS top FROM agent_prompts "
            "WHERE client_account_id = %s",
            (account_id,),
        )
        top = (await cur.fetchone())["top"]
        version = int(top or 0) + 1
        prompt_id = str(uuid4())
        snapshot = compose_prompt(tone, instructions)
        await cur.execute(
            "UPDATE agent_prompts SET is_active = false "
            "WHERE client_account_id = %s AND is_active",
            (account_id,),
        )
        await cur.execute(
            """
            INSERT INTO agent_prompts
              (id, client_account_id, version, tone, instructions,
               composed_snapshot, is_active, demo_seed_key)
            VALUES (%s, %s, %s, %s, %s, %s, true, NULL)
            """,
            (prompt_id, account_id, version, tone, instructions, snapshot),
        )
        return {"resource_id": prompt_id, "version": version,
                "tone": tone, "instructions": instructions,
                "composed_snapshot": snapshot,
                "reverted_from": intent["revert_from"],
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"version": payload["version"],
                "reverted_from": payload["reverted_from"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"],
                "version": payload["version"]}


class CreateKbArticleCommand(CrmCommandBase):
    """Scoped KB insert, console-owned (`demo_seed_key NULL`)."""

    operation = "agent.kb.create"
    audit_resource_type = "kb_article"
    outbox_event_type = "KbArticleCreated"

    async def execute_create(self, context: TenantContext | None, *,
                             category: Any, question: Any, keywords: Any,
                             answer: Any, idempotency_key: str):
        clean = self._validate(category, question, keywords, answer)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input=clean, **clean,
        )

    @staticmethod
    def _validate(category: Any, question: Any, keywords: Any,
                  answer: Any) -> dict[str, Any]:
        if category not in KB_CATEGORIES:
            raise CrmCommandValidationError("category is invalid")
        return {"category": category,
                "question": _require_text(
                    question, "question", maximum=_QUESTION_MAX),
                "keywords": _require_keywords(keywords),
                "answer": _require_text(
                    answer, "answer", maximum=_ANSWER_MAX)}

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        article_id = str(uuid4())
        await cur.execute(
            """
            INSERT INTO knowledge_base_articles
              (id, client_account_id, demo_seed_key, category,
               question, keywords, answer)
            VALUES (%s, %s, NULL, %s, %s, %s::text[], %s)
            """,
            (article_id, account_id, intent["category"],
             intent["question"], intent["keywords"], intent["answer"]),
        )
        return {"resource_id": article_id, "category": intent["category"],
                "question": intent["question"],
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"category": payload["category"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"],
                "category": payload["category"]}


class UpdateKbArticleCommand(CrmCommandBase):
    """Scoped KB field update (at least one field; never touches seeds)."""

    operation = "agent.kb.update"
    audit_resource_type = "kb_article"
    outbox_event_type = "KbArticleUpdated"

    async def execute_update(self, context: TenantContext | None, *,
                             article_id: str, category: Any = None,
                             question: Any = None, keywords: Any = None,
                             answer: Any = None, idempotency_key: str = ""):
        if (category is None and question is None
                and keywords is None and answer is None):
            raise CrmCommandValidationError("nothing to update")
        clean: dict[str, Any] = {}
        if category is not None:
            if category not in KB_CATEGORIES:
                raise CrmCommandValidationError("category is invalid")
            clean["category"] = category
        if question is not None:
            clean["question"] = _require_text(
                question, "question", maximum=_QUESTION_MAX)
        if keywords is not None:
            clean["keywords"] = _require_keywords(keywords)
        if answer is not None:
            clean["answer"] = _require_text(
                answer, "answer", maximum=_ANSWER_MAX)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"article_id": article_id, **clean},
            article_id=article_id, **clean,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id FROM knowledge_base_articles "
            "WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["article_id"], account_id),
        )
        if await cur.fetchone() is None:
            # foreign and missing share one non-enumerating denial
            raise CrmCommandDenied("kb_article_not_found_in_scope")
        sets: list[str] = []
        params: list[Any] = []
        for column in ("category", "question", "keywords", "answer"):
            if column in intent and intent[column] is not None:
                if column == "keywords":
                    sets.append("keywords = %s::text[]")
                else:
                    sets.append(f"{column} = %s")
                params.append(intent[column])
        sets.append("updated_at = now()")
        params.extend([intent["article_id"], account_id])
        await cur.execute(
            f"UPDATE knowledge_base_articles SET {', '.join(sets)} "
            "WHERE id = %s AND client_account_id = %s",
            tuple(params),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("kb_article_update_failed")
        return {"resource_id": intent["article_id"],
                "correlation_id": context.correlation_id}


class DeactivateKbArticleCommand(CrmCommandBase):
    """Scoped KB soft-deactivate (sets `deactivated_at`; rows stay)."""

    operation = "agent.kb.deactivate"
    audit_resource_type = "kb_article"
    outbox_event_type = "KbArticleDeactivated"

    async def execute_deactivate(self, context: TenantContext | None, *,
                                 article_id: str, idempotency_key: str):
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"article_id": article_id},
            article_id=article_id,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id FROM knowledge_base_articles "
            "WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["article_id"], account_id),
        )
        if await cur.fetchone() is None:
            raise CrmCommandDenied("kb_article_not_found_in_scope")
        await cur.execute(
            "UPDATE knowledge_base_articles SET deactivated_at = now(), "
            "updated_at = now() WHERE id = %s AND client_account_id = %s",
            (intent["article_id"], account_id),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("kb_article_deactivate_failed")
        return {"resource_id": intent["article_id"],
                "correlation_id": context.correlation_id}


class UpdateCalendarConfigCommand(CrmCommandBase):
    """Upsert the per-account scheduling window (operation
    `agent.calendar.update`). Partial bodies merge over the stored row;
    invalid windows fail closed."""

    operation = "agent.calendar.update"
    audit_resource_type = "agent_calendar_config"
    outbox_event_type = "AgentCalendarConfigUpdated"

    async def execute_update(self, context: TenantContext | None, *,
                             window_start: Any = None, window_end: Any = None,
                             slot_minutes: Any = None,
                             working_days: Any = None,
                             timezone: Any = None,
                             destination_label: Any = "__absent__",
                             idempotency_key: str = ""):
        if (window_start is None and window_end is None
                and slot_minutes is None and working_days is None
                and timezone is None and destination_label == "__absent__"):
            raise CrmCommandValidationError("nothing to update")
        tz = _parse_timezone(timezone) if timezone is not None else None
        if (slot_minutes is not None
                and (isinstance(slot_minutes, bool)
                     or not isinstance(slot_minutes, int)
                     or slot_minutes <= 0)):
            raise CrmCommandValidationError("slot_minutes must be positive")
        if working_days is not None:
            if (not isinstance(working_days, list)
                    or any(isinstance(day, bool)
                           or not isinstance(day, int)
                           or day not in range(0, 7)
                           for day in working_days)):
                raise CrmCommandValidationError(
                    "working_days must be a list of 0-6")
        if (destination_label != "__absent__"
                and destination_label is not None):
            if (not isinstance(destination_label, str)
                    or not destination_label.strip()):
                raise CrmCommandValidationError(
                    "destination_label must be a non-empty string")
            if len(destination_label.strip()) > _LABEL_MAX:
                raise CrmCommandValidationError(
                    "destination_label exceeds 200 characters")
        if ((window_start is None) != (window_end is None)):
            raise CrmCommandValidationError(
                "window_start and window_end update together")
        window = None
        if window_start is not None:
            start = _parse_hour(window_start, "window_start")
            end = _parse_hour(window_end, "window_end")
            if not start < end:
                raise CrmCommandValidationError(
                    "window_start must be before window_end")
            window = (_format_window(start), _format_window(end))
        label = (destination_label.strip()
                 if isinstance(destination_label, str) else None)
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"window": window, "slot_minutes": slot_minutes,
                             "working_days": (sorted(working_days)
                                              if working_days is not None
                                              else None),
                             "timezone": tz,
                             "destination_label": label
                             if destination_label != "__absent__" else None,
                             "label_set": destination_label != "__absent__"},
            window=window, slot_minutes=slot_minutes,
            working_days=(sorted(working_days)
                          if working_days is not None else None),
            timezone=tz,
            destination_label=label,
            label_set=destination_label != "__absent__",
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT window_start, window_end, slot_minutes, working_days, "
            "timezone, destination_label FROM agent_calendar_config "
            "WHERE client_account_id = %s",
            (account_id,),
        )
        current = await cur.fetchone()
        window = intent["window"]
        if window is None and current is not None:
            window = (current["window_start"], current["window_end"])
        if window is None or window[0] is None or window[1] is None:
            raise CrmCommandValidationError(
                "window_start and window_end update together")
        slot = (intent["slot_minutes"] if intent["slot_minutes"] is not None
                else (current["slot_minutes"] if current else None))
        if slot is None:
            raise CrmCommandValidationError("slot_minutes is required")
        days = (intent["working_days"]
                if intent["working_days"] is not None
                else (list(current["working_days"])
                      if current and current["working_days"] is not None
                      else None))
        if days is None:
            raise CrmCommandValidationError("working_days is required")
        tz = (intent["timezone"] if intent["timezone"] is not None
              else (current["timezone"] if current else None))
        label = (intent["destination_label"] if intent["label_set"]
                 else (current["destination_label"] if current else None))
        await cur.execute(
            """
            INSERT INTO agent_calendar_config
              (id, client_account_id, window_start, window_end,
               slot_minutes, working_days, destination_label, timezone,
               demo_seed_key)
            VALUES (%s, %s, %s, %s, %s, %s::int[], %s, %s, NULL)
            ON CONFLICT (client_account_id) DO UPDATE SET
              window_start = EXCLUDED.window_start,
              window_end = EXCLUDED.window_end,
              slot_minutes = EXCLUDED.slot_minutes,
              working_days = EXCLUDED.working_days,
              destination_label = EXCLUDED.destination_label,
              timezone = EXCLUDED.timezone
            """,
            (str(uuid4()), account_id, window[0], window[1],
             slot, days, label, tz),
        )
        return {"resource_id": account_id,
                "window_start": window[0], "window_end": window[1],
                "slot_minutes": slot, "working_days": sorted(days),
                "destination_label": label, "timezone": tz,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"window_start": payload["window_start"],
                "window_end": payload["window_end"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"],
                "window_start": payload["window_start"],
                "window_end": payload["window_end"]}


class UpdateFieldAllowlistCommand(CrmCommandBase):
    """Replace the per-account lead-field allowlist (presence = allowed;
    operation `agent.allowlist.update`)."""

    operation = "agent.allowlist.update"
    audit_resource_type = "agent_field_allowlist"
    outbox_event_type = "AgentFieldAllowlistUpdated"

    async def execute_update(self, context: TenantContext | None, *,
                             allowed: Any, idempotency_key: str):
        if not isinstance(allowed, list):
            raise CrmCommandValidationError("allowed must be a list")
        for column in allowed:
            if column not in ALLOWLIST_COLUMNS:
                raise CrmCommandValidationError(
                    "allowed contains an unknown column")
        unique = sorted(set(allowed))
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"allowed": unique}, allowed=unique,
        )

    async def _apply(self, context: TenantContext, cur: Any,
                     intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "DELETE FROM agent_field_allowlist WHERE client_account_id = %s",
            (account_id,),
        )
        for column in intent["allowed"]:
            await cur.execute(
                """
                INSERT INTO agent_field_allowlist
                  (id, client_account_id, column_name, demo_seed_key)
                VALUES (%s, %s, %s, NULL)
                """,
                (str(uuid4()), account_id, column),
            )
        return {"resource_id": account_id, "allowed": list(intent["allowed"]),
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"allowed": payload["allowed"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"],
                "allowed": payload["allowed"]}

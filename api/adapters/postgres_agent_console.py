"""Agent Console read path (change: agent-console, WU16).

Pure per-request loaders over the 009 console tables; no cache (WU13
"correctness > latency" precedent), account predicate first, missing rows
fall back to the code constants so pre-009 bases behave exactly as today:

- settings/matrix absent -> (AUTO_ACTION_TYPES, AUTO_EVENT_CREATION, True).
- allowlist absent -> {"stage_id", "status"}: the legacy behavior. Deny-all
  would be the "safe" default in the abstract, but it would break the
  accepted transition path (TransitionLeadStageCommand._apply writes
  stage_id AND status together), so the honest fallback is today's
  hardcoded effect, not an empty set.
- calendar config absent (or any NULL column) -> BUSINESS_* constants.
- active prompt absent -> build_system_prompt() defaults.

No commands, no routes, no writes live here.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from api.adapters.postgres_commercial_crm import _require
from api.adapters.postgres_core import PostgresRunner
from api.adapters.postgres_knowledge_base import normalize_tokens
from api.application.agent_persona import AGENT_TONE, compose_prompt
from api.application.agent_policy import (
    AUTO_ACTION_TYPES,
    AUTO_EVENT_CREATION,
    HANDOFF_ACTION,
)
from api.domain.calendar_event import (
    BUSINESS_DAYS,
    BUSINESS_END_HOUR,
    BUSINESS_START_HOUR,
    BUSINESS_TIMEZONE,
    SLOT_MINUTES,
)

# The 7 CHECK-domain action names of agent_policy_matrix: the AUTO whitelist
# plus the mechanics-only handoff type. Derived from code constants only.
ALL_CONSOLE_ACTION_TYPES = frozenset(AUTO_ACTION_TYPES) | {HANDOFF_ACTION}

# Legacy lead-write effect (see module docstring): missing allowlist rows
# mean "pre-console base", which wrote stage_id + status together.
LEGACY_FIELD_ALLOWLIST = frozenset({"stage_id", "status"})


class ConsoleSettings(NamedTuple):
    auto_types: frozenset  # effective AUTO whitelist after the overlay
    calendar_allowed: bool  # managed AUTO_EVENT_CREATION
    global_auto: bool  # master switch; True suppresses nothing
    matrix: dict  # action_type -> "auto" | "escalate" (all 7, fallback-filled)


class CalendarConfig(NamedTuple):
    tz_name: str
    start_hour: int
    end_hour: int
    slot_minutes: int
    working_days: tuple
    destination_label: str | None


def default_calendar_config() -> CalendarConfig:
    """Module-constant calendar config (== bare business_slot_starts)."""
    return CalendarConfig(
        tz_name=BUSINESS_TIMEZONE,
        start_hour=BUSINESS_START_HOUR,
        end_hour=BUSINESS_END_HOUR,
        slot_minutes=SLOT_MINUTES,
        working_days=tuple(BUSINESS_DAYS),
        destination_label=None,
    )


def default_verdict(action_type: str) -> str:
    """Code-constant verdict for one action type (missing matrix row)."""
    return "auto" if action_type in AUTO_ACTION_TYPES else "escalate"


async def load_console_settings(context, runner: PostgresRunner) -> ConsoleSettings:
    """Effective policy overlay for the context account (single SELECTs)."""
    account_id = _require(context)
    setting_rows = await runner.query(
        "SELECT global_auto, calendar_auto_enabled FROM agent_settings "
        "WHERE client_account_id = %s",
        (account_id,),
    )
    if setting_rows:
        global_auto = bool(setting_rows[0]["global_auto"])
        calendar_allowed = bool(setting_rows[0]["calendar_auto_enabled"])
    else:
        global_auto = True
        calendar_allowed = bool(AUTO_EVENT_CREATION)
    matrix_rows = await runner.query(
        "SELECT action_type, verdict FROM agent_policy_matrix "
        "WHERE client_account_id = %s",
        (account_id,),
    )
    matrix = {action: default_verdict(action) for action in sorted(ALL_CONSOLE_ACTION_TYPES)}
    for row in matrix_rows:
        if row["action_type"] in matrix and row["verdict"] in ("auto", "escalate"):
            matrix[row["action_type"]] = row["verdict"]
    auto_types = frozenset(action for action, verdict in matrix.items() if verdict == "auto")
    return ConsoleSettings(
        auto_types=auto_types,
        calendar_allowed=calendar_allowed,
        global_auto=global_auto,
        matrix=matrix,
    )


async def load_field_allowlist(context, runner: PostgresRunner) -> frozenset:
    """Allowed lead columns for the context account (presence = allowed).

    Returns fixed lead columns (stage_id/status/selected_property_id) plus the
    names of any defined custom fields referenced via the 010
    `field_definition_id` FK. A pre-010 base (no `field_definition_id` column,
    no `custom_field_definitions` table) re-reads the bare 009 columns.
    """
    account_id = _require(context)
    try:
        rows = await runner.query(
            "SELECT a.column_name, d.name AS custom_name "
            "FROM agent_field_allowlist a "
            "LEFT JOIN custom_field_definitions d ON d.id = a.field_definition_id "
            "AND d.client_account_id = a.client_account_id "
            "WHERE a.client_account_id = %s",
            (account_id,),
        )
    except Exception:
        rows = await runner.query(
            "SELECT column_name FROM agent_field_allowlist "
            "WHERE client_account_id = %s",
            (account_id,),
        )
    if not rows:
        return frozenset(LEGACY_FIELD_ALLOWLIST)
    names: set = set()
    for row in rows:
        name = row.get("custom_name") or row.get("column_name")
        if name:
            names.add(name)
    return frozenset(names)


async def load_signal_bindings(context, runner: PostgresRunner) -> dict:
    """Signal -> field-definition bindings for the context account.

    Returns {signal_key: field_definition_id}. A pre-011 base (no
    `agent_signal_bindings` table) yields {} — the tolerant read mirrors the
    calendar-config pattern (missing surface behaves as today: no bindings,
    so the agent emits no `set_custom_field_value`).
    """
    account_id = _require(context)
    try:
        rows = await runner.query(
            "SELECT signal_key, field_definition_id FROM agent_signal_bindings "
            "WHERE client_account_id = %s",
            (account_id,),
        )
    except Exception:
        return {}
    bindings: dict = {}
    for row in rows:
        if row.get("signal_key"):
            bindings[row["signal_key"]] = str(row["field_definition_id"])
    return bindings


def _parse_window_hour(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip().split(":")[0])
        except (ValueError, IndexError):
            return default
    return default


async def load_calendar_config(context, runner: PostgresRunner) -> CalendarConfig:
    """Scheduling window for the context account (missing row -> constants).

    The 010 `timezone` column is read when present; NULL/absent falls back to
    the BUSINESS_TIMEZONE constant. A pre-010 base (no `timezone` column) is
    re-read over the 009 columns so the window still resolves with the constant
    timezone.
    """
    account_id = _require(context)
    try:
        rows = await runner.query(
            "SELECT window_start, window_end, slot_minutes, working_days, "
            "timezone, destination_label FROM agent_calendar_config "
            "WHERE client_account_id = %s",
            (account_id,),
        )
    except Exception:
        rows = await runner.query(
            "SELECT window_start, window_end, slot_minutes, working_days, "
            "destination_label FROM agent_calendar_config "
            "WHERE client_account_id = %s",
            (account_id,),
        )
    if not rows:
        return default_calendar_config()
    row = rows[0]
    slot = row["slot_minutes"]
    days = row["working_days"]
    return CalendarConfig(
        tz_name=row.get("timezone") or BUSINESS_TIMEZONE,
        start_hour=_parse_window_hour(row.get("window_start"), BUSINESS_START_HOUR),
        end_hour=_parse_window_hour(row.get("window_end"), BUSINESS_END_HOUR),
        slot_minutes=slot if isinstance(slot, int) and not isinstance(slot, bool) and slot > 0 else SLOT_MINUTES,
        working_days=tuple(days) if isinstance(days, (list, tuple)) and days else tuple(BUSINESS_DAYS),
        destination_label=row.get("destination_label"),
    )


async def preview_prompt(context, runner: PostgresRunner) -> str:
    """Exact system-prompt text the model would receive for this account."""
    account_id = _require(context)
    rows = await runner.query(
        "SELECT tone, instructions FROM agent_prompts "
        "WHERE client_account_id = %s AND is_active ORDER BY version DESC LIMIT 1",
        (account_id,),
    )
    if not rows:
        return compose_prompt(AGENT_TONE, "")
    return compose_prompt(rows[0]["tone"], rows[0]["instructions"] or "")


def score_question(article_keywords: Any, query_tokens: Any) -> int:
    """Pure overlap score mirroring KB_SCORING_SQL for the self-test display.

    Set-intersection size over normalized tokens on both sides (INTERSECT
    dedupes, cardinality counts), exactly like the SQL ranking.
    """
    keywords: set[str] = set()
    for raw in article_keywords or ():
        keywords.update(normalize_tokens(str(raw)))
    return len(keywords & set(query_tokens or ()))

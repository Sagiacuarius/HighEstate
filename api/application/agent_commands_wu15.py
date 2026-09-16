"""Autonomous effect commands (change: agent-autonomy, WU15).

On the accepted `CrmCommandBase` template (canonical hash, reserve -> apply ->
audit + outbox -> one transaction; replay returns the stored result):

- PersistOutboundReplyCommand persists ONE grounded outbound `messages` row
  for a `send_reply` action. The conversation is locked and its `agent_mode`
  re-checked at INSERT time: a pause that raced in fails closed (denied, no
  row). No direction migration — the 004 CHECK already allows `outbound`.
- SetConversationAgentModeCommand is the ONLY writer of
  `conversations.agent_mode` (handoff pause, dev toggle, resume). Only legal
  transitions apply; anything else fails closed.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from api.application.crm_commands_wu5 import (
    CrmCommandBase,
    CrmCommandDenied,
    CrmCommandValidationError,
)
from api.tenant_boundary import TenantContext

REPLY_SOURCES = frozenset({"kb_article", "price_catalog"})

AGENT_MODES = frozenset({"manual", "auto", "paused"})

# Legal per-conversation transitions: manual<->auto is the dev surface,
# auto->paused is the handoff command only, paused->auto|manual is resume.
LEGAL_MODE_TRANSITIONS = {
    "manual": frozenset({"auto"}),
    "auto": frozenset({"manual", "paused"}),
    "paused": frozenset({"auto", "manual"}),
}


class PersistOutboundReplyCommand(CrmCommandBase):
    """Persist one grounded outbound reply (operation `agent.reply`)."""

    operation = "agent.reply"
    audit_resource_type = "message"
    outbox_event_type = "AgentReplyPersisted"

    async def execute_reply(self, context: TenantContext | None, *,
                            conversation_id: str, source: str, body: str,
                            article_id: str | None = None,
                            property_id: str | None = None,
                            external_event_id: str,
                            idempotency_key: str):
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise CrmCommandValidationError("conversation_id must be a non-empty string")
        if source not in REPLY_SOURCES:
            raise CrmCommandValidationError("source must be kb_article or price_catalog")
        if not isinstance(body, str) or not body.strip() or len(body) > 2000:
            raise CrmCommandValidationError("body must be a non-empty string (max 2000)")
        if not isinstance(external_event_id, str) or not external_event_id.strip():
            raise CrmCommandValidationError("external_event_id must be a non-empty string")
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"conversation_id": conversation_id, "source": source,
                             "article_id": article_id, "property_id": property_id,
                             "body": body, "external_event_id": external_event_id},
            conversation_id=conversation_id, source=source, body=body,
            article_id=article_id, property_id=property_id,
            external_event_id=external_event_id,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id, contact_id, agent_mode FROM conversations "
            "WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["conversation_id"], account_id),
        )
        conversation = await cur.fetchone()
        if conversation is None:
            raise CrmCommandDenied("conversation_not_found_in_scope")
        # Agent-mode race guard: a handoff pause that landed between analyze
        # and dispatch fails the reply closed at INSERT time.
        if conversation["agent_mode"] == "paused":
            raise CrmCommandDenied("conversation_paused")
        message_id = str(uuid4())
        await cur.execute(
            """
            INSERT INTO messages
              (id, client_account_id, conversation_id, demo_seed_key,
               direction, body, external_event_id)
            VALUES (%s, %s, %s, NULL, 'outbound', %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (message_id, account_id, intent["conversation_id"],
             intent["body"], intent["external_event_id"]),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("message_already_exists")
        await cur.execute(
            "UPDATE conversations SET updated_at = now() "
            "WHERE id = %s AND client_account_id = %s",
            (intent["conversation_id"], account_id),
        )
        return {"resource_id": message_id,
                "conversation_id": intent["conversation_id"],
                "contact_id": str(conversation["contact_id"]),
                "direction": "outbound", "source": intent["source"],
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"direction": "outbound", "source": payload["source"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"],
                "conversation_id": payload["conversation_id"],
                "direction": "outbound"}


class SetConversationAgentModeCommand(CrmCommandBase):
    """Scoped agent_mode write (operation `agent.mode`)."""

    operation = "agent.mode"
    audit_resource_type = "conversation"
    outbox_event_type = "AgentModeChanged"

    async def execute_mode(self, context: TenantContext | None, *,
                           conversation_id: str, mode: str,
                           idempotency_key: str):
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise CrmCommandValidationError("conversation_id must be a non-empty string")
        if mode not in AGENT_MODES:
            raise CrmCommandValidationError("mode must be manual, auto or paused")
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"conversation_id": conversation_id, "mode": mode},
            conversation_id=conversation_id, mode=mode,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id, agent_mode FROM conversations "
            "WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["conversation_id"], account_id),
        )
        conversation = await cur.fetchone()
        if conversation is None:
            raise CrmCommandDenied("conversation_not_found_in_scope")
        current = conversation["agent_mode"]
        target = intent["mode"]
        if target == current:
            return {"resource_id": intent["conversation_id"],
                    "conversation_id": intent["conversation_id"],
                    "previous_mode": current, "agent_mode": current,
                    "transition": "none",
                    "correlation_id": context.correlation_id}
        if target not in LEGAL_MODE_TRANSITIONS.get(current, frozenset()):
            raise CrmCommandDenied("agent_mode_transition_illegal", {"from": current})
        await cur.execute(
            "UPDATE conversations SET agent_mode = %s, updated_at = now() "
            "WHERE id = %s AND client_account_id = %s",
            (target, intent["conversation_id"], account_id),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("agent_mode_update_failed")
        return {"resource_id": intent["conversation_id"],
                "conversation_id": intent["conversation_id"],
                "previous_mode": current, "agent_mode": target,
                "transition": f"{current}->{target}",
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"previous_mode": payload["previous_mode"],
                "agent_mode": payload["agent_mode"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"],
                "previous_mode": payload["previous_mode"],
                "agent_mode": payload["agent_mode"]}

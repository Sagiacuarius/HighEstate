"""Atomic agent-draft commands (change: llm-conversational-agent, WU14).

Same accepted machinery as WU5/WU11/WU13 — canonical hash, reserve -> apply ->
audit + outbox -> one transaction; replay returns the stored canonical result.

- AnalyzeInboundMessageCommand persists a schema-validated draft PLAN produced
  by the ConversationAgent (the model call happens BEFORE the command, so no
  network I/O ever runs inside a DB transaction; the command carries the plan
  as already-validated intent).
- DecideAgentDraftCommand is the atomic proposed -> approved|rejected decision
  (lock + monotonic status check inside the transaction).
- FinalizeAgentDraftCommand records the aggregate execution_result and moves
  approved -> executed after the router's per-action dispatch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from api.application.crm_commands_wu5 import (
    CrmCommandBase,
    CrmCommandDenied,
    CrmCommandValidationError,
)
from api.domain.agent_draft import INTENTS
from api.tenant_boundary import TenantContext

# OD-2 attribution: deterministic sentinel actor written on policy (auto)
# decisions; human rows keep decided_by and leave this NULL.
AGENT_POLICY_ACTOR = str(uuid5(NAMESPACE_URL, "agent-policy"))


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, separators=(",", ":"), sort_keys=True)


def _iso(value: datetime | None) -> str | None:
    return (value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z") if value else None)


class AnalyzeInboundMessageCommand(CrmCommandBase):
    """Persist one proposed draft from a validated agent plan."""

    operation = "agent.analyze"
    audit_resource_type = "agent_draft"
    outbox_event_type = "AgentDraftProposed"

    async def execute_analyze(self, context: TenantContext | None, *,
                              body: str, conversation_id: str, contact_id: str,
                              plan: dict[str, Any], idempotency_key: str,
                              message_id: str | None = None,
                              lead_id: str | None = None):
        if not isinstance(body, str) or not body.strip():
            raise CrmCommandValidationError("body must be a non-empty string")
        if plan.get("intent") not in INTENTS:
            raise CrmCommandValidationError("plan intent is not a valid intent")
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"body": body.strip(), "conversation_id": conversation_id,
                             "contact_id": contact_id},
            body=body.strip(), conversation_id=conversation_id, contact_id=contact_id,
            message_id=message_id, lead_id=lead_id, plan=plan,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        plan = intent["plan"]
        # server-side scope re-validation, independent of the router
        await cur.execute(
            "SELECT id, contact_id FROM conversations "
            "WHERE id = %s AND client_account_id = %s",
            (intent["conversation_id"], account_id),
        )
        conversation = await cur.fetchone()
        if conversation is None:
            raise CrmCommandDenied("conversation_not_found_in_scope")
        if str(conversation["contact_id"]) != intent["contact_id"]:
            raise CrmCommandDenied("conversation_contact_mismatch")
        await cur.execute(
            "SELECT 1 FROM contacts WHERE id = %s AND client_account_id = %s",
            (intent["contact_id"], account_id),
        )
        if await cur.fetchone() is None:
            raise CrmCommandDenied("contact_not_found_in_scope")
        verdict = (plan.get("policy") or {}).get("verdict")
        policy_decision = verdict if verdict in ("auto", "escalate") else "escalate"
        draft_id = str(uuid4())
        await cur.execute(
            """
            INSERT INTO agent_drafts
              (id, client_account_id, conversation_id, message_id, contact_id, lead_id,
               status, intent, criteria, matched_property_ids, proposed_actions,
               reply_text, model_provider, model_name,
               policy_decision, execution_mode, auto_decision_actor)
            VALUES (%s, %s, %s, %s, %s, %s, 'proposed', %s, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s, %s, %s,
                    %s, 'human', NULL)
            """,
            (draft_id, account_id, intent["conversation_id"], intent["message_id"],
             intent["contact_id"], intent["lead_id"], plan["intent"],
             _json(plan["criteria"]), _json(plan["matched_property_ids"]),
             _json(plan["proposed_actions"]), plan["reply_text"],
             plan["model_provider"], plan["model_name"], policy_decision),
        )
        await cur.execute(
            "SELECT created_at FROM agent_drafts WHERE id = %s", (draft_id,))
        created = await cur.fetchone()
        return {"resource_id": draft_id, "draft_id": draft_id, "status": "proposed",
                "intent": plan["intent"], "criteria": plan["criteria"],
                "matched_property_ids": plan["matched_property_ids"],
                "proposed_actions": plan["proposed_actions"],
                "reply_text": plan["reply_text"],
                "model_provider": plan["model_provider"], "model_name": plan["model_name"],
                "policy_decision": policy_decision, "execution_mode": "human",
                "auto_decision_actor": None,
                "conversation_id": intent["conversation_id"],
                "contact_id": intent["contact_id"], "lead_id": intent["lead_id"],
                "created_at": _iso(created["created_at"]),
                "decided_at": None, "execution_result": None,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"draft_status": "proposed", "intent": payload["intent"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"], "intent": payload["intent"],
                "conversation_id": payload["conversation_id"]}


class DecideAgentDraftCommand(CrmCommandBase):
    """Atomic proposed -> approved|auto|rejected decision.

    "approve"/"reject" is the human override (decided_by = user,
    execution_mode stays 'human'). "auto" is the policy decision (status
    'auto', decided_by NULL, execution_mode 'agent_policy', sentinel actor).
    Any non-proposed source status fails closed.
    """

    operation = "agent.decide"
    audit_resource_type = "agent_draft"
    outbox_event_type = "AgentDraftDecided"

    async def execute_decision(self, context: TenantContext | None, *, draft_id: str,
                               decision: str, idempotency_key: str):
        if decision not in ("approve", "reject", "auto"):
            raise CrmCommandValidationError("decision must be approve, reject or auto")
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"draft_id": draft_id, "decision": decision},
            draft_id=draft_id, decision=decision,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id, status, intent, proposed_actions, conversation_id, contact_id "
            "FROM agent_drafts WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["draft_id"], account_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise CrmCommandDenied("draft_not_found_in_scope")
        if row["status"] != "proposed":
            raise CrmCommandDenied("draft_not_proposed", {"status": row["status"]})
        is_auto = intent["decision"] == "auto"
        new_status = ("auto" if is_auto
                      else "approved" if intent["decision"] == "approve" else "rejected")
        await cur.execute(
            "UPDATE agent_drafts SET status = %s, decided_at = now(), decided_by = %s, "
            "execution_mode = %s, auto_decision_actor = %s "
            "WHERE id = %s AND client_account_id = %s",
            (new_status,
             None if is_auto else context.user.id,
             "agent_policy" if is_auto else "human",
             AGENT_POLICY_ACTOR if is_auto else None,
             intent["draft_id"], account_id),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("draft_decision_failed")
        actions = row["proposed_actions"]
        if isinstance(actions, str):
            actions = json.loads(actions)
        return {"resource_id": intent["draft_id"], "status": new_status,
                "decision": intent["decision"], "intent": row["intent"],
                "proposed_actions": list(actions or []),
                "execution_mode": "agent_policy" if is_auto else "human",
                "auto_decision_actor": AGENT_POLICY_ACTOR if is_auto else None,
                "conversation_id": str(row["conversation_id"]),
                "contact_id": str(row["contact_id"]),
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"draft_status": payload["status"], "decision": payload["decision"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"], "status": payload["status"]}


class FinalizeAgentDraftCommand(CrmCommandBase):
    """Record the saga's aggregate outcome and move approved|auto -> executed."""

    operation = "agent.execute"
    audit_resource_type = "agent_draft"
    outbox_event_type = "AgentDraftExecuted"

    async def execute_finalize(self, context: TenantContext | None, *, draft_id: str,
                               execution_result: list[dict[str, Any]], idempotency_key: str):
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"draft_id": draft_id, "result": execution_result},
            draft_id=draft_id, execution_result=execution_result,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id, status FROM agent_drafts "
            "WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["draft_id"], account_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise CrmCommandDenied("draft_not_found_in_scope")
        if row["status"] not in ("approved", "auto"):
            raise CrmCommandDenied("draft_not_approved", {"status": row["status"]})
        await cur.execute(
            "UPDATE agent_drafts SET status = 'executed', execution_result = %s::jsonb "
            "WHERE id = %s AND client_account_id = %s",
            (_json(list(intent["execution_result"])), intent["draft_id"], account_id),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("draft_finalize_failed")
        return {"resource_id": intent["draft_id"], "status": "executed",
                "execution_result": list(intent["execution_result"]),
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        outcomes = [entry.get("outcome") for entry in payload["execution_result"]]
        return {"draft_status": "executed", "outcomes": outcomes}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"], "status": "executed"}

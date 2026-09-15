"""MVP Commercial Demo WU5: atomic CRM commands over real PostgreSQL.

Adapts the accepted technical WU5/WU6 principles (transaction, canonical hash,
idempotency reserve/replay/conflict, audit, outbox) to CRM entities. The
tenant_resource commands are NOT called directly; this is a new CRM surface
over the same command_idempotency/audit_records/outbox_events seams.

Every command: authorize context -> reserve(actor,operation,key,hash) ->
scoped CRM writes -> one audit record + one pending outbox event -> stored
result payload -> commit, all inside a single transaction. Replay returns the
canonical stored result with zero extra effects; a hash conflict rolls back
with zero effects; denials roll the command back and then record one separate
redacted denial audit (WU6 pattern: reason class + correlation only).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from api.adapters.postgres_audit_outbox import PostgresAuditOutboxRepository
from api.adapters.postgres_core import CrossTenantAccessDenied
from api.adapters.postgres_idempotency import IdempotencyConflict
from api.application.audit_outbox_ports import AuditRecord, OutboxEvent
from api.commands.crm_commands import (
    InboundCrmEffects,
    LeadRef,
    LeadSummary,
    SaveFollowUp,
    canonical_hash,
)
from api.domain.commercial_crm import FollowUp
from api.inbound_boundary import NormalizedInboundEvent
from api.tenant_boundary import TenantContext

WRITE_PERMISSION = "client_account:write"
LEAD_STATUSES = frozenset({"new", "qualified", "won", "lost"})


class CrmCommandValidationError(ValueError):
    """Raised before any SQL when command inputs violate a pure rule."""


class CrmCommandDenied(Exception):
    """Denied command outcome; carries the redacted audit metadata."""

    def __init__(self, reason: str, metadata: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.metadata = metadata or {}


@dataclass(frozen=True)
class CrmCommandOutcome:
    payload: dict[str, Any]
    replayed: bool
    correlation_id: str
    idempotency_key: str


def _json(value: Any) -> str:
    import json
    return json.dumps(value if value is not None else {}, separators=(",", ":"), sort_keys=True)


def _parse_iso(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CrmCommandValidationError(f"{label} must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CrmCommandValidationError(f"{label} is not a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise CrmCommandValidationError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _stable_uuid(namespace: str, digest: str) -> UUID:
    return UUID(hex=UUID(namespace).hex[:12] + digest[:20])


class CrmCommandBase:
    """Shared atomic execution template: reserve -> act -> audit/outbox -> commit.

    Subclasses implement _apply with plain psycopg on the SAME cursor as the
    idempotency reservation; a single commit makes mutation, audit, outbox and
    result payload durable together or not at all.
    """

    operation = ""
    audit_resource_type = "crm"
    outbox_event_type = ""

    def __init__(self, conn_kwargs: dict[str, Any]):
        self._conn_kwargs = conn_kwargs
        self._audit_outbox = PostgresAuditOutboxRepository()

    async def execute(
        self,
        context: TenantContext | None,
        *,
        idempotency_key: str,
        canonical_input: dict[str, Any],
        fail_before_commit: bool = False,
        **intent: Any,
    ) -> CrmCommandOutcome:
        request_hash = canonical_hash(canonical_input)
        try:
            self._validate_context(context)
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise CrmCommandValidationError("idempotency_key is required")
        except (CrossTenantAccessDenied, CrmCommandValidationError) as error:
            await self._record_denial(context, self.operation, error)
            raise
        assert context is not None

        conn = await psycopg.AsyncConnection.connect(
            **self._conn_kwargs, autocommit=False, row_factory=dict_row
        )
        try:
            async with conn.cursor() as cur:
                # reservation (unique actor+operation+key; DO NOTHING on clash)
                await cur.execute(
                    """
                    INSERT INTO command_idempotency
                      (id, actor_id, operation, idempotency_key, request_hash, status)
                    VALUES (%s, %s, %s, %s, %s, 'processing')
                    ON CONFLICT (actor_id, operation, idempotency_key) DO NOTHING
                    RETURNING id
                    """,
                    (str(uuid4()), context.user.id, self.operation, idempotency_key, request_hash),
                )
                inserted = await cur.fetchone()
                if inserted is None:
                    await cur.execute(
                        """
                        SELECT request_hash, result_payload, status
                        FROM command_idempotency
                        WHERE actor_id = %s AND operation = %s AND idempotency_key = %s
                        FOR UPDATE
                        """,
                        (context.user.id, self.operation, idempotency_key),
                    )
                    existing = await cur.fetchone()
                    if existing is None or existing["request_hash"] != request_hash:
                        # same key, different hash: conflict, zero effects
                        await conn.rollback()
                        raise IdempotencyConflict()
                    if existing["status"] != "completed" or existing["result_payload"] is None:
                        # still processing or crashed reservation: not replayable yet
                        await conn.rollback()
                        raise IdempotencyConflict()
                    await conn.commit()
                    return CrmCommandOutcome(
                        payload=dict(existing["result_payload"]), replayed=True,
                        correlation_id=context.correlation_id, idempotency_key=idempotency_key,
                    )

                payload = await self._apply(context, cur, intent)

                await cur.execute(
                    """
                    UPDATE command_idempotency
                    SET result_payload = %s::jsonb, status = 'completed', updated_at = now()
                    WHERE actor_id = %s AND operation = %s AND idempotency_key = %s
                    """,
                    (_json(payload), context.user.id, self.operation, idempotency_key),
                )
                await self._audit_outbox.append(
                    AuditRecord(
                        id=str(uuid4()), actor_id=context.user.id, agency_id=context.agency.id,
                        client_account_id=context.client_account.id,
                        action=f"crm.{self.operation}",
                        resource_type=self.audit_resource_type,
                        resource_id=self._resource_id(payload), outcome="success",
                        correlation_id=context.correlation_id, idempotency_key=idempotency_key,
                        metadata={"operation": self.operation, **self._audit_extra(payload)},
                    ), cur)
                await self._audit_outbox.append_outbox(
                    OutboxEvent(
                        id=str(uuid4()), event_type=self.outbox_event_type,
                        aggregate_type=self.audit_resource_type,
                        aggregate_id=self._resource_id(payload) or "unknown",
                        agency_id=context.agency.id, client_account_id=context.client_account.id,
                        correlation_id=context.correlation_id, idempotency_key=idempotency_key,
                        payload=self._outbox_payload(payload),
                    ), cur)
                if fail_before_commit:
                    raise RuntimeError("forced failure before commit")
                await conn.commit()
                return CrmCommandOutcome(
                    payload=payload, replayed=False,
                    correlation_id=context.correlation_id, idempotency_key=idempotency_key,
                )
        except CrmCommandDenied as error:
            # authorization/integrity failure: rollback everything, then one
            # redacted denial audit in its own transaction.
            if not conn.closed:
                await conn.rollback()
            await self._record_denial(context, self.operation, error)
            raise
        except BaseException:
            if not conn.closed:
                await conn.rollback()
            raise
        finally:
            await conn.close()

    # subclass hooks ------------------------------------------------------
    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _resource_id(payload: dict[str, Any]) -> str | None:
        return payload.get("resource_id")

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {}

    @staticmethod
    def _outbox_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload.get("resource_id")}

    # shared helpers ------------------------------------------------------
    @staticmethod
    def _validate_context(context: TenantContext | None) -> None:
        if context is None:
            raise CrossTenantAccessDenied("tenant context is required")
        if (context.membership.user_id != context.user.id
                or context.membership.agency_id != context.agency.id
                or context.membership.client_account_id != context.client_account.id):
            raise CrossTenantAccessDenied("tenant context is inconsistent")
        if not context.membership.allows(WRITE_PERMISSION):
            raise CrossTenantAccessDenied("membership lacks write permission")

    async def _record_denial(self, context: TenantContext | None, operation: str,
                             error: Exception) -> None:
        """One separate redacted denial audit; safe reason class only, no ids."""
        if context is None or not context.user.id or not context.agency.id:
            return
        account_id: str | None = None
        if (context.membership.user_id == context.user.id
                and context.membership.agency_id == context.agency.id
                and context.membership.client_account_id == context.client_account.id):
            account_id = context.client_account.id
        reason = error.reason if isinstance(error, CrmCommandDenied) else type(error).__name__
        extra = error.metadata if isinstance(error, CrmCommandDenied) else {}
        conn = await psycopg.AsyncConnection.connect(
            **self._conn_kwargs, autocommit=False, row_factory=dict_row
        )
        try:
            async with conn.cursor() as cur:
                await self._audit_outbox.append(
                    AuditRecord(
                        id=str(uuid4()), actor_id=context.user.id, agency_id=context.agency.id,
                        client_account_id=account_id, action=f"crm.{operation}",
                        resource_type=self.audit_resource_type, resource_id=None,
                        outcome="denied", correlation_id=context.correlation_id,
                        idempotency_key=None, metadata={"reason": reason, **extra},
                    ), cur)
            await conn.commit()
        finally:
            await conn.close()

    async def _lock_conversation(self, cur: Any, conversation_id: str, account_id: str) -> dict[str, Any]:
        """Serialize commands per conversation; foreign/missing are one denial."""
        await cur.execute(
            "SELECT id, contact_id FROM conversations WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (conversation_id, account_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise CrmCommandDenied("conversation_not_found_in_scope")
        return row

    @staticmethod
    async def _lead_by_contact(cur: Any, account_id: str, contact_id: str) -> dict[str, Any] | None:
        await cur.execute(
            """
            SELECT id, contact_id, pipeline_id, stage_id, status, selected_property_id, source
            FROM leads WHERE client_account_id = %s AND contact_id = %s
            ORDER BY created_at, id LIMIT 1
            """,
            (account_id, contact_id),
        )
        return await cur.fetchone()

    @staticmethod
    async def _ordered_stage_ids(cur: Any, account_id: str, pipeline_id: str) -> tuple[str, ...]:
        await cur.execute(
            """
            SELECT id FROM pipeline_stages
            WHERE client_account_id = %s AND pipeline_id = %s
            ORDER BY position, id
            """,
            (account_id, pipeline_id),
        )
        return tuple(str(row["id"]) for row in await cur.fetchall())


class ProcessInboundMessageCommand(CrmCommandBase):
    """Atomic inbound command: persist message, create/update lead, deterministic
    pipeline advance, follow-up, audit, outbox, result payload — one transaction."""

    operation = "inbound.process"
    audit_resource_type = "lead"
    outbox_event_type = "InboundMessageProcessed"

    def __init__(self, conn_kwargs: dict[str, Any], effects: InboundCrmEffects | None = None):
        super().__init__(conn_kwargs)
        self._effects = effects or InboundCrmEffects()

    async def execute_event(self, context: TenantContext | None, *, event: NormalizedInboundEvent,
                            idempotency_key: str, follow_up_due_at: str | None = None,
                            fail_before_commit: bool = False) -> CrmCommandOutcome:
        canonical_input = {
            "event_id": event.event_id,
            "conversation_id": event.conversation_id,
            "message_id": event.message_id,
            "direction": event.direction,
            "body": event.body,
            "received_at": event.received_at,
            "follow_up_due_at": follow_up_due_at,
        }
        return await self.execute(
            context, idempotency_key=idempotency_key, canonical_input=canonical_input,
            fail_before_commit=fail_before_commit,
            event=event, follow_up_due_at=follow_up_due_at,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        event: NormalizedInboundEvent = intent["event"]
        account_id = context.client_account.id
        # authorization independent of parser/route: event scope == context scope
        if event.client_account_id != account_id:
            raise CrmCommandDenied("event_scope_does_not_match_context")

        conversation = await self._lock_conversation(cur, event.conversation_id, account_id)
        contact_id = str(conversation["contact_id"])

        # message persisted; external_event_id unique index backstops redelivery
        try:
            await cur.execute(
                """
                INSERT INTO messages (id, client_account_id, conversation_id, direction,
                                      body, external_event_id, created_at)
                VALUES (%s, %s, %s, 'inbound', %s, %s, %s)
                """,
                (event.message_id, account_id, event.conversation_id, event.body,
                 event.event_id, _parse_iso(event.received_at, "received_at")),
            )
        except psycopg.errors.UniqueViolation:
            raise CrmCommandDenied("message_event_already_processed") from None
        await cur.execute(
            "UPDATE conversations SET unread_count = unread_count + 1, updated_at = now() "
            "WHERE id = %s AND client_account_id = %s",
            (event.conversation_id, account_id),
        )

        lead = await self._lead_by_contact(cur, account_id, contact_id)
        lead_created = lead is None
        if lead_created:
            stage = await self._first_stage(cur, account_id)
            lead_id = str(uuid4())
            await cur.execute(
                """
                INSERT INTO leads (id, client_account_id, contact_id, pipeline_id, stage_id, status, source)
                VALUES (%s, %s, %s, %s, %s, 'new', 'simulator')
                """,
                (lead_id, account_id, contact_id, str(stage["pipeline_id"]), str(stage["id"])),
            )
            lead = {"id": lead_id, "contact_id": contact_id,
                    "pipeline_id": str(stage["pipeline_id"]), "stage_id": str(stage["id"]),
                    "status": "new"}

        # deterministic stage advance (provider-neutral effects port)
        ordered_stages = await self._ordered_stage_ids(cur, account_id, str(lead["pipeline_id"]))
        transition = self._effects.stage_transition(
            LeadRef(lead_id=str(lead["id"]), pipeline_id=str(lead["pipeline_id"]),
                    current_stage_id=str(lead["stage_id"]), status=str(lead["status"])),
            ordered_stages)
        previous_stage_id = str(lead["stage_id"])
        current_stage_id = previous_stage_id
        if transition is not None:
            await cur.execute(
                "UPDATE leads SET stage_id = %s, status = %s, updated_at = now() "
                "WHERE id = %s AND client_account_id = %s",
                (transition.to_stage_id, transition.new_status, str(lead["id"]), account_id),
            )
            if cur.rowcount != 1:
                raise CrmCommandDenied("stage_transition_not_allowed")
            current_stage_id = transition.to_stage_id

        # follow-up effect (optional by design: only when the flow declares one)
        spec = self._effects.follow_up(LeadSummary(lead_id=str(lead["id"]),
                                                   contact_id=contact_id, status=str(lead["status"])))
        due_at = _parse_iso(intent.get("follow_up_due_at") or spec.due_at, "follow_up_due_at")
        follow_up_hash = canonical_hash({"event_id": event.event_id, "lead_id": str(lead["id"])})
        follow_up_id = str(_stable_uuid(str(lead["id"]), follow_up_hash))
        await cur.execute(
            """
            INSERT INTO follow_ups (id, client_account_id, lead_id, contact_id, title, due_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (follow_up_id, account_id, str(lead["id"]), contact_id, spec.title, due_at, spec.status),
        )

        return {
            "resource_id": str(lead["id"]),
            "lead_created": lead_created,
            "contact_id": contact_id,
            "conversation_id": event.conversation_id,
            "event_id": event.event_id,
            "message_id": event.message_id,
            "previous_stage_id": previous_stage_id,
            "current_stage_id": current_stage_id,
            "follow_up_id": follow_up_id,
            "received_at": event.received_at,
            "correlation_id": context.correlation_id,
        }

    @staticmethod
    async def _first_stage(cur: Any, account_id: str) -> dict[str, Any]:
        await cur.execute(
            """
            SELECT s.id, s.pipeline_id FROM pipeline_stages s
            JOIN pipelines p ON p.id = s.pipeline_id AND p.client_account_id = s.client_account_id
            WHERE s.client_account_id = %s
            ORDER BY p.created_at, s.position, s.id LIMIT 1
            """,
            (account_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise CrmCommandDenied("no_pipeline_stage_available")
        return row

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"lead_created": payload["lead_created"],
                "previous_stage_id": payload["previous_stage_id"],
                "current_stage_id": payload["current_stage_id"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"], "event_id": payload["event_id"],
                "conversation_id": payload["conversation_id"],
                "lead_created": payload["lead_created"]}


class TransitionLeadStageCommand(CrmCommandBase):
    """Explicit atomic lead transition (own operation, audit and outbox)."""

    operation = "lead.transition"
    audit_resource_type = "lead"
    outbox_event_type = "LeadStageChanged"

    async def execute_transition(self, context: TenantContext | None, *, lead_id: str,
                                 to_stage_id: str, new_status: str = "qualified",
                                 idempotency_key: str) -> CrmCommandOutcome:
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"lead_id": lead_id, "to_stage_id": to_stage_id,
                             "new_status": new_status},
            lead_id=lead_id, to_stage_id=to_stage_id, new_status=new_status,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        new_status = intent["new_status"]
        if new_status not in LEAD_STATUSES:
            raise CrmCommandValidationError("lead status is invalid")
        await cur.execute(
            "SELECT id, stage_id, pipeline_id FROM leads WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["lead_id"], account_id),
        )
        lead = await cur.fetchone()
        if lead is None:
            # foreign and missing share one non-enumerating denial
            raise CrmCommandDenied("lead_not_found_in_scope")
        await cur.execute(
            "SELECT id, pipeline_id FROM pipeline_stages WHERE id = %s AND client_account_id = %s",
            (intent["to_stage_id"], account_id),
        )
        stage = await cur.fetchone()
        if stage is None or str(stage["pipeline_id"]) != str(lead["pipeline_id"]):
            raise CrmCommandDenied("stage_not_available_in_scope")
        previous = str(lead["stage_id"])
        await cur.execute(
            "UPDATE leads SET stage_id = %s, status = %s, updated_at = now() "
            "WHERE id = %s AND client_account_id = %s",
            (intent["to_stage_id"], new_status, intent["lead_id"], account_id),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("stage_transition_not_allowed")
        return {"resource_id": intent["lead_id"], "previous_stage_id": previous,
                "current_stage_id": intent["to_stage_id"], "status": new_status,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"previous_stage_id": payload["previous_stage_id"],
                "current_stage_id": payload["current_stage_id"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"],
                "previous_stage_id": payload["previous_stage_id"],
                "current_stage_id": payload["current_stage_id"],
                "status": payload["status"]}


class CreateFollowUpCommand(CrmCommandBase):
    """Atomic follow-up creation; the domain FollowUp enforces the WU1<->WU2
    resolved invariant (nullable lead/contact columns + 'at least one target'
    CHECK from migration 004)."""

    operation = "follow_up.create"
    audit_resource_type = "follow_up"
    outbox_event_type = "FollowUpCreated"

    async def execute_follow_up(self, context: TenantContext | None, *, follow_up: FollowUp,
                                idempotency_key: str) -> CrmCommandOutcome:
        canonical_input = {
            "lead_id": follow_up.lead_id, "contact_id": follow_up.contact_id,
            "title": follow_up.title, "status": follow_up.status,
            "due_at": follow_up.due_at.astimezone(timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
        return await self.execute(
            context, idempotency_key=idempotency_key, canonical_input=canonical_input,
            follow_up_id=follow_up.id, account_follow_up=follow_up,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        follow_up: FollowUp = intent["account_follow_up"]
        if follow_up.client_account_id != account_id:
            raise CrmCommandDenied("entity_scope_does_not_match_context")
        await cur.execute(
            """
            INSERT INTO follow_ups (id, client_account_id, lead_id, contact_id, title, due_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (follow_up.id, account_id, follow_up.lead_id, follow_up.contact_id,
             follow_up.title, follow_up.due_at, follow_up.status),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("follow_up_already_exists")
        return {"resource_id": follow_up.id, "lead_id": follow_up.lead_id,
                "contact_id": follow_up.contact_id, "status": follow_up.status,
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": payload["status"]}

    def _outbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"resource_id": payload["resource_id"], "lead_id": payload["lead_id"],
                "status": payload["status"]}


class CompleteFollowUpCommand(CrmCommandBase):
    operation = "follow_up.complete"
    audit_resource_type = "follow_up"
    outbox_event_type = "FollowUpCompleted"

    async def execute_completion(self, context: TenantContext | None, *, follow_up_id: str,
                                 idempotency_key: str) -> CrmCommandOutcome:
        return await self.execute(
            context, idempotency_key=idempotency_key,
            canonical_input={"follow_up_id": follow_up_id},
            follow_up_id=follow_up_id,
        )

    async def _apply(self, context: TenantContext, cur: Any, intent: dict[str, Any]) -> dict[str, Any]:
        account_id = context.client_account.id
        await cur.execute(
            "SELECT id, status FROM follow_ups WHERE id = %s AND client_account_id = %s FOR UPDATE",
            (intent["follow_up_id"], account_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise CrmCommandDenied("follow_up_not_found_in_scope")
        if row["status"] != "pending":
            raise CrmCommandDenied("follow_up_not_pending", {"status": row["status"]})
        await cur.execute(
            "UPDATE follow_ups SET status = 'completed', updated_at = now() "
            "WHERE id = %s AND client_account_id = %s",
            (intent["follow_up_id"], account_id),
        )
        if cur.rowcount != 1:
            raise CrmCommandDenied("follow_up_completion_failed")
        return {"resource_id": intent["follow_up_id"], "status": "completed",
                "correlation_id": context.correlation_id}

    @staticmethod
    def _audit_extra(payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": payload["status"]}

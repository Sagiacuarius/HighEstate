"""PostgreSQL CRM repositories (MVP Commercial Demo WU3), psycopg3, no ORM.

Implements the WU1 port boundary (src/application/commercial-demo.ts) against
the migration-004 tables. Isolation rules, adapted from the accepted WU4
tenant-isolation pattern:

- every operation requires a server-derived TenantContext; absent or
  internally inconsistent contexts fail before any SQL runs;
- the SQL predicate always includes client_account_id = context account;
- referenced entity ids (contact/property/pipeline/stage/conversation/lead)
  are never authorities: same-account integrity is enforced by the composite
  FKs from migration 004, so a cross-account reference fails at the database
  and is translated into a safe non-enumerating error;
- foreign resources are indistinguishable from missing ones;
- writes additionally require the write permission from the membership.

The repositories can read the deterministic demo seed for Accounts A and B,
but they never touch demo_seed_key: seeded rows are only mutated through
explicit save/delete calls made with the owning account context.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg

from api.adapters.postgres_core import CrossTenantAccessDenied, PostgresRunner
from api.domain.commercial_crm import (
    Contact,
    Conversation,
    FollowUp,
    Lead,
    Message,
    Pipeline,
    Property,
    Stage,
)
from api.tenant_boundary import TenantContext


class CrmNotFoundError(Exception):
    """Missing-in-scope and foreign-in-scope are one safe outcome."""


class CrmRelationshipViolation(Exception):
    """A composite-FK failure surfaced as a safe, non-enumerating error."""


class CrmConflictError(Exception):
    """Uniqueness clash inside the calling account's own scope."""


def _require(context: TenantContext | None, *, write: bool = False) -> str:
    if context is None:
        raise CrossTenantAccessDenied("tenant context is required")
    account_id = context.client_account.id
    if not account_id:
        raise CrossTenantAccessDenied("tenant context has no client account")
    if (
        context.client_account.agency_id != context.agency.id
        or context.membership.client_account_id != account_id
        or context.membership.agency_id != context.agency.id
    ):
        raise CrossTenantAccessDenied("tenant context membership is inconsistent")
    if write and not context.membership.allows("client_account:write"):
        raise CrossTenantAccessDenied("membership lacks write permission")
    return account_id


def _translate(error: psycopg.Error) -> Exception:
    sqlstate = error.sqlstate or ""
    if sqlstate == "23503":  # foreign_key_violation (composite cross-account FK)
        return CrmRelationshipViolation("relationship crosses account scope")
    if sqlstate == "23505":  # unique_violation
        return CrmConflictError("resource already exists in this account")
    return error


def _contact(row: dict[str, Any]) -> Contact:
    return Contact(
        id=str(row["id"]),
        client_account_id=str(row["client_account_id"]),
        name=row["name"],
        email=row["email"],
        phone=row["phone"],
        tags=tuple(row["tags"] or ()),
        status=row["status"],
    )


def _lead(row: dict[str, Any]) -> Lead:
    return Lead(
        id=str(row["id"]),
        client_account_id=str(row["client_account_id"]),
        contact_id=str(row["contact_id"]),
        pipeline_id=str(row["pipeline_id"]),
        stage_id=str(row["stage_id"]),
        status=row["status"],
        selected_property_id=str(row["selected_property_id"]) if row["selected_property_id"] else None,
        source=row["source"],
    )


def _property(row: dict[str, Any]) -> Property:
    return Property(
        id=str(row["id"]),
        client_account_id=str(row["client_account_id"]),
        title=row["title"],
        operation_type=row["operation_type"],
        location=row["location"],
        bedrooms=row["bedrooms"],
        price=row["price"],
        currency=row["currency"],
        availability=row["availability"],
    )


def _stage(row: dict[str, Any]) -> Stage:
    return Stage(
        id=str(row["id"]),
        pipeline_id=str(row["pipeline_id"]),
        client_account_id=str(row["client_account_id"]),
        name=row["name"],
        position=row["position"],
    )


def _conversation(row: dict[str, Any]) -> Conversation:
    return Conversation(
        id=str(row["id"]),
        client_account_id=str(row["client_account_id"]),
        contact_id=str(row["contact_id"]),
        unread_count=row["unread_count"],
        status=row["status"],
    )


def _message(row: dict[str, Any]) -> Message:
    return Message(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        client_account_id=str(row["client_account_id"]),
        direction=row["direction"],
        body=row["body"],
        created_at=row["created_at"],
    )


def _follow_up(row: dict[str, Any]) -> FollowUp:
    return FollowUp(
        id=str(row["id"]),
        client_account_id=str(row["client_account_id"]),
        title=row["title"],
        due_at=row["due_at"],
        lead_id=str(row["lead_id"]) if row["lead_id"] else None,
        contact_id=str(row["contact_id"]) if row["contact_id"] else None,
        status=row["status"],
    )


class PostgresContactLeadRepository:
    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def list_contacts(self, context: TenantContext) -> list[Contact]:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM contacts WHERE client_account_id = %s ORDER BY created_at, id",
            (account_id,),
        )
        return [_contact(r) for r in rows]

    async def get_contact(self, context: TenantContext, contact_id: str) -> Contact | None:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM contacts WHERE id = %s AND client_account_id = %s",
            (contact_id, account_id),
        )
        return _contact(rows[0]) if rows else None

    async def save_contact(self, context: TenantContext, contact: Contact) -> Contact:
        account_id = _require(context, write=True)
        if contact.client_account_id != account_id:
            raise CrossTenantAccessDenied("entity scope does not match context")
        try:
            await self._runner.execute(
                """
                INSERT INTO contacts (id, client_account_id, name, email, phone, tags, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name, email = EXCLUDED.email, phone = EXCLUDED.phone,
                    tags = EXCLUDED.tags, status = EXCLUDED.status, updated_at = now()
                WHERE contacts.client_account_id = EXCLUDED.client_account_id
                """,
                (contact.id, account_id, contact.name, contact.email, contact.phone,
                 list(contact.tags), contact.status),
            )
        except psycopg.Error as error:
            raise _translate(error) from error
        saved = await self.get_contact(context, contact.id)
        if saved is None:
            raise CrmNotFoundError("contact is not available in this account scope")
        return saved

    async def delete_contact(self, context: TenantContext, contact_id: str) -> bool:
        account_id = _require(context, write=True)
        affected = await self._runner.execute(
            "DELETE FROM contacts WHERE id = %s AND client_account_id = %s",
            (contact_id, account_id),
        )
        return affected == 1

    async def list_leads(self, context: TenantContext) -> list[Lead]:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM leads WHERE client_account_id = %s ORDER BY created_at, id",
            (account_id,),
        )
        return [_lead(r) for r in rows]

    async def get_lead(self, context: TenantContext, lead_id: str) -> Lead | None:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM leads WHERE id = %s AND client_account_id = %s",
            (lead_id, account_id),
        )
        return _lead(rows[0]) if rows else None

    async def save_lead(self, context: TenantContext, lead: Lead) -> Lead:
        account_id = _require(context, write=True)
        if lead.client_account_id != account_id:
            raise CrossTenantAccessDenied("entity scope does not match context")
        try:
            await self._runner.execute(
                """
                INSERT INTO leads (id, client_account_id, contact_id, selected_property_id,
                                   pipeline_id, stage_id, status, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET contact_id = EXCLUDED.contact_id,
                    selected_property_id = EXCLUDED.selected_property_id,
                    pipeline_id = EXCLUDED.pipeline_id, stage_id = EXCLUDED.stage_id,
                    status = EXCLUDED.status, source = EXCLUDED.source, updated_at = now()
                WHERE leads.client_account_id = EXCLUDED.client_account_id
                """,
                (lead.id, account_id, lead.contact_id, lead.selected_property_id,
                 lead.pipeline_id, lead.stage_id, lead.status, lead.source),
            )
        except psycopg.Error as error:
            raise _translate(error) from error
        saved = await self.get_lead(context, lead.id)
        if saved is None:
            raise CrmNotFoundError("lead is not available in this account scope")
        return saved

    async def delete_lead(self, context: TenantContext, lead_id: str) -> bool:
        account_id = _require(context, write=True)
        affected = await self._runner.execute(
            "DELETE FROM leads WHERE id = %s AND client_account_id = %s",
            (lead_id, account_id),
        )
        return affected == 1


class PostgresPropertyRepository:
    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def list(self, context: TenantContext) -> list[Property]:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM properties WHERE client_account_id = %s ORDER BY created_at, id",
            (account_id,),
        )
        return [_property(r) for r in rows]

    async def get(self, context: TenantContext, property_id: str) -> Property | None:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM properties WHERE id = %s AND client_account_id = %s",
            (property_id, account_id),
        )
        return _property(rows[0]) if rows else None

    async def save(self, context: TenantContext, property: Property) -> Property:
        account_id = _require(context, write=True)
        if property.client_account_id != account_id:
            raise CrossTenantAccessDenied("entity scope does not match context")
        try:
            await self._runner.execute(
                """
                INSERT INTO properties (id, client_account_id, title, operation_type, location,
                                        bedrooms, price, currency, availability)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET title = EXCLUDED.title, operation_type = EXCLUDED.operation_type,
                    location = EXCLUDED.location, bedrooms = EXCLUDED.bedrooms,
                    price = EXCLUDED.price, currency = EXCLUDED.currency,
                    availability = EXCLUDED.availability, updated_at = now()
                WHERE properties.client_account_id = EXCLUDED.client_account_id
                """,
                (property.id, account_id, property.title, property.operation_type,
                 property.location, property.bedrooms, property.price, property.currency,
                 property.availability),
            )
        except psycopg.Error as error:
            raise _translate(error) from error
        saved = await self.get(context, property.id)
        if saved is None:
            raise CrmNotFoundError("property is not available in this account scope")
        return saved

    async def delete(self, context: TenantContext, property_id: str) -> bool:
        account_id = _require(context, write=True)
        affected = await self._runner.execute(
            "DELETE FROM properties WHERE id = %s AND client_account_id = %s",
            (property_id, account_id),
        )
        return affected == 1


class PostgresPipelineRepository:
    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def get(self, context: TenantContext, pipeline_id: str) -> Pipeline | None:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM pipelines WHERE id = %s AND client_account_id = %s",
            (pipeline_id, account_id),
        )
        if not rows:
            return None
        stage_rows = await self._runner.query(
            """
            SELECT * FROM pipeline_stages
            WHERE pipeline_id = %s AND client_account_id = %s
            ORDER BY position, id
            """,
            (pipeline_id, account_id),
        )
        head = rows[0]
        return Pipeline(
            id=str(head["id"]),
            client_account_id=str(head["client_account_id"]),
            name=head["name"],
            stages=tuple(_stage(r) for r in stage_rows),
        )

    async def list(self, context: TenantContext) -> list[Pipeline]:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM pipelines WHERE client_account_id = %s ORDER BY created_at, id",
            (account_id,),
        )
        out: list[Pipeline] = []
        for head in rows:
            full = await self.get(context, str(head["id"]))
            assert full is not None
            out.append(full)
        return out

    async def transition_lead(
        self, context: TenantContext, lead_id: str, to_stage_id: str
    ) -> tuple[Lead, str]:
        """Moves a lead to another stage of its own pipeline. Returns
        (lead, previous_stage_id). Foreign stages fail closed via composite FK."""
        account_id = _require(context, write=True)
        lead = await _get_lead_row(self._runner, account_id, lead_id)
        if lead is None:
            raise CrmNotFoundError("lead is not available in this account scope")
        pipeline = await self.get(context, str(lead["pipeline_id"]))
        if pipeline is None or all(stage.id != to_stage_id for stage in pipeline.stages):
            # A stage of another pipeline or account is indistinguishable from absent.
            raise CrmNotFoundError("stage is not available in this account scope")
        previous = str(lead["stage_id"])
        if previous == to_stage_id:
            return _lead(lead), previous
        try:
            affected = await self._runner.execute(
                """
                UPDATE leads SET stage_id = %s, updated_at = now()
                WHERE id = %s AND client_account_id = %s
                """,
                (to_stage_id, lead_id, account_id),
            )
        except psycopg.Error as error:
            raise _translate(error) from error
        if affected != 1:
            raise CrmNotFoundError("lead is not available in this account scope")
        updated = await _get_lead_row(self._runner, account_id, lead_id)
        assert updated is not None
        return _lead(updated), previous


class PostgresConversationRepository:
    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def list(self, context: TenantContext) -> list[Conversation]:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM conversations WHERE client_account_id = %s ORDER BY updated_at, id",
            (account_id,),
        )
        return [_conversation(r) for r in rows]

    async def get(self, context: TenantContext, conversation_id: str) -> Conversation | None:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM conversations WHERE id = %s AND client_account_id = %s",
            (conversation_id, account_id),
        )
        return _conversation(rows[0]) if rows else None

    async def get_messages(self, context: TenantContext, conversation_id: str) -> list[Message]:
        account_id = _require(context)
        conversation = await self.get(context, conversation_id)
        if conversation is None:
            raise CrmNotFoundError("conversation is not available in this account scope")
        rows = await self._runner.query(
            """
            SELECT * FROM messages
            WHERE conversation_id = %s AND client_account_id = %s
            ORDER BY created_at, id
            """,
            (conversation_id, account_id),
        )
        return [_message(r) for r in rows]

    async def mark_read(self, context: TenantContext, conversation_id: str) -> Conversation | None:
        account_id = _require(context, write=True)
        affected = await self._runner.execute(
            """
            UPDATE conversations SET unread_count = 0, updated_at = now()
            WHERE id = %s AND client_account_id = %s
            """,
            (conversation_id, account_id),
        )
        if affected == 0:
            return None
        return await self.get(context, conversation_id)


class PostgresFollowUpRepository:
    def __init__(self, runner: PostgresRunner):
        self._runner = runner

    async def list_due(self, context: TenantContext, at: datetime) -> list[FollowUp]:
        account_id = _require(context)
        rows = await self._runner.query(
            """
            SELECT * FROM follow_ups
            WHERE client_account_id = %s AND status = 'pending' AND due_at <= %s
            ORDER BY due_at, id
            """,
            (account_id, at),
        )
        return [_follow_up(r) for r in rows]

    async def list_all(self, context: TenantContext) -> list[FollowUp]:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM follow_ups WHERE client_account_id = %s ORDER BY due_at, id",
            (account_id,),
        )
        return [_follow_up(r) for r in rows]

    async def get(self, context: TenantContext, follow_up_id: str) -> FollowUp | None:
        account_id = _require(context)
        rows = await self._runner.query(
            "SELECT * FROM follow_ups WHERE id = %s AND client_account_id = %s",
            (follow_up_id, account_id),
        )
        return _follow_up(rows[0]) if rows else None

    async def save(self, context: TenantContext, follow_up: FollowUp) -> FollowUp:
        account_id = _require(context, write=True)
        if follow_up.client_account_id != account_id:
            raise CrossTenantAccessDenied("entity scope does not match context")
        try:
            await self._runner.execute(
                """
                INSERT INTO follow_ups (id, client_account_id, lead_id, contact_id, title, due_at, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET lead_id = EXCLUDED.lead_id, contact_id = EXCLUDED.contact_id,
                    title = EXCLUDED.title, due_at = EXCLUDED.due_at,
                    status = EXCLUDED.status, updated_at = now()
                WHERE follow_ups.client_account_id = EXCLUDED.client_account_id
                """,
                (follow_up.id, account_id, follow_up.lead_id, follow_up.contact_id,
                 follow_up.title, follow_up.due_at, follow_up.status),
            )
        except psycopg.Error as error:
            raise _translate(error) from error
        saved = await self.get(context, follow_up.id)
        if saved is None:
            raise CrmNotFoundError("follow-up is not available in this account scope")
        return saved

    async def complete(self, context: TenantContext, follow_up_id: str) -> FollowUp | None:
        account_id = _require(context, write=True)
        affected = await self._runner.execute(
            """
            UPDATE follow_ups SET status = 'completed', updated_at = now()
            WHERE id = %s AND client_account_id = %s AND status = 'pending'
            """,
            (follow_up_id, account_id),
        )
        if affected == 0:
            return None
        return await self.get(context, follow_up_id)


async def _get_lead_row(runner: PostgresRunner, account_id: str, lead_id: str) -> dict[str, Any] | None:
    rows = await runner.query(
        "SELECT * FROM leads WHERE id = %s AND client_account_id = %s",
        (lead_id, account_id),
    )
    return rows[0] if rows else None

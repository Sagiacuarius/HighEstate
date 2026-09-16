import { describe, expect, it } from "vitest";
import {
  CORRELATION_ID_MAX_LENGTH,
  correlationId,
  correlationIdOrGenerate,
  idempotencyKey,
  makeCorrelationId,
  optionalIdempotencyKey,
  parseAgentDraftCommandResponse,
  parseAgentDraftsResponse,
  parseAgentDraftResponse,
  parseAgentStateResponse,
  parseAvailabilityResponse,
  parseCalendarCommandResponse,
  parseCalendarEventsResponse,
  parseCrmAuditResponse,
  parseErrorEnvelope,
  parseLivenessResponse,
  parseReadinessResponse,
} from "./runtime-v1";

describe("runtime v1 contracts", () => {
  it("validates correlation and idempotency values with shared limits", () => {
    expect(correlationId("corr-1")).toBe("corr-1");
    expect(idempotencyKey("idem-1")).toBe("idem-1");
    expect(() => correlationId("")).toThrow();
    expect(() => correlationId("x".repeat(CORRELATION_ID_MAX_LENGTH + 1))).toThrow();
    expect(correlationIdOrGenerate(undefined).startsWith("auto-")).toBe(true);
    expect(optionalIdempotencyKey(undefined)).toBeUndefined();
    // Never generates an idempotency key implicitly.
    expect(optionalIdempotencyKey("   ")).toBeUndefined();
  });

  it("rejects header-unsafe values", () => {
    expect(() => correlationId("bad\nvalue")).toThrow();
    expect(() => idempotencyKey("bad\tvalue")).toThrow();
  });

  it("generates valid correlation IDs when missing", () => {
    const generated = makeCorrelationId();
    expect(generated.length).toBeLessThanOrEqual(CORRELATION_ID_MAX_LENGTH);
    expect(() => correlationId(generated)).not.toThrow();
  });

  it("parses the stable error envelope", () => {
    const parsed = parseErrorEnvelope({
      error: { code: "invalid_config", message: "Invalid configuration", correlation_id: "corr-2" },
    });
    expect(parsed.error.code).toBe("invalid_config");
    expect(parsed.error.correlation_id).toBe("corr-2");
    expect(() => parseErrorEnvelope({ error: null })).toThrow();
    expect(() => parseErrorEnvelope({ error: {} })).toThrow();
    expect(() => parseErrorEnvelope(null)).toThrow();
  });

  it("validates liveness and readiness payloads structurally", () => {
    const live = parseLivenessResponse({
      status: "ok",
      service: "production-runtime-api",
      contract_version: "v1",
      correlation_id: "corr-3",
    });
    expect(live.correlation_id).toBe("corr-3");

    const ready = parseReadinessResponse({
      status: "ok",
      service: "production-runtime-api",
      contract_version: "v1",
      correlation_id: "corr-4",
      dependencies: {},
    });
    expect(ready.dependencies).toEqual({});

    expect(() =>
      parseLivenessResponse({ status: "ok", service: "x", contract_version: "v9", correlation_id: "c" }),
    ).toThrow();
    expect(() =>
      parseReadinessResponse({ status: "ok", service: "x", contract_version: "v1", correlation_id: "c", dependencies: { db: "broken" } }),
    ).toThrow();
  });
});

/* --- WU12 audit screen contract mirror --- */

function auditPayload(itemOverrides: Record<string, unknown> = {},
                      rootOverrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    account_id: "acct-1",
    as_of: "2026-09-15T06:00:00.000000Z",
    correlation_id: "corr-a",
    items: [
      {
        audit_id: "8f000000-0000-0000-0000-000000000001",
        action: "crm.property.create",
        outcome: "success",
        resource_type: "property",
        resource_id: "6d100000-0000-0000-0000-0000000000a1",
        actor_id: "10000000-0000-0000-0000-000000000001",
        correlation_id: "corr-item",
        idempotency_key: null,
        reason: null,
        created_at: "2026-09-15T06:00:00.000000Z",
        ...itemOverrides,
      },
    ],
    count: 1,
    total: 1,
    limit: 25,
    has_more: false,
    next_cursor: null,
    filters: {
      contact_id: null, property_id: null, user_id: null,
      action: null, from: null, to: null,
    },
    ...rootOverrides,
  };
}

describe("parseCrmAuditResponse (WU12)", () => {
  it("accepts the server shape and preserves cursor fields", () => {
    const parsed = parseCrmAuditResponse(auditPayload({},
      { has_more: true, next_cursor: "eyJ4IjoxfQ" }));
    expect(parsed.items[0].action).toBe("crm.property.create");
    expect(parsed.has_more).toBe(true);
    expect(parsed.next_cursor).toBe("eyJ4IjoxfQ");
    expect(parsed.filters).toEqual(auditPayload().filters);
  });

  it("rejects an item carrying a metadata key — hard redaction failure", () => {
    expect(() => parseCrmAuditResponse(auditPayload({ metadata: { reason: "x" } })))
      .toThrow(/metadata must never be exposed/);
  });

  it("rejects unknown outcome enums and missing required fields", () => {
    expect(() => parseCrmAuditResponse(auditPayload({ outcome: "succeeded" }))).toThrow();
    expect(() => parseCrmAuditResponse(auditPayload({ outcome: null }))).toThrow();
    expect(() => parseCrmAuditResponse(auditPayload({}, { items: undefined }))).toThrow();
    expect(() => parseCrmAuditResponse(auditPayload({}, { has_more: "yes" }))).toThrow();
    expect(() => parseCrmAuditResponse(auditPayload({}, { filters: undefined }))).toThrow();
    expect(() => parseCrmAuditResponse(null)).toThrow();
  });
});

function calendarPayload(itemOverrides: Record<string, unknown> = {},
  rootOverrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    account_id: "acct-1",
    as_of: "2026-09-15T06:00:00.000000Z",
    correlation_id: "corr-c",
    items: [
      {
        event_id: "c1000000-0000-0000-0000-000000000001",
        title: "Visita · Palermo",
        description: null,
        starts_at: "2026-09-15T14:00:00.000Z",
        ends_at: "2026-09-15T15:00:00.000Z",
        contact_id: null,
        lead_id: null,
        property_id: null,
        status: "scheduled",
        source: "manual",
        notes: null,
        created_at: "2026-09-15T06:00:00.000Z",
        updated_at: "2026-09-15T06:00:00.000Z",
        ...itemOverrides,
      },
    ],
    count: 1,
    from: "2026-09-15T00:00:00.000Z",
    to: "2026-09-15T23:59:59.000Z",
    include_cancelled: false,
    ...rootOverrides,
  };
}

describe("calendar contracts (WU13)", () => {
  it("accepts the server shape for events and availability", () => {
    const parsed = parseCalendarEventsResponse(calendarPayload());
    expect(parsed.items[0].event_id).toBe("c1000000-0000-0000-0000-000000000001");
    expect(parsed.items[0].status).toBe("scheduled");
    expect(parsed.count).toBe(1);
    const avail = parseAvailabilityResponse({
      account_id: "acct-1",
      as_of: "2026-09-15T06:00:00.000000Z",
      correlation_id: "corr-c",
      items: [{ starts_at: "2026-09-14T12:00:00.000Z", ends_at: "2026-09-14T13:00:00.000Z" }],
      count: 1,
      from: "2026-09-14T00:00:00.000Z",
      to: "2026-09-14T23:59:59.000Z",
    });
    expect(avail.items[0].starts_at).toBe("2026-09-14T12:00:00.000Z");
  });

  it("rejects invalid status/source enums and missing required fields", () => {
    expect(() => parseCalendarEventsResponse(calendarPayload({ status: "tentative" }))).toThrow();
    expect(() => parseCalendarEventsResponse(calendarPayload({ source: "bot" }))).toThrow();
    expect(() => parseCalendarEventsResponse(calendarPayload({}, { items: undefined }))).toThrow();
    expect(() => parseCalendarEventsResponse(null)).toThrow();
  });

  it("accepts the calendar command envelope and rejects wrong environment", () => {
    const parsed = parseCalendarCommandResponse({
      contract_version: "v1",
      environment: "dev-only-calendar",
      accepted: true,
      replayed: false,
      correlation_id: "corr-c",
      result: {
        resource_id: "c1000000-0000-0000-0000-000000000001",
        status: "scheduled",
        starts_at: "2026-09-15T14:00:00.000Z",
        ends_at: "2026-09-15T15:00:00.000Z",
        correlation_id: "corr-c",
      },
    });
    expect(parsed.result.resource_id).toBe("c1000000-0000-0000-0000-000000000001");
    expect(() => parseCalendarCommandResponse({
      contract_version: "v1",
      environment: "dev-only-simulator",
      accepted: true,
      replayed: false,
      correlation_id: "corr-c",
      result: { resource_id: "x", status: "scheduled", correlation_id: "corr-c" },
    })).toThrow();
  });
});

function draftFixture(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    draft_id: "d1000000-0000-0000-0000-000000000001",
    status: "proposed",
    intent: "schedule_visit",
    criteria: { preferred_date: "2026-09-22", preferred_time: "10:00" },
    matched_property_ids: [],
    proposed_actions: [
      { type: "create_event", params: { title: "Visita", starts_at: "2026-09-22T13:00:00.000Z", ends_at: "2026-09-22T14:00:00.000Z", contact_id: "61000000-0000-0000-0000-000000000001" } },
      { type: "create_follow_up", params: { title: "Preparar visita", due_at: "2026-09-16T06:00:00.000Z", contact_id: "61000000-0000-0000-0000-000000000001" } },
    ],
    reply_text: "Coordinamos la visita.",
    model_provider: "stub",
    model_name: "stub-keyword-v1",
    policy_decision: "escalate",
    execution_mode: "human",
    conversation_id: "66000000-0000-0000-0000-000000000001",
    contact_id: "61000000-0000-0000-0000-000000000001",
    lead_id: null,
    created_at: "2026-09-15T06:00:00.000Z",
    decided_at: null,
    execution_result: null,
    ...overrides,
  };
}

describe("conversational agent contracts (WU14)", () => {
  it("parses the command envelope, list, and single-draft responses", () => {
    const cmd = parseAgentDraftCommandResponse({
      contract_version: "v1",
      environment: "dev-only-agent",
      accepted: true,
      replayed: false,
      correlation_id: "corr-a",
      draft: draftFixture(),
    });
    expect(cmd.draft.draft_id).toBe("d1000000-0000-0000-0000-000000000001");
    expect(cmd.draft.status).toBe("proposed");
    expect(cmd.draft.intent).toBe("schedule_visit");
    expect(cmd.draft.proposed_actions[0].type).toBe("create_event");

    const list = parseAgentDraftsResponse({
      account_id: "acct-1",
      as_of: "2026-09-15T06:00:00.000000Z",
      correlation_id: "corr-l",
      count: 1,
      items: [draftFixture()],
    });
    expect(list.count).toBe(1);
    expect(list.items[0].draft_id).toBe("d1000000-0000-0000-0000-000000000001");

    const one = parseAgentDraftResponse({
      account_id: "acct-1",
      as_of: "2026-09-15T06:00:00.000000Z",
      correlation_id: "corr-s",
      draft: draftFixture(),
    });
    expect(one.draft.intent).toBe("schedule_visit");
  });

  it("rejects unknown intent/action type, wrong environment, and malformed drafts", () => {
    expect(() => parseAgentDraftCommandResponse({
      contract_version: "v1",
      environment: "dev-only-calendar",
      accepted: true,
      replayed: false,
      correlation_id: "corr-a",
      draft: draftFixture(),
    })).toThrow();
    expect(() => parseAgentDraftsResponse({
      account_id: "a", as_of: "x", correlation_id: "c", count: 1,
      items: [draftFixture({ intent: "hack_the_planet" })],
    })).toThrow();
    expect(() => parseAgentDraftsResponse({
      account_id: "a", as_of: "x", correlation_id: "c", count: 1,
      items: [draftFixture({ proposed_actions: [{ type: "self_destruct", params: {} }] })],
    })).toThrow();
    expect(() => parseAgentDraftsResponse({
      account_id: "a", as_of: "x", correlation_id: "c", count: 1,
      items: [draftFixture({ status: "unknown" })],
    })).toThrow();
  });
});

function agentStateFixture(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    contract_version: "v1",
    environment: "dev-only-agent",
    conversation_id: "66000000-0000-0000-0000-000000000001",
    agent_mode: "auto",
    handoff_open: false,
    account_id: "acct-1",
    correlation_id: "corr-s",
    ...overrides,
  };
}

describe("agent autonomy contracts (WU15)", () => {
  it("parses an auto draft with the handoff vocabulary and auto_executed", () => {
    const cmd = parseAgentDraftCommandResponse({
      contract_version: "v1",
      environment: "dev-only-agent",
      accepted: true,
      replayed: false,
      auto_executed: true,
      correlation_id: "corr-a",
      draft: draftFixture({
        status: "auto",
        intent: "handoff",
        criteria: {},
        proposed_actions: [
          { type: "send_reply", params: { source: "kb_article", article_id: "kb-1" } },
          { type: "handoff_to_human", params: { reason: "reclamo del lead" } },
        ],
        policy_decision: "auto",
        execution_mode: "agent_policy",
      }),
    });
    expect(cmd.auto_executed).toBe(true);
    expect(cmd.draft.status).toBe("auto");
    expect(cmd.draft.intent).toBe("handoff");
    expect(cmd.draft.policy_decision).toBe("auto");
    expect(cmd.draft.execution_mode).toBe("agent_policy");
    expect(cmd.draft.proposed_actions[1]?.type).toBe("handoff_to_human");
  });

  it("rejects invalid policy_decision / execution_mode enums", () => {
    expect(() => parseAgentDraftsResponse({
      account_id: "a", as_of: "x", correlation_id: "c", count: 1,
      items: [draftFixture({ policy_decision: "maybe" })],
    })).toThrow(/policy_decision/);
    expect(() => parseAgentDraftsResponse({
      account_id: "a", as_of: "x", correlation_id: "c", count: 1,
      items: [draftFixture({ execution_mode: "robot" })],
    })).toThrow(/execution_mode/);
    expect(() => parseAgentDraftCommandResponse({
      contract_version: "v1", environment: "dev-only-agent", accepted: true,
      replayed: false, auto_executed: "yes", correlation_id: "c",
      draft: draftFixture(),
    })).toThrow(/auto_executed/);
  });

  it("rejects unknown nested keys inside proposed actions", () => {
    expect(() => parseAgentDraftsResponse({
      account_id: "a", as_of: "x", correlation_id: "c", count: 1,
      items: [draftFixture({
        proposed_actions: [{ type: "send_reply", params: {}, rogue: true }],
      })],
    })).toThrow(/unknown key/);
  });

  it("parses the agent state shape and rejects bad enums / keys / env", () => {
    const parsed = parseAgentStateResponse(agentStateFixture({ agent_mode: "paused", handoff_open: true }));
    expect(parsed.agent_mode).toBe("paused");
    expect(parsed.handoff_open).toBe(true);
    expect(parsed.environment).toBe("dev-only-agent");
    expect(() => parseAgentStateResponse(agentStateFixture({ agent_mode: "sleeping" }))).toThrow(/agent_mode/);
    expect(() => parseAgentStateResponse(agentStateFixture({ handoff_open: "yes" }))).toThrow(/handoff_open/);
    expect(() => parseAgentStateResponse(agentStateFixture({ environment: "dev-only-calendar" }))).toThrow();
    expect(() => parseAgentStateResponse(agentStateFixture({ rogue: 1 }))).toThrow(/unknown key/);
    expect(() => parseAgentStateResponse(null)).toThrow();
  });
});

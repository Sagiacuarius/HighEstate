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
  parseKbArticleResponse,
  parseKbArticleWriteResponse,
  parseKbArticlesResponse,
  parseKbSelfTestResponse,
  parseAgentPromptPreviewResponse,
  parseAgentPromptResponse,
  parseAgentPromptWriteResponse,
  parseAgentPromptsResponse,
  parseAgentSettingsResponse,
  parseAgentSettingsWriteResponse,
  parseCalendarConfigResponse,
  parseCalendarConfigWriteResponse,
  parseLeadFieldsResponse,
  parseLeadFieldsWriteResponse,
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

/* --- Agent Console WU16: settings / prompts / KB / calendar / allowlist --- */

function consoleEnvelope(resource: string, payload: Record<string, unknown>,
  overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    contract_version: "v1",
    environment: "dev-only-agent",
    accepted: true,
    replayed: false,
    correlation_id: "corr-k",
    [resource]: payload,
    ...overrides,
  };
}

function settingsPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    global_auto: true,
    calendar_auto_enabled: false,
    matrix: [
      { action_type: "send_reply", verdict: "auto" },
      { action_type: "register_interest", verdict: "auto" },
      { action_type: "create_follow_up", verdict: "auto" },
      { action_type: "transition_lead", verdict: "auto" },
      { action_type: "create_event", verdict: "escalate" },
      { action_type: "reschedule_event", verdict: "escalate" },
      { action_type: "handoff_to_human", verdict: "escalate" },
    ],
    account_id: "acct-1",
    ...overrides,
  };
}

function promptFixture(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "p1000000-0000-0000-0000-000000000001",
    version: 2,
    tone: "cercano",
    instructions: "Priorizar visitas.",
    composed_snapshot: "Tono cercano. Priorizar visitas.",
    is_active: true,
    created_at: "2026-09-15T06:00:00.000Z",
    ...overrides,
  };
}

function kbFixture(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "k1000000-0000-0000-0000-000000000001",
    category: "visitas",
    question: "¿Puedo visitar el sábado?",
    keywords: ["visita", "sábado"],
    answer: "Coordinamos la visita.",
    deactivated_at: null,
    created_at: "2026-09-15T06:00:00.000Z",
    ...overrides,
  };
}

describe("agent console contracts (WU16)", () => {
  it("parses valid settings / prompts / KB / calendar / allowlist fixtures", () => {
    const settings = parseAgentSettingsResponse(consoleEnvelope("settings", settingsPayload()));
    expect(settings.settings.global_auto).toBe(true);
    expect(settings.settings.calendar_auto_enabled).toBe(false);
    expect(settings.settings.matrix).toHaveLength(7);

    const prompts = parseAgentPromptsResponse(
      consoleEnvelope("prompts", { count: 1, items: [promptFixture()] }));
    expect(prompts.prompts.count).toBe(1);
    expect(prompts.prompts.items[0].version).toBe(2);

    const one = parseAgentPromptResponse(consoleEnvelope("prompt", promptFixture()));
    expect(one.prompt.tone).toBe("cercano");

    // Seeded v1 carries instructions "" — empty is valid, missing is not.
    const seeded = parseAgentPromptResponse(consoleEnvelope("prompt", promptFixture({ instructions: "" })));
    expect(seeded.prompt.instructions).toBe("");
    expect(() => parseAgentPromptResponse(consoleEnvelope("prompt", promptFixture({ instructions: undefined })))).toThrow();
    expect(() => parseAgentPromptsResponse(
      consoleEnvelope("prompts", { count: 1, items: [promptFixture({ tone: 7 })] }))).toThrow();

    const preview = parseAgentPromptPreviewResponse(
      consoleEnvelope("preview", { version: 2, composed_text: "Tono cercano." }));
    expect(preview.preview.composed_text).toBe("Tono cercano.");

    const articles = parseKbArticlesResponse(
      consoleEnvelope("articles", { count: 1, items: [kbFixture()] }));
    expect(articles.articles.items[0].category).toBe("visitas");

    const article = parseKbArticleResponse(consoleEnvelope("article", kbFixture()));
    expect(article.article.keywords).toEqual(["visita", "sábado"]);

    const hit = parseKbSelfTestResponse(consoleEnvelope("probe", {
      match: true,
      article: { id: "k1", category: "visitas", question: "¿Puedo visitar?" },
      hits: 2,
    }));
    expect(hit.probe.match).toBe(true);

    const miss = parseKbSelfTestResponse(consoleEnvelope("probe", { match: false }));
    expect(miss.probe).toEqual({ match: false });

    const calendar = parseCalendarConfigResponse(consoleEnvelope("calendar_config", {
      window_start: "10:00",
      window_end: "16:00",
      slot_minutes: 30,
      working_days: [0, 1, 2, 3, 4],
      destination_label: "Oficina central",
      account_id: "acct-1",
    }));
    expect(calendar.calendar_config.slot_minutes).toBe(30);

    const fields = parseLeadFieldsResponse(consoleEnvelope("allowlist", {
      allowed: ["stage_id", "status"],
      account_id: "acct-1",
    }));
    expect(fields.allowlist.allowed).toEqual(["stage_id", "status"]);
  });

  it("rejects invalid enums and unknown nested keys", () => {
    expect(() => parseAgentSettingsResponse(consoleEnvelope("settings", settingsPayload({
      matrix: [{ action_type: "self_destruct", verdict: "auto" }],
    })))).toThrow(/action_type/);
    expect(() => parseAgentSettingsResponse(consoleEnvelope("settings", settingsPayload({
      matrix: [{ action_type: "send_reply", verdict: "maybe" }],
    })))).toThrow(/verdict/);
    expect(() => parseAgentSettingsResponse(consoleEnvelope("settings", settingsPayload({
      matrix: [{ action_type: "send_reply", verdict: "auto", rogue: 1 }],
    })))).toThrow(/unknown key/);
    expect(() => parseAgentSettingsResponse(consoleEnvelope("settings", settingsPayload({ rogue: 1 })))).toThrow(/unknown key/);
    expect(() => parseAgentSettingsResponse(consoleEnvelope("settings", settingsPayload(), { rogue: 1 }))).toThrow(/unknown key/);
    expect(() => parseKbArticlesResponse(
      consoleEnvelope("articles", { count: 1, items: [kbFixture({ category: "cohetes" })] }))).toThrow(/category/);
    expect(() => parseKbArticleResponse(consoleEnvelope("article", kbFixture({ rogue: 1 })))).toThrow(/unknown key/);
    expect(() => parseKbSelfTestResponse(consoleEnvelope("probe", { match: true }))).toThrow();
    expect(() => parseCalendarConfigResponse(consoleEnvelope("calendar_config", {
      window_start: "18:00", window_end: "09:00", slot_minutes: 0,
      working_days: [7], destination_label: null, account_id: "a",
    }))).toThrow();
    expect(() => parseLeadFieldsResponse(consoleEnvelope("allowlist", {
      allowed: ["stage_id", "custom_field"], account_id: "a",
    }))).toThrow(/unknown column/);
    expect(() => parseAgentPromptPreviewResponse(consoleEnvelope("preview",
      { version: 1, composed_text: "x", rogue: 1 }))).toThrow(/unknown key/);
  });

  it("parses the write envelopes mirroring the command payloads", () => {
    const settingsWrite = parseAgentSettingsWriteResponse(consoleEnvelope("settings", {
      resource_id: "acct-1",
      global_auto: false,
      calendar_auto_enabled: true,
      matrix: [{ action_type: "send_reply", verdict: "auto" }],
      correlation_id: "corr-w",
    }));
    expect(settingsWrite.settings.global_auto).toBe(false);
    expect(settingsWrite.settings.matrix).toHaveLength(1);

    const promptWrite = parseAgentPromptWriteResponse(consoleEnvelope("prompt", {
      resource_id: "p3",
      version: 3,
      tone: "cercano",
      instructions: "",
      composed_snapshot: "Cercano.",
      reverted_from: 1,
      correlation_id: "corr-w",
    }));
    expect(promptWrite.prompt.reverted_from).toBe(1);
    expect(() => parseAgentPromptWriteResponse(consoleEnvelope("prompt", {
      resource_id: "p3", version: 3, tone: "x", instructions: "",
      composed_snapshot: "x", reverted_from: 1, correlation_id: "c",
      skeleton: "no",
    }))).toThrow(/unknown key/);

    const kbCreate = parseKbArticleWriteResponse(consoleEnvelope("article", {
      resource_id: "k2", category: "visitas", question: "¿Q?",
      correlation_id: "corr-w",
    }));
    expect(kbCreate.article.category).toBe("visitas");
    const kbDeactivate = parseKbArticleWriteResponse(consoleEnvelope("article", {
      resource_id: "k2", correlation_id: "corr-w",
    }));
    expect(kbDeactivate.article.question).toBeNull();

    const calWrite = parseCalendarConfigWriteResponse(consoleEnvelope("calendar_config", {
      resource_id: "acct-1",
      window_start: "10:00", window_end: "16:00", slot_minutes: 30,
      working_days: [0, 1, 2, 3, 4], destination_label: null,
      correlation_id: "corr-w",
    }));
    expect(calWrite.calendar_config.working_days).toEqual([0, 1, 2, 3, 4]);

    const fieldsWrite = parseLeadFieldsWriteResponse(consoleEnvelope("allowlist", {
      resource_id: "acct-1", allowed: ["status"], correlation_id: "corr-w",
    }));
    expect(fieldsWrite.allowlist.allowed).toEqual(["status"]);
    expect(() => parseLeadFieldsWriteResponse(consoleEnvelope("allowlist", {
      resource_id: "a", allowed: ["rogue"], correlation_id: "c",
    }))).toThrow(/unknown column/);
  });
});

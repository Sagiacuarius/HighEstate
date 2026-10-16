// @vitest-environment jsdom
/**
 * WU14 conversational agent — inline draft-card behavior tests. Renders the
 * real dashboard page with a stubbed fetch transport: the thread panel shows
 * a draft card (intent, criteria chips, proposed actions, reply), "Analizar"
 * POSTs /v1/demo/agent/analyze (message text only, never an account id), and
 * Aprobar/Rechazar hit the per-draft command routes and refresh. A decision
 * must never carry a client-supplied scope; a server denial surfaces the
 * envelope with its correlation id. No fixture data: everything asserted is
 * rendered from the stubbed agent API.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

process.env.NEXT_PUBLIC_DEMO_API_BASE_URL = "http://api.test";

type Call = { method: string; url: string; body?: unknown };
let calls: Call[] = [];

const META = { account_id: "acct-1", as_of: "2026-09-15T12:00:00.000000Z", correlation_id: "c1" };

function draft(overrides: Record<string, unknown> = {}): unknown {
  return {
    draft_id: "d1000000-0000-0000-0000-000000000001",
    status: "proposed",
    intent: "schedule_visit",
    criteria: { preferred_date: "2026-09-22", preferred_time: "10:00" },
    matched_property_ids: [],
    proposed_actions: [
      { type: "create_event", params: { title: "Visita solicitada por el lead", starts_at: "2026-09-22T13:00:00.000Z", ends_at: "2026-09-22T14:00:00.000Z", contact_id: "contact-1" } },
      { type: "create_follow_up", params: { title: "Preparar visita", due_at: "2026-09-16T06:00:00.000Z", contact_id: "contact-1" } },
    ],
    reply_text: "Coordinamos la visita.",
    model_provider: "stub",
    model_name: "stub-keyword-v1",
    policy_decision: "escalate",
    execution_mode: "human",
    conversation_id: "conv-1",
    contact_id: "contact-1",
    lead_id: null,
    created_at: "2026-09-15T06:00:00.000Z",
    decided_at: null,
    execution_result: null,
    ...overrides,
  };
}

function commandEnvelope(d: unknown, replayed = false, auto_executed = false): unknown {
  return {
    contract_version: "v1", environment: "dev-only-agent", accepted: true,
    replayed, auto_executed, correlation_id: "c1", draft: d,
  };
}

function agentState(overrides: Record<string, unknown> = {}): unknown {
  return {
    contract_version: "v1", environment: "dev-only-agent",
    conversation_id: "conv-1", agent_mode: "manual", handoff_open: false,
    account_id: "acct-1", correlation_id: "c1",
    ...overrides,
  };
}

function jsonOk(payload: unknown, correlation = "c1"): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json", "x-correlation-id": correlation },
  });
}

const READ_BODIES: Record<string, unknown> = {
  "/v1/demo/context/current": {
    contract_version: "v1", environment: "dev-only-demo-selector", correlation_id: "c1",
    user_id: "u1", agency_id: "ag1", client_account_id: "acct-1",
    client_account_name: "Aurea Realty North",
    permissions: ["client_account:read", "client_account:write"],
  },
  "/v1/demo/dashboard": {
    ...META,
    counts: {
      leads_total: 0, leads_by_status: {}, properties_total: 0,
      properties_by_availability: {}, conversations_open: 1,
      unread_messages: 1, follow_ups_pending: 0, follow_ups_overdue: 0, leads_by_stage: [],
    },
  },
  "/v1/demo/inbox": {
    ...META, count: 1,
    items: [{
      conversation_id: "conv-1", contact_id: "contact-1", contact_name: "Camila Torres",
      status: "open", unread_count: 1, updated_at: "2026-09-15T06:00:00.000Z",
      last_message: { message_id: "m1", direction: "inbound", body: "Quiero visitar el 22/9", created_at: "2026-09-15T06:00:00.000Z" },
    }],
  },
  "/v1/demo/inbox/conv-1/messages": {
    ...META, conversation_id: "conv-1", count: 1,
    items: [{ message_id: "m1", direction: "inbound", body: "Quiero visitar el 22/9", created_at: "2026-09-15T06:00:00.000Z" }],
  },
  "/v1/demo/leads": { ...META, count: 0, items: [] },
  "/v1/demo/properties": { ...META, count: 0, items: [] },
  "/v1/demo/pipeline": { ...META, pipelines: [] },
  "/v1/demo/agenda": { ...META, counts: { pending: 0, overdue: 0 }, items: [] },
  "/v1/demo/activity": { ...META, count: 0, items: [] },
};

let draftsPage: () => unknown = () => ({ ...META, count: 0, items: [] });
let nextCommand: () => unknown = () => commandEnvelope(draft());
let analyzeFail: { status: number; code: string; correlation: string } | null = null;
let statePage: () => unknown = () => agentState();
let stateFail: { status: number; code: string; correlation: string } | null = null;

/* --- WU16 Agent Console stubs (mirror api/routes/agent_wu14.py envelopes) --- */

function consoleOk(resource: string, payload: unknown): unknown {
  return {
    contract_version: "v1", environment: "dev-only-agent", accepted: true,
    replayed: false, correlation_id: "c1", [resource]: payload,
  };
}

const CONSOLE_ACTIONS = ["send_reply", "register_interest", "create_follow_up",
  "transition_lead", "create_event", "reschedule_event", "handoff_to_human",
  "set_custom_field_value"];
const CONSOLE_GATED = ["create_event", "reschedule_event", "handoff_to_human"];

let settingsPayload: () => unknown = () => ({
  global_auto: true, calendar_auto_enabled: true,
  matrix: CONSOLE_ACTIONS.map((action) => ({
    action_type: action,
    verdict: (CONSOLE_GATED as string[]).includes(action) ? "escalate" : "auto",
  })),
  account_id: "acct-1",
});
let promptsPayload: () => unknown = () => ({
  count: 2,
  items: [
    { id: "p1", version: 1, tone: "formal", instructions: "", composed_snapshot: "Formal.", is_active: false, created_at: "2026-09-14T06:00:00.000Z" },
    { id: "p2", version: 2, tone: "cercano", instructions: "Priorizar visitas.", composed_snapshot: "Cercano.", is_active: true, created_at: "2026-09-15T06:00:00.000Z" },
  ],
});
let previewPayload: () => unknown = () => ({ version: 2, composed_text: "Cercano. Priorizar visitas." });
let promptWritePayload: () => unknown = () => ({
  resource_id: "p3", version: 3, tone: "cercano", instructions: "Nueva.",
  composed_snapshot: "Cercano. Nueva.", reverted_from: 1, correlation_id: "c1",
});
let kbPayload: () => unknown = () => ({
  count: 1,
  items: [{
    id: "k1", category: "visitas", question: "¿Puedo visitar el sábado?",
    keywords: ["visita"], answer: "Coordinamos la visita.",
    deactivated_at: null, created_at: "2026-09-15T06:00:00.000Z",
  }],
});
let kbWritePayload: () => unknown = () => ({ resource_id: "k1", correlation_id: "c1" });
let selfTestPayload: () => unknown = () => ({
  match: true,
  article: { id: "k1", category: "visitas", question: "¿Puedo visitar el sábado?" },
  hits: 2,
});
let calCfgPayload: () => unknown = () => ({
  window_start: "09:00", window_end: "18:00", slot_minutes: 60,
  working_days: [0, 1, 2, 3, 4, 5], destination_label: null, account_id: "acct-1",
});
let fieldsPayload: () => unknown = () => ({ allowed: ["stage_id", "status"], account_id: "acct-1" });
let bindingsPayload: () => unknown = () => ({ count: 0, items: [] });
let customFieldsPayload: () => unknown = () => ({
  count: 1,
  items: [{ id: "cf-1", name: "telefono", type: "text", choices: [] }],
});
let consoleWriteFail: { status: number; code: string; correlation: string } | null = null;
let bindingsFail: { status: number; code: string; correlation: string } | null = null;

function errResponse(fail: { status: number; code: string; correlation: string }): Response {
  return new Response(JSON.stringify({
    error: { code: fail.code, message: `stub ${fail.code}`, correlation_id: fail.correlation },
  }), { status: fail.status, headers: { "content-type": "application/json", "x-correlation-id": fail.correlation } });
}

function stubFetch(): void {
  calls = [];
  draftsPage = () => ({ ...META, count: 0, items: [] });
  nextCommand = () => commandEnvelope(draft());
  analyzeFail = null;
  statePage = () => agentState();
  stateFail = null;
  consoleWriteFail = null;
  bindingsFail = null;
  bindingsPayload = () => ({ count: 0, items: [] });
  customFieldsPayload = () => ({
    count: 1,
    items: [{ id: "cf-1", name: "telefono", type: "text", choices: [] }],
  });
  selfTestPayload = () => ({
    match: true,
    article: { id: "k1", category: "visitas", question: "¿Puedo visitar el sábado?" },
    hits: 2,
  });
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const parsed = new URL(url);
    const method = init?.method ?? "GET";
    calls.push({ method, url, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    if (parsed.pathname === "/v1/demo/agent/drafts" && method === "GET") return jsonOk(draftsPage());
    if (parsed.pathname === "/v1/demo/agent/state") {
      if (method === "GET") return jsonOk(statePage());
      if (stateFail) {
        return new Response(JSON.stringify({
          error: { code: stateFail.code, message: `stub ${stateFail.code}`, correlation_id: stateFail.correlation },
        }), { status: stateFail.status, headers: { "content-type": "application/json", "x-correlation-id": stateFail.correlation } });
      }
      return jsonOk(statePage());
    }
    if (parsed.pathname === "/v1/demo/agent/handoff/resume" && method === "POST") {
      return jsonOk(agentState({ agent_mode: "auto", handoff_open: false }));
    }
    if (parsed.pathname === "/v1/demo/agent/analyze") {
      if (analyzeFail) {
        return new Response(JSON.stringify({
          error: { code: analyzeFail.code, message: `stub ${analyzeFail.code}`, correlation_id: analyzeFail.correlation },
        }), { status: analyzeFail.status, headers: { "content-type": "application/json", "x-correlation-id": analyzeFail.correlation } });
      }
      return jsonOk(commandEnvelope(draft()));
    }
    const decision = parsed.pathname.match(/^\/v1\/demo\/agent\/drafts\/([^/]+)\/(approve|reject)$/);
    if (decision) return jsonOk(nextCommand());
    if (parsed.pathname === "/v1/demo/agent/settings") {
      if (method === "GET") return jsonOk(consoleOk("settings", settingsPayload()));
      if (consoleWriteFail) return errResponse(consoleWriteFail);
      return jsonOk(consoleOk("settings", {
        resource_id: "acct-1", global_auto: true, calendar_auto_enabled: true,
        matrix: (settingsPayload() as { matrix: unknown }).matrix, correlation_id: "c1",
      }));
    }
    if (parsed.pathname === "/v1/demo/agent/prompts" && method === "GET") {
      return jsonOk(consoleOk("prompts", promptsPayload()));
    }
    if (parsed.pathname === "/v1/demo/agent/prompts" && method === "POST") {
      if (consoleWriteFail) return errResponse(consoleWriteFail);
      return jsonOk(consoleOk("prompt", promptWritePayload()));
    }
    if (parsed.pathname === "/v1/demo/agent/prompts/composed-preview") {
      return jsonOk(consoleOk("preview", previewPayload()));
    }
    if (/^\/v1\/demo\/agent\/prompts\/\d+\/revert$/.test(parsed.pathname) && method === "POST") {
      if (consoleWriteFail) return errResponse(consoleWriteFail);
      return jsonOk(consoleOk("prompt", promptWritePayload()));
    }
    if (/^\/v1\/demo\/agent\/prompts\/\d+$/.test(parsed.pathname) && method === "GET") {
      return jsonOk(consoleOk("prompt", (promptsPayload() as { items: unknown[] }).items[0]));
    }
    if (parsed.pathname === "/v1/demo/agent/kb/articles") {
      if (method === "GET") return jsonOk(consoleOk("articles", kbPayload()));
      if (consoleWriteFail) return errResponse(consoleWriteFail);
      return jsonOk(consoleOk("article", kbWritePayload()));
    }
    if (/^\/v1\/demo\/agent\/kb\/articles\/.+$/.test(parsed.pathname) && (method === "PUT" || method === "DELETE")) {
      if (consoleWriteFail) return errResponse(consoleWriteFail);
      return jsonOk(consoleOk("article", kbWritePayload()));
    }
    if (parsed.pathname === "/v1/demo/agent/kb/self-test") {
      if (consoleWriteFail) return errResponse(consoleWriteFail);
      return jsonOk(consoleOk("probe", selfTestPayload()));
    }
    if (parsed.pathname === "/v1/demo/agent/calendar-config") {
      if (method === "GET") return jsonOk(consoleOk("calendar_config", calCfgPayload()));
      if (consoleWriteFail) return errResponse(consoleWriteFail);
      const { account_id: _dropped, ...readCfg } = calCfgPayload() as Record<string, unknown>;
      void _dropped;
      return jsonOk(consoleOk("calendar_config", {
        resource_id: "acct-1", ...readCfg, correlation_id: "c1",
      }));
    }
    if (parsed.pathname === "/v1/demo/agent/lead-fields") {
      if (method === "GET") return jsonOk(consoleOk("allowlist", fieldsPayload()));
      if (consoleWriteFail) return errResponse(consoleWriteFail);
      return jsonOk(consoleOk("allowlist", {
        resource_id: "acct-1",
        allowed: (fieldsPayload() as { allowed: unknown }).allowed,
        correlation_id: "c1",
      }));
    }
    if (parsed.pathname === "/v1/demo/agent/signal-bindings") {
      if (method === "GET") return jsonOk(consoleOk("signal_bindings", bindingsPayload()));
      if (bindingsFail) return errResponse(bindingsFail);
      const posted = typeof init?.body === "string"
        ? JSON.parse(init.body) as Record<string, unknown> : {};
      return jsonOk(consoleOk("signal_binding", {
        signal_key: posted.signal_key ?? "phone",
        field_definition_id: posted.field_definition_id ?? "cf-1",
        field_name: "telefono",
        field_type: "text",
      }));
    }
    if (/^\/v1\/demo\/agent\/signal-bindings\/.+$/.test(parsed.pathname) && method === "DELETE") {
      if (bindingsFail) return errResponse(bindingsFail);
      const signal = decodeURIComponent(parsed.pathname.split("/").pop() as string);
      return jsonOk(consoleOk("signal_binding", { signal_key: signal, deleted: true }));
    }
    if (parsed.pathname === "/v1/demo/agent/custom-fields") {
      if (method === "GET") return jsonOk(consoleOk("custom_fields", customFieldsPayload()));
      if (consoleWriteFail) return errResponse(consoleWriteFail);
      return jsonOk(consoleOk("custom_field", { resource_id: "cf-1", correlation_id: "c1" }));
    }
    const body = READ_BODIES[parsed.pathname];
    if (body) return jsonOk(body);
    return new Response(JSON.stringify({
      error: { code: "route_not_found", message: "no stub", correlation_id: "c1" },
    }), { status: 404, headers: { "x-correlation-id": "c1" } });
  }));
}

async function openInboxAndConversation(): Promise<void> {
  await waitFor(() => {
    if (!document.querySelector('.main-nav a[href="#inbox"]')) throw new Error("nav not ready");
  });
  fireEvent.click(document.querySelector('.main-nav a[href="#inbox"]') as HTMLElement);
  await waitFor(() => {
    const conv = document.querySelector(".view-section.is-visible .conversation");
    if (!conv) throw new Error("conversation row not ready");
    fireEvent.click(conv as HTMLElement);
  });
  await screen.findByText(/Asistente de/);
}

describe("WU14 conversational agent draft cards", () => {
  beforeEach(() => stubFetch());
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders a proposed draft card with Spanish labels and approve/reject", async () => {
    draftsPage = () => ({ ...META, count: 1, items: [draft()] });
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openInboxAndConversation();
    expect(await screen.findByText("Pedido de visita")).toBeTruthy();
    expect(screen.getByText("Propuesto")).toBeTruthy();
    expect(screen.getByText(/Crear evento en el calendario/)).toBeTruthy();
    expect(screen.getByText(/Crear seguimiento/)).toBeTruthy();
    expect(screen.getByText(/Coordinamos la visita/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Aprobar" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Rechazar" })).toBeTruthy();
  });

  it("Analizar posts the message text only, never an account id", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openInboxAndConversation();
    const button = await screen.findByRole("button", { name: /Analizar con el agente/ });
    fireEvent.click(button);
    await waitFor(() => {
      const analyzeCalls = calls.filter((c) => c.url.includes("/v1/demo/agent/analyze"));
      expect(analyzeCalls.length).toBe(1);
      expect(analyzeCalls[0].method).toBe("POST");
      expect((analyzeCalls[0].body as { body?: string }).body).toBeTruthy();
      for (const c of analyzeCalls) expect(c.url).not.toMatch(/clientAccountId|client_account_id=/);
    });
  });

  it("Aprobar hits the per-draft approve route and refreshes drafts", async () => {
    draftsPage = () => ({ ...META, count: 1, items: [draft()] });
    nextCommand = () => commandEnvelope(draft({
      status: "executed",
      decided_at: "2026-09-15T07:00:00.000Z",
      execution_result: [{ index: 0, type: "create_event", outcome: "success", resource_id: "e1" },
                         { index: 1, type: "create_follow_up", outcome: "success", resource_id: "f1" }],
    }));
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openInboxAndConversation();
    fireEvent.click(await screen.findByRole("button", { name: "Aprobar" }));
    await waitFor(() => {
      const approveCalls = calls.filter((c) => c.url.endsWith("/approve"));
      expect(approveCalls.length).toBe(1);
      expect(approveCalls[0].url).toContain("/v1/demo/agent/drafts/d1000000-0000-0000-0000-000000000001/approve");
    });
    expect(await screen.findByText(/Borrador ejecutado · 2 aplicadas/)).toBeTruthy();
  });

  it("empty state renders when there are no drafts", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openInboxAndConversation();
    expect(await screen.findByText(/Todavía no hay borradores/)).toBeTruthy();
  });

  it("analyze failure surfaces the envelope with correlation", async () => {
    analyzeFail = { status: 502, code: "provider_unavailable", correlation: "corr-agent-err" };
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openInboxAndConversation();
    fireEvent.click(await screen.findByRole("button", { name: /Analizar con el agente/ }));
    expect(await screen.findByText(/corr-agent-err/)).toBeTruthy();
    expect(screen.getByText(/502 · provider_unavailable/)).toBeTruthy();
  });
});

describe("WU15 agent autonomy: auto badge, handoff, mode switch, resume", () => {
  beforeEach(() => stubFetch());
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  function autoDraft(overrides: Record<string, unknown> = {}): unknown {
    return draft({
      status: "auto",
      intent: "handoff",
      criteria: {},
      proposed_actions: [
        { type: "send_reply", params: { source: "kb_article", article_id: "kb-1" } },
        { type: "handoff_to_human", params: { reason: "reclamo del lead" } },
      ],
      reply_text: null,
      policy_decision: "auto",
      execution_mode: "agent_policy",
      decided_at: "2026-09-15T07:00:00.000Z",
      execution_result: [{ index: 1, type: "handoff_to_human", outcome: "success", resource_id: "conv-1" }],
      ...overrides,
    });
  }

  it("auto badge renders on auto drafts and approve/reject stay hidden", async () => {
    draftsPage = () => ({ ...META, count: 1, items: [autoDraft()] });
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openInboxAndConversation();
    expect(await screen.findByText(/Auto · decisión automática/)).toBeTruthy();
    expect(screen.getAllByText("Derivación a humano").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Automático").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Aprobar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rechazar" })).toBeNull();
  });

  it("handoff flag + paused indicator render on the handoff-open row", async () => {
    statePage = () => agentState({ agent_mode: "paused", handoff_open: true });
    draftsPage = () => ({ ...META, count: 1, items: [autoDraft()] });
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openInboxAndConversation();
    expect(await screen.findByText("Pausado")).toBeTruthy();
    expect(screen.getAllByText("Derivado a humano").length).toBeGreaterThan(0);
    expect(screen.getByText("Modo: Pausado")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retomar conversación" })).toBeTruthy();
  });

  it("mode switch posts to the typed state endpoint and refreshes", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openInboxAndConversation();
    const stateGetsBefore = calls.filter((c) => c.url.includes("/v1/demo/agent/state") && c.method === "GET").length;
    fireEvent.click(await screen.findByRole("button", { name: "Automático" }));
    await waitFor(() => {
      const posts = calls.filter((c) => c.url.endsWith("/v1/demo/agent/state") && c.method === "POST");
      expect(posts.length).toBe(1);
      expect((posts[0].body as { mode?: string }).mode).toBe("auto");
      for (const c of posts) expect(c.url).not.toMatch(/clientAccountId|account_id=/);
    });
    expect(await screen.findByText(/Modo automático activado/)).toBeTruthy();
    await waitFor(() => {
      const stateGetsAfter = calls.filter((c) => c.url.includes("/v1/demo/agent/state") && c.method === "GET").length;
      const draftGets = calls.filter((c) => c.url.includes("/v1/demo/agent/drafts") && c.method === "GET").length;
      expect(stateGetsAfter + draftGets).toBeGreaterThan(stateGetsBefore);
    });
  });

  it("resume wires to the handoff resume endpoint with refresh", async () => {
    statePage = () => agentState({ agent_mode: "paused", handoff_open: true });
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openInboxAndConversation();
    fireEvent.click(await screen.findByRole("button", { name: "Retomar conversación" }));
    await waitFor(() => {
      const resumes = calls.filter((c) => c.url.endsWith("/v1/demo/agent/handoff/resume"));
      expect(resumes.length).toBe(1);
      expect(resumes[0].method).toBe("POST");
    });
    expect(await screen.findByText(/Conversación retomada/)).toBeTruthy();
  });

  it("mode failure surfaces the envelope with correlation", async () => {
    stateFail = { status: 409, code: "agent_handoff_open", correlation: "corr-state-err" };
    statePage = () => agentState({ agent_mode: "manual", handoff_open: false });
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openInboxAndConversation();
    fireEvent.click(await screen.findByRole("button", { name: "Automático" }));
    expect(await screen.findByText(/corr-state-err/)).toBeTruthy();
    expect(screen.getByText(/409 · agent_handoff_open/)).toBeTruthy();
  });
});

describe("WU16 agent console: Agente view with five sections", () => {
  beforeEach(() => stubFetch());
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  async function openAgente(): Promise<void> {
    await waitFor(() => {
      if (!document.querySelector('.main-nav a[href="#agente"]')) throw new Error("nav not ready");
    });
    fireEvent.click(document.querySelector('.main-nav a[href="#agente"]') as HTMLElement);
    await screen.findByText("Campos del prospecto", { selector: "h3" });
  }

  it("renders the five sections with Spanish labels plus live console data", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    for (const heading of ["Ajustes", "Prompt", "Base de conocimiento", "Calendario", "Campos del prospecto"]) {
      expect(screen.getByText(heading, { selector: "h3" })).toBeTruthy();
    }
    expect(await screen.findByText("Vista previa compuesta · v2")).toBeTruthy();
    expect(screen.getByText("¿Puedo visitar el sábado?")).toBeTruthy();
    expect(screen.getByLabelText("Automatización global")).toBeTruthy();
    expect(screen.getByLabelText("Creación automática de eventos")).toBeTruthy();
  });

  it("matrix edit PUTs the typed settings payload, never an account id", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    const select = await screen.findByLabelText("Veredicto create_event") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "auto" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar ajustes" }));
    await waitFor(() => {
      const puts = calls.filter((c) => c.url.endsWith("/v1/demo/agent/settings") && c.method === "PUT");
      expect(puts.length).toBe(1);
      const body = puts[0].body as Record<string, unknown>;
      expect(body.global_auto).toBe(true);
      expect(body.calendar_auto_enabled).toBe(true);
      const matrix = body.matrix as Array<{ action_type: string; verdict: string }>;
      expect(matrix).toHaveLength(8);
      expect(matrix.find((e) => e.action_type === "create_event")?.verdict).toBe("auto");
      expect(matrix.find((e) => e.action_type === "handoff_to_human")?.verdict).toBe("escalate");
      expect(matrix.find((e) => e.action_type === "set_custom_field_value")?.verdict).toBe("auto");
      for (const c of puts) expect(c.url).not.toMatch(/clientAccountId|account_id=/);
      expect(JSON.stringify(body)).not.toMatch(/client_account_id/);
    });
    expect(await screen.findByText("Ajustes guardados")).toBeTruthy();
  });

  it("prompt revert POSTs to the version revert route and refreshes", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    fireEvent.click(await screen.findByRole("button", { name: "Revertir a la versión 1" }));
    await waitFor(() => {
      const reverts = calls.filter((c) => c.url.endsWith("/v1/demo/agent/prompts/1/revert"));
      expect(reverts.length).toBe(1);
      expect(reverts[0].method).toBe("POST");
      for (const c of reverts) expect(c.url).not.toMatch(/clientAccountId|account_id=/);
    });
    expect(await screen.findByText(/Reversión a la versión 1/)).toBeTruthy();
  });

  it("KB self-test renders a hit with article and hits", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    fireEvent.change(await screen.findByLabelText("Pregunta de prueba"), {
      target: { value: "¿Puedo visitar el sábado?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Probar" }));
    expect(await screen.findByText(/Coincidencia:/)).toBeTruthy();
    expect(screen.getByText(/2 aciertos/)).toBeTruthy();
    await waitFor(() => {
      const probes = calls.filter((c) => c.url.endsWith("/v1/demo/agent/kb/self-test"));
      expect(probes.length).toBe(1);
      expect((probes[0].body as { question?: string }).question).toBe("¿Puedo visitar el sábado?");
    });
  });

  it("KB self-test renders an explicit no-match", async () => {
    selfTestPayload = () => ({ match: false });
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    fireEvent.change(await screen.findByLabelText("Pregunta de prueba"), {
      target: { value: "¿Tienen cohetes?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Probar" }));
    expect(await screen.findByText(/Sin coincidencia/)).toBeTruthy();
  });

  it("calendar form PUTs the typed window payload", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    fireEvent.change(await screen.findByLabelText("Inicio de la ventana"), { target: { value: "10:00" } });
    fireEvent.change(screen.getByLabelText("Fin de la ventana"), { target: { value: "16:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar calendario" }));
    await waitFor(() => {
      const puts = calls.filter((c) => c.url.endsWith("/v1/demo/agent/calendar-config") && c.method === "PUT");
      expect(puts.length).toBe(1);
      const body = puts[0].body as Record<string, unknown>;
      expect(body.window_start).toBe("10:00");
      expect(body.window_end).toBe("16:00");
      expect(body.slot_minutes).toBe(60);
      expect(body.working_days).toEqual([0, 1, 2, 3, 4, 5]);
      expect(body.destination_label).toBeNull();
      expect(JSON.stringify(body)).not.toMatch(/client_account_id/);
    });
    expect(await screen.findByText("Calendario guardado")).toBeTruthy();
  });

  it("failed writes surface the envelope with correlation", async () => {
    consoleWriteFail = { status: 400, code: "invalid_command", correlation: "corr-console-err" };
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    fireEvent.click(await screen.findByRole("button", { name: "Guardar ajustes" }));
    expect(await screen.findByText(/corr-console-err/)).toBeTruthy();
    expect(screen.getByText(/400 · invalid_command/)).toBeTruthy();
  });
});

describe("WU18 Fuentes del agente: signal bindings console section", () => {
  beforeEach(() => stubFetch());
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  async function openAgente(): Promise<void> {
    await waitFor(() => {
      if (!document.querySelector('.main-nav a[href="#agente"]')) throw new Error("nav not ready");
    });
    fireEvent.click(document.querySelector('.main-nav a[href="#agente"]') as HTMLElement);
    await screen.findByText("Fuentes del agente", { selector: "h3" });
  }

  it("renders the section with the closed signal catalog, field options, and live bindings", async () => {
    bindingsPayload = () => ({
      count: 1,
      items: [{
        signal_key: "phone", field_definition_id: "cf-1",
        field_name: "telefono", field_type: "text",
      }],
    });
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    expect(screen.getByText("Fuentes del agente", { selector: "h3" })).toBeTruthy();
    const signalSelect = screen.getByLabelText("Señal") as HTMLSelectElement;
    expect(signalSelect.options.length).toBe(7);
    expect(screen.getByText("Teléfono", { selector: "option" })).toBeTruthy();
    const fieldSelect = screen.getByLabelText("Campo personalizado") as HTMLSelectElement;
    expect(fieldSelect.options.length).toBe(2);
    expect(screen.getByText(/telefono · Texto/)).toBeTruthy();
    const bindingsTable = screen.getByRole("table", { name: "Enlaces señal a campo" });
    expect(bindingsTable.textContent).toMatch(/telefono/);
    expect(screen.getByRole("button", { name: "Eliminar enlace phone" })).toBeTruthy();
  });

  it("renders the empty state when no bindings exist", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    expect(await screen.findByText(/Sin enlaces/)).toBeTruthy();
  });

  it("create posts the typed signal payload and refreshes the list", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    await screen.findByText(/Sin enlaces/);
    fireEvent.change(screen.getByLabelText("Campo personalizado"), { target: { value: "cf-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear enlace" }));
    await waitFor(() => {
      const posts = calls.filter((c) => c.url.endsWith("/v1/demo/agent/signal-bindings") && c.method === "POST");
      expect(posts.length).toBe(1);
      expect(posts[0].body).toEqual({ signal_key: "phone", field_definition_id: "cf-1" });
      for (const c of posts) expect(c.url).not.toMatch(/clientAccountId|account_id=/);
      expect(JSON.stringify(posts[0].body)).not.toMatch(/client_account_id/);
    });
    expect(await screen.findByText("Enlace de señal creado")).toBeTruthy();
    await waitFor(() => {
      const gets = calls.filter((c) => c.url.endsWith("/v1/demo/agent/signal-bindings") && c.method === "GET");
      expect(gets.length).toBeGreaterThan(1);
    });
  });

  it("delete wires the per-signal route and refreshes the list", async () => {
    bindingsPayload = () => ({
      count: 1,
      items: [{
        signal_key: "phone", field_definition_id: "cf-1",
        field_name: "telefono", field_type: "text",
      }],
    });
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    fireEvent.click(await screen.findByRole("button", { name: "Eliminar enlace phone" }));
    await waitFor(() => {
      const deletes = calls.filter((c) => c.url.endsWith("/v1/demo/agent/signal-bindings/phone") && c.method === "DELETE");
      expect(deletes.length).toBe(1);
      for (const c of deletes) expect(c.url).not.toMatch(/clientAccountId|account_id=/);
    });
    expect(await screen.findByText("Enlace de señal eliminado")).toBeTruthy();
  });

  it("binding failure surfaces the envelope with correlation", async () => {
    bindingsFail = { status: 400, code: "invalid_command", correlation: "corr-bind-err" };
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAgente();
    await screen.findByText(/Sin enlaces/);
    fireEvent.change(screen.getByLabelText("Campo personalizado"), { target: { value: "cf-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear enlace" }));
    expect(await screen.findByText(/corr-bind-err/)).toBeTruthy();
    expect(screen.getByText(/400 · invalid_command/)).toBeTruthy();
  });
});

/* --- WU19 Embudo stubs (mirror the commercial-pipeline write envelopes) --- */

function pipeWrite(result: unknown): unknown {
  return {
    contract_version: "v1", environment: "dev-only-commercial-pipelines",
    accepted: true, replayed: false, correlation_id: "c1", result,
  };
}

let embudoPage: () => unknown = () => ({ ...META, count: 0, items: [] });
let embudoMoved = true;
let embudoRenameWarning = true;
let embudoWriteFail: { status: number; code: string; correlation: string } | null = null;
let embudoDeleteFail: { status: number; code: string; correlation: string } | null = null;

function defaultEmbudoPage(): unknown {
  return {
    ...META, count: 1,
    items: [{
      pipeline_id: "p1", name: "Embudo demo",
      stages: [
        { stage_id: "s1", name: "Nuevo", position: 0, lead_count: 0 },
        { stage_id: "s2", name: "Calificado", position: 1, lead_count: 0 },
      ],
    }],
  };
}

function stubEmbudoTransport(): void {
  const baseFetch = globalThis.fetch as unknown as (input: string, init?: RequestInit) => Promise<Response>;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const parsed = new URL(url);
    const method = init?.method ?? "GET";
    const readBody = (): unknown =>
      (typeof init?.body === "string" ? JSON.parse(init.body) : undefined);
    if (parsed.pathname === "/v1/demo/pipeline" && method === "GET") {
      calls.push({ method, url, body: undefined });
      return jsonOk(embudoPage());
    }
    if (parsed.pathname === "/v1/demo/pipelines" && method === "POST") {
      const posted = (readBody() ?? {}) as { name?: string; stages?: string[] };
      calls.push({ method, url, body: readBody() });
      if (embudoWriteFail) return errResponse(embudoWriteFail);
      return jsonOk(pipeWrite({
        resource_id: "p9",
        name: posted.name ?? "Embudo norte",
        stages: Array.isArray(posted.stages)
          ? posted.stages.map((name, index) => ({ stage_id: `s9-${index}`, name, position: index }))
          : [],
        correlation_id: "c1",
      }));
    }
    if (/^\/v1\/demo\/stages\/[^/]+$/.test(parsed.pathname) && method === "PUT") {
      const stageId = decodeURIComponent(parsed.pathname.split("/").pop() as string);
      const posted = (readBody() ?? {}) as { name?: string };
      calls.push({ method, url, body: readBody() });
      if (embudoWriteFail) return errResponse(embudoWriteFail);
      return jsonOk(pipeWrite({
        resource_id: stageId, name: posted.name ?? "Renombrada",
        previous_name: "Nuevo", agent_name_warning: embudoRenameWarning, correlation_id: "c1",
      }));
    }
    const move = parsed.pathname.match(/^\/v1\/demo\/stages\/([^/]+)\/move$/);
    if (move && method === "POST") {
      calls.push({ method, url, body: readBody() });
      if (embudoWriteFail) return errResponse(embudoWriteFail);
      return jsonOk(pipeWrite({
        resource_id: decodeURIComponent(move[1]),
        position: 0, moved: embudoMoved, correlation_id: "c1",
      }));
    }
    const del = parsed.pathname.match(/^\/v1\/demo\/stages\/([^/]+)$/);
    if (del && method === "DELETE") {
      calls.push({ method, url, body: readBody() });
      if (embudoDeleteFail) {
        return new Response(JSON.stringify({
          error: { code: embudoDeleteFail.code, message: `stub ${embudoDeleteFail.code}`, correlation_id: embudoDeleteFail.correlation },
          lead_count: 3,
        }), { status: embudoDeleteFail.status, headers: { "content-type": "application/json", "x-correlation-id": embudoDeleteFail.correlation } });
      }
      return jsonOk(pipeWrite({
        resource_id: decodeURIComponent(del[1]),
        deleted: true, agent_name_warning: false, correlation_id: "c1",
      }));
    }
    return baseFetch(input, init);
  }));
}

describe("WU19 Embudo: pipelines y etapas", () => {
  beforeEach(() => {
    stubFetch();
    embudoPage = () => defaultEmbudoPage();
    embudoMoved = true;
    embudoRenameWarning = true;
    embudoWriteFail = null;
    embudoDeleteFail = null;
    stubEmbudoTransport();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  async function openEmbudo(): Promise<void> {
    await waitFor(() => {
      if (!document.querySelector('.main-nav a[href="#pipeline"]')) throw new Error("nav not ready");
    });
    fireEvent.click(document.querySelector('.main-nav a[href="#pipeline"]') as HTMLElement);
    await screen.findByText("Embudo comercial");
  }

  function embudoButton(name: string): HTMLElement {
    const panel = document.querySelector(".view-section.is-visible .pipeline-panel");
    const found = Array.from(panel?.querySelectorAll("button") ?? [])
      .find((b) => b.textContent?.trim() === name);
    if (!found) throw new Error(`embudo button not found: ${name}`);
    return found as HTMLElement;
  }

  it("create form posts the typed payload and refreshes", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openEmbudo();
    const pipelineGetsBefore = calls.filter((c) => c.url.endsWith("/v1/demo/pipeline") && c.method === "GET").length;
    fireEvent.change(screen.getByLabelText("Nombre del embudo"), { target: { value: "Embudo norte" } });
    fireEvent.change(screen.getByLabelText("Etapas iniciales (separadas por comas)"), { target: { value: "Nuevo, Calificado, Visita" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear embudo" }));
    await waitFor(() => {
      const posts = calls.filter((c) => c.url.endsWith("/v1/demo/pipelines") && c.method === "POST");
      expect(posts.length).toBe(1);
      expect(posts[0].body).toEqual({ name: "Embudo norte", stages: ["Nuevo", "Calificado", "Visita"] });
      expect(JSON.stringify(posts[0].body)).not.toMatch(/client_account_id/);
    });
    expect(await screen.findByText("Embudo creado")).toBeTruthy();
    await waitFor(() => {
      const pipelineGetsAfter = calls.filter((c) => c.url.endsWith("/v1/demo/pipeline") && c.method === "GET").length;
      expect(pipelineGetsAfter).toBeGreaterThan(pipelineGetsBefore);
    });
  });

  it("stage delete blocked shows the denial with lead count and never retries", async () => {
    embudoPage = () => ({
      ...META, count: 1,
      items: [{
        pipeline_id: "p1", name: "Embudo demo",
        stages: [{ stage_id: "s1", name: "Nuevo", position: 0, lead_count: 3 }],
      }],
    });
    embudoDeleteFail = { status: 409, code: "stage_has_leads", correlation: "corr-deny" };
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openEmbudo();
    fireEvent.click(embudoButton("Eliminar"));
    expect(await screen.findByText("Confirmar eliminación")).toBeTruthy();
    expect(await screen.findByText(/Repetí clic en eliminar para confirmar/)).toBeTruthy();
    expect(calls.filter((c) => c.method === "DELETE" && c.url.includes("/v1/demo/stages/"))).toHaveLength(0);
    fireEvent.click(embudoButton("Confirmar eliminación"));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "DELETE" && c.url.includes("/v1/demo/stages/s1"))).toHaveLength(1);
    });
    expect(await screen.findByText(/409 · stage_has_leads/)).toBeTruthy();
    expect(screen.getByText(/corr-deny/)).toBeTruthy();
    expect(screen.getByText(/3 prospecto\(s\)/)).toBeTruthy();
    expect(calls.filter((c) => c.method === "DELETE" && c.url.includes("/v1/demo/stages/"))).toHaveLength(1);
  });

  it("rename surfacing agent_name_warning shows the warning badge", async () => {
    embudoRenameWarning = true;
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openEmbudo();
    const form = document.querySelector('form[aria-label="Renombrar etapa Nuevo"]') as HTMLFormElement;
    expect(form).toBeTruthy();
    fireEvent.change(form.querySelector('input[name="name"]') as HTMLInputElement, { target: { value: "Contacto" } });
    fireEvent.click(form.querySelector('button[type="submit"]') as HTMLElement);
    await waitFor(() => {
      const puts = calls.filter((c) => c.url.endsWith("/v1/demo/stages/s1") && c.method === "PUT");
      expect(puts.length).toBe(1);
      expect(puts[0].body).toEqual({ name: "Contacto" });
      expect(JSON.stringify(puts[0].body)).not.toMatch(/client_account_id/);
    });
    expect(await screen.findByText(/el nombre coincide con una etapa usada por el agente/)).toBeTruthy();
    expect(screen.getByText("nombre sensible del agente")).toBeTruthy();
  });

  it("move up/down posts direction and handles the already-at-edge message", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openEmbudo();
    fireEvent.click(await screen.findByRole("button", { name: "Subir Nuevo" }));
    await waitFor(() => {
      const moves = calls.filter((c) => c.url.endsWith("/v1/demo/stages/s1/move") && c.method === "POST");
      expect(moves.length).toBe(1);
      expect(moves[0].body).toEqual({ direction: "up" });
    });
    expect(await screen.findByText("Etapa movida")).toBeTruthy();
    embudoMoved = false;
    fireEvent.click(screen.getByRole("button", { name: "Bajar Nuevo" }));
    await waitFor(() => {
      const moves = calls.filter((c) => c.url.endsWith("/v1/demo/stages/s1/move") && c.method === "POST");
      expect(moves.length).toBe(2);
      expect(moves[1].body).toEqual({ direction: "down" });
    });
    expect(await screen.findByText("La etapa ya está en ese extremo")).toBeTruthy();
  });

  it("failure surfaces the envelope with correlation", async () => {
    embudoWriteFail = { status: 400, code: "invalid_command", correlation: "corr-pipe-err" };
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openEmbudo();
    fireEvent.change(screen.getByLabelText("Nombre del embudo"), { target: { value: "Embudo norte" } });
    fireEvent.change(screen.getByLabelText("Etapas iniciales (separadas por comas)"), { target: { value: "Nuevo, Visita" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear embudo" }));
    expect(await screen.findByText(/corr-pipe-err/)).toBeTruthy();
    expect(screen.getByText(/400 · invalid_command/)).toBeTruthy();
  });
});

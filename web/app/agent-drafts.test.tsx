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

function stubFetch(): void {
  calls = [];
  draftsPage = () => ({ ...META, count: 0, items: [] });
  nextCommand = () => commandEnvelope(draft());
  analyzeFail = null;
  statePage = () => agentState();
  stateFail = null;
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

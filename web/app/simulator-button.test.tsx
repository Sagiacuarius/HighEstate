// @vitest-environment jsdom
/**
 * MVP Commercial Demo WU9 — Inbox simulator button behavior tests.
 * Renders the real dashboard page with a stubbed fetch transport: the button
 * must be labeled dev-only, call POST /v1/demo/simulate once per click,
 * show apply vs replay notes, surface denials as envelope notes, and expose
 * the explicit force-reset path only after the server blocks with
 * demo_effects_pending (WU9 resolution of the known fail-closed reset).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

process.env.NEXT_PUBLIC_DEMO_API_BASE_URL = "http://api.test";

type Call = { method: string; url: string; body?: string };
let calls: Call[] = [];
let simulateBodies: unknown[] = [];

const META = { account_id: "acct-1", as_of: "2026-09-14T12:00:00.000Z", correlation_id: "c1" };

function simulateResult(overrides: Record<string, unknown> = {}): unknown {
  return {
    contract_version: "v1",
    environment: "dev-only-simulator",
    accepted: true,
    replayed: false,
    correlation_id: "c1",
    result: {
      resource_id: "lead-1abcd",
      lead_created: false,
      contact_id: "contact-1",
      conversation_id: "conv-1",
      event_id: "sim-a-1",
      message_id: "msg-1",
      previous_stage_id: "stage-a1",
      current_stage_id: "stage-a2",
      follow_up_id: "fu-1",
      received_at: "2026-09-05T12:00:00.000Z",
      correlation_id: "c1",
    },
    ...overrides,
  };
}

function jsonOk(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json", "x-correlation-id": "c1" },
  });
}

function jsonError(status: number, code: string, extra: Record<string, unknown> = {}): Response {
  return new Response(
    JSON.stringify({ error: { code, message: `stub ${code}`, correlation_id: "c1" }, ...extra }),
    { status, headers: { "content-type": "application/json", "x-correlation-id": "c1" } },
  );
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
      leads_total: 1, leads_by_status: { new: 1 }, properties_total: 1,
      properties_by_availability: { available: 1 }, conversations_open: 1,
      unread_messages: 1, follow_ups_pending: 1, follow_ups_overdue: 1, leads_by_stage: [],
    },
  },
  "/v1/demo/inbox": {
    ...META, count: 1,
    items: [{
      conversation_id: "conv-1", contact_id: "contact-1", contact_name: "Camila Torres",
      status: "open", unread_count: 1, updated_at: META.as_of,
      last_message: { message_id: "m1", direction: "inbound", body: "hello", created_at: META.as_of },
    }],
  },
  "/v1/demo/leads": { ...META, count: 0, items: [] },
  "/v1/demo/properties": { ...META, count: 0, items: [] },
  "/v1/demo/pipeline": { ...META, pipelines: [] },
  "/v1/demo/agenda": { ...META, counts: { pending: 1, overdue: 1 }, items: [] },
  "/v1/demo/activity": { ...META, count: 0, items: [] },
};

function stubFetch(simulateHandler?: (url: string, init?: RequestInit) => Promise<Response> | Response) {
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const path = new URL(url).pathname;
    calls.push({ method: init?.method ?? "GET", url,
      body: typeof init?.body === "string" ? init.body : undefined });
    if (path === "/v1/demo/simulate") {
      if (simulateHandler) return simulateHandler(url, init);
      const body = simulateBodies.shift() ?? simulateResult();
      return jsonOk(body);
    }
    if (path === "/v1/demo/reset") {
      const forced = new URL(url).searchParams.get("clear_effects") === "true";
      if (!forced) return jsonError(409, "demo_effects_pending", { pending_effects: 12 });
      return jsonOk({ contract_version: "v1", environment: "dev-only-demo-controls",
        reset: true, cleared_effects: 12, account_id: "acct-1", correlation_id: "c1" });
    }
    const body = READ_BODIES[path];
    if (body) return jsonOk(body);
    return jsonError(404, "route_not_found");
  }));
}

beforeEach(() => {
  calls = [];
  simulateBodies = [];
  stubFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function openInbox(): Promise<void> {
  const { default: Page } = await import("./page");
  render(React.createElement(Page));
  await waitFor(() => {
    const nav = document.querySelector('.main-nav a[href="#inbox"]');
    if (!nav) throw new Error("nav not ready");
  });
  fireEvent.click(document.querySelector('.main-nav a[href="#inbox"]') as HTMLElement);
}

describe("WU9 inbox simulator button", () => {
  it("is labeled dev-only in the Inbox and triggers simulate + reload on click", async () => {
    await openInbox();
    const button = await screen.findByRole("button", { name: /Simular mensaje entrante/i });
    const panel = button.closest(".simulator-panel");
    expect(panel?.textContent).toMatch(/solo demo/i);
    expect(panel?.textContent).toMatch(/no Kapso/i);

    fireEvent.click(button);
    await waitFor(() => expect(screen.getByText(/Simulación aplicada · prospecto/i)).toBeTruthy());
    const posts = calls.filter((c) => c.url.includes("/v1/demo/simulate"));
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0].body ?? "{}")).toEqual({});
    await waitFor(() => expect(
      calls.filter((c) => c.url.includes("/v1/demo/dashboard")).length).toBeGreaterThanOrEqual(2));
  });

  it("shows the replay note when the server replays the same canonical event", async () => {
    simulateBodies.push(simulateResult({ replayed: true }));
    await openInbox();
    fireEvent.click(await screen.findByRole("button", { name: /Simular mensaje entrante/i }));
    await waitFor(() => expect(screen.getByText(
      /reproducida \(repetición\): mismo resultado, sin efecto extra/i)).toBeTruthy());
  });

  it("is disabled while a simulation is in flight (no double submit)", async () => {
    let release: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => { release = resolve; });
    stubFetch(() => pending);
    await openInbox();
    const button = await screen.findByRole("button", { name: /Simular mensaje entrante/i });
    fireEvent.click(button);
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(true));
    fireEvent.click(button); // ignored while in flight (guard + disabled)
    expect(calls.filter((c) => c.url.includes("/v1/demo/simulate"))).toHaveLength(1);
    release(jsonOk(simulateResult()));
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    expect(calls.filter((c) => c.url.includes("/v1/demo/simulate"))).toHaveLength(1);
  });

  it("surfaces simulation denial as the envelope note without breaking the page", async () => {
    stubFetch(() => jsonError(403, "tenant_access_denied"));
    await openInbox();
    fireEvent.click(await screen.findByRole("button", { name: /Simular mensaje entrante/i }));
    await waitFor(() => expect(
      screen.getByText(/Simulación falló: 403 · tenant_access_denied/i)).toBeTruthy());
    expect(screen.getByText("Bandeja · 1")).toBeTruthy(); // los datos de la bandeja siguen renderizados
  });

  it("offers the explicit force-reset only after the server blocks with demo_effects_pending", async () => {
    await openInbox();
    expect(screen.queryByText("Forzar reinicio")).toBeNull();
    fireEvent.click(screen.getByText("Reiniciar"));
    await waitFor(() => expect(screen.getByText(/Reinicio bloqueado por/i)).toBeTruthy());
    const force = await screen.findByRole("button", { name: /Forzar reinicio/i });
    fireEvent.click(force);
    await waitFor(() => expect(screen.getByText(/12 efectos limpiados/i)).toBeTruthy());
    const resetCalls = calls.filter((c) => c.url.includes("/v1/demo/reset"));
    expect(resetCalls.map((c) => new URL(c.url).pathname + new URL(c.url).search))
      .toEqual(["/v1/demo/reset", "/v1/demo/reset?clear_effects=true"]);
  });
});

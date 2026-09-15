// @vitest-environment jsdom
/**
 * WU12 audit screen — view behavior tests. Renders the real dashboard page
 * with a stubbed fetch transport: the Auditoría nav must show a table of
 * persisted API rows with Spanish statusLabel translations, the filter form
 * must wire to server-side query params (never an account id), the empty
 * state must render, and "Cargar más" must append the next cursor page
 * without duplicates. Error state must surface the envelope message with
 * correlation. No fixture data: everything asserted comes from stubbed
 * /v1/demo/audit responses.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

process.env.NEXT_PUBLIC_DEMO_API_BASE_URL = "http://api.test";

type Call = { method: string; url: string };
let calls: Call[] = [];

const META = { account_id: "acct-1", as_of: "2026-09-15T12:00:00.000000Z", correlation_id: "c1" };

function auditRow(id: string, overrides: Record<string, unknown> = {}): unknown {
  return {
    audit_id: id,
    action: "crm.property.create",
    outcome: "success",
    resource_type: "property",
    resource_id: "6d100000-0000-0000-0000-0000000000a1",
    actor_id: "10000000-0000-0000-0000-000000000001",
    correlation_id: "corr-row",
    idempotency_key: null,
    reason: null,
    created_at: "2026-09-15T06:00:00.000000Z",
    ...overrides,
  };
}

function auditBody(items: unknown[], hasMore = false, cursor: string | null = null,
                  filters: Record<string, unknown> = {}): unknown {
  return {
    ...META,
    items,
    count: items.length,
    total: items.length,
    limit: 1,
    has_more: hasMore,
    next_cursor: hasMore ? cursor : null,
    filters: {
      contact_id: null, property_id: null, user_id: null,
      action: null, from: null, to: null, ...filters,
    },
  };
}

function jsonOk(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json", "x-correlation-id": "c1" },
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
      properties_by_availability: {}, conversations_open: 0,
      unread_messages: 0, follow_ups_pending: 0, follow_ups_overdue: 0, leads_by_stage: [],
    },
  },
  "/v1/demo/inbox": { ...META, count: 0, items: [] },
  "/v1/demo/leads": { ...META, count: 0, items: [] },
  "/v1/demo/properties": { ...META, count: 0, items: [] },
  "/v1/demo/pipeline": { ...META, pipelines: [] },
  "/v1/demo/agenda": { ...META, counts: { pending: 0, overdue: 0 }, items: [] },
  "/v1/demo/activity": { ...META, count: 0, items: [] },
};

/** Deterministic audit responder: page 1 is replayable (the dashboard loads
 * twice per mount: before and after the demo context resolves); cursor
 * requests consult a dedicated responder. */
let pageOne: () => unknown = () => auditBody([]);
let cursorPages: Record<string, () => unknown> = {};
let auditFailure: { status: number; code: string; correlation: string } | null = null;

function stubFetch(): void {
  calls = [];
  pageOne = () => auditBody([]);
  cursorPages = {};
  auditFailure = null;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const parsed = new URL(url);
    calls.push({ method: init?.method ?? "GET", url });
    if (parsed.pathname === "/v1/demo/audit") {
      if (auditFailure) {
        return new Response(JSON.stringify({
          error: {
            code: auditFailure.code, message: `stub ${auditFailure.code}`,
            correlation_id: auditFailure.correlation,
          },
        }), {
          status: auditFailure.status,
          headers: { "content-type": "application/json",
                     "x-correlation-id": auditFailure.correlation },
        });
      }
      const cursor = parsed.searchParams.get("cursor");
      if (cursor && cursorPages[cursor]) return jsonOk(cursorPages[cursor]());
      return jsonOk(pageOne());
    }
    const body = READ_BODIES[parsed.pathname];
    if (body) return jsonOk(body);
    return new Response(JSON.stringify({
      error: { code: "route_not_found", message: "no stub", correlation_id: "c1" },
    }), { status: 404, headers: { "x-correlation-id": "c1" } });
  }));
}

async function openAuditView(): Promise<void> {
  await waitFor(() => {
    if (!document.querySelector('.main-nav a[href="#auditoria"]'))
      throw new Error("nav not ready");
  });
  fireEvent.click(document.querySelector('.main-nav a[href="#auditoria"]') as HTMLElement);
}

describe("WU12 audit screen view", () => {
  beforeEach(() => stubFetch());
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders API rows with Spanish statusLabel translations and correlation", async () => {
    pageOne = () => auditBody([auditRow("8f-1", {
      action: "crm.property.status_change", outcome: "denied",
      reason: "property_status_transition_not_allowed",
    })]);
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAuditView();
    // translated action + translated outcome via statusLabel; raw reason shown too
    expect(await screen.findByText("Cambio de estado de propiedad")).toBeTruthy();
    expect(screen.getAllByText("denegado").length).toBeGreaterThan(0);
    expect(screen.getByText("property_status_transition_not_allowed")).toBeTruthy();
    expect(screen.getByText("corr-row")).toBeTruthy();
    // redaction: metadata is not part of the contract shape at all
    expect(document.body.textContent).not.toMatch(/metadata/);
  });

  it("wires the filter form to server query params without any account id", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAuditView();
    await screen.findByText(/Sin registros de auditoría/);
    fireEvent.change(screen.getByLabelText("Acción"),
                     { target: { value: "crm.inbound.process" } });
    fireEvent.submit(screen.getByRole("button", { name: "Filtrar" }).closest("form")!);
    await waitFor(() => {
      const auditCalls = calls.filter((c) => c.url.includes("/v1/demo/audit"));
      const last = new URL(auditCalls[auditCalls.length - 1].url);
      expect(last.searchParams.get("action")).toBe("crm.inbound.process");
      for (const c of auditCalls) {
        expect(c.url).not.toMatch(/clientAccountId|client_account_id=/);
      }
    });
  });

  it("shows the empty state when the trail is empty", async () => {
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAuditView();
    expect(await screen.findByText(/Sin registros de auditoría/)).toBeTruthy();
  });

  it("load-more appends the next cursor page without duplicates", async () => {
    pageOne = () => auditBody([auditRow("8f-page1", {
      action: "crm.property.create",
    })], true, "CURSOR-2");
    cursorPages = {
      "CURSOR-2": () => auditBody([auditRow("8f-page2", {
        action: "crm.inbound.process",
      })], false, null),
    };
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAuditView();
    const loadMore = await screen.findByRole("button", { name: /Cargar más/i });
    fireEvent.click(loadMore);
    await waitFor(() => {
      const rows = document.querySelectorAll("tbody tr");
      expect(rows.length).toBe(2);
      const actions = [...rows].map((r) => r.querySelector("td strong")?.textContent);
      expect(actions).toEqual(["Propiedad creada", "Mensaje entrante procesado"]);
      const cursorCall = calls.filter((c) => c.url.includes("cursor=CURSOR-2"));
      expect(cursorCall.length).toBe(1); // exactly one append request
    });
  });

  it("error state surfaces the envelope message with matching correlation", async () => {
    auditFailure = { status: 400, code: "invalid_query", correlation: "corr-audit-err" };
    const { default: DashboardPage } = await import("./page");
    render(React.createElement(DashboardPage));
    await openAuditView();
    expect(await screen.findByText(/corr-audit-err/)).toBeTruthy();
    expect(screen.getByText(/400 · invalid_query/)).toBeTruthy();
  });
});

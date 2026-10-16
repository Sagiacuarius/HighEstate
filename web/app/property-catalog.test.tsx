// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

process.env.NEXT_PUBLIC_DEMO_API_BASE_URL = "http://api.test";
const property = { property_id: "p1", title: "Casa Norte", operation_type: "sale", location: "Calle 1", bedrooms: 2, price: "120000", currency: "USD", availability: "available" };
const meta = { account_id: "a1", as_of: "2026-09-14T12:00:00.000Z", correlation_id: "c1" };
let calls: Array<{ method: string; path: string; body?: string }> = [];
let propertyFailure = false;

function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json", "x-correlation-id": "c1" } });
}

beforeEach(() => {
  calls = [];
  propertyFailure = false;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const path = new URL(input).pathname;
    calls.push({ method: init?.method ?? "GET", path, body: typeof init?.body === "string" ? init.body : undefined });
    if (path === "/v1/demo/context/current") return response({ contract_version: "v1", environment: "dev-only-demo-selector", correlation_id: "c1", user_id: "u1", agency_id: "ag1", client_account_id: "a1", client_account_name: "Cuenta", permissions: [] });
    if (path === "/v1/demo/dashboard") return response({ ...meta, counts: { leads_total: 0, leads_by_status: {}, properties_total: 1, properties_by_availability: { available: 1 }, conversations_open: 0, unread_messages: 0, follow_ups_pending: 0, follow_ups_overdue: 0, leads_by_stage: [] } });
    if (path === "/v1/demo/properties" && (!init?.method || init.method === "GET")) return response({ ...meta, count: 1, items: [property] });
    if (path === "/v1/demo/leads") return response({ ...meta, count: 0, items: [] });
    if (path === "/v1/demo/inbox") return response({ ...meta, count: 0, items: [] });
    if (path === "/v1/demo/pipeline") return response({ ...meta, items: [], count: 0 });
    if (path === "/v1/demo/agenda") return response({ ...meta, counts: { pending: 0, overdue: 0, due_today: 0, completed: 0 }, items: [] });
    if (path === "/v1/demo/activity") return response({ ...meta, total: 0, limit: 20, items: [] });
    if (propertyFailure && path === "/v1/demo/properties" && init?.method === "POST") return response({ error: { code: "invalid_command", message: "invalid property", correlation_id: "c1" } }, 400);
    if (init?.method === "POST" || init?.method === "PUT") return response({ contract_version: "v1", environment: "dev-only-property-catalog", accepted: true, replayed: false, correlation_id: "c1", result: { resource_id: "p1", correlation_id: "c1" } });
    return response({ error: { code: "route_not_found", message: "missing", correlation_id: "c1" } }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openProperties() {
  const { default: Page } = await import("./page");
  render(React.createElement(Page));
  await waitFor(() => expect(document.querySelector('a[href="#properties"]')).toBeTruthy());
  fireEvent.click(document.querySelector('a[href="#properties"]') as HTMLElement);
  await screen.findByText("Propiedades · 1");
}

describe("current property catalog controls", () => {
  it("creates a property and refreshes the catalog", async () => {
    await openProperties();
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Casa Nueva" } });
    fireEvent.change(screen.getByLabelText("Precio"), { target: { value: "200000" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear propiedad" }));
    await waitFor(() => expect(screen.getByText("Propiedad creada")).toBeTruthy());
    expect(calls.some((call) => call.path === "/v1/demo/properties" && call.method === "POST")).toBe(true);
  });

  it("opens edit and sends PUT, while API errors show code and correlation", async () => {
    await openProperties();
    fireEvent.click(screen.getByRole("button", { name: "Editar" }));
    fireEvent.change(screen.getByDisplayValue("Casa Norte"), { target: { value: "Casa Editada" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(screen.getByText("Propiedad actualizada")).toBeTruthy());
    expect(calls.some((call) => call.path === "/v1/demo/properties/p1" && call.method === "PUT")).toBe(true);
  });

  it("shows the API envelope and correlation on create failure", async () => {
    propertyFailure = true;
    await openProperties();
    fireEvent.change(screen.getByLabelText("Título"), { target: { value: "Casa Fallida" } });
    fireEvent.change(screen.getByLabelText("Precio"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear propiedad" }));
    await waitFor(() => expect(screen.getByText(/Crear propiedad falló: 400 · invalid_command · c1/)).toBeTruthy());
  });
});

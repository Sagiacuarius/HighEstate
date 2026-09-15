/**
 * MVP Commercial Demo WU8 — fetch layer + view helper tests.
 * Stub fetcher stands in for the WU8 demo composition; parsers are the same
 * structural validators the dashboard uses, so malformed payloads fail here.
 */
import { describe, expect, it } from "vitest";
import {
  createDemoApi,
  resolveDemoApiBaseUrl,
  buildAuditQuery,
  describeSimulateResult,
  formatDayUtc,
  formatMoney,
  formatTimeUtc,
  initials,
  isResetBlockedError,
  unreadTotal,
  DEMO_API_ENV_VAR,
} from "./demo-api";
import { RuntimeHttpError } from "../runtime-client";

function jsonResponse(body: unknown, status: number, headers: Record<string, string> = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers,
    json: async () => body,
  };
}

const META = { account_id: "a1", as_of: "2026-09-08T12:00:00.000Z", correlation_id: "c1" };

const dashboardPayload = {
  ...META,
  counts: {
    leads_total: 2,
    leads_by_status: { new: 1, qualified: 1 },
    properties_total: 1,
    properties_by_availability: { available: 1 },
    conversations_open: 1,
    unread_messages: 3,
    follow_ups_pending: 1,
    follow_ups_overdue: 0,
    leads_by_stage: [{ stage_id: "s1", stage_name: "Nuevo", position: 0, count: 2 }],
  },
};

describe("resolveDemoApiBaseUrl", () => {
  it("fails fast naming the env var when missing", () => {
    expect(() => resolveDemoApiBaseUrl(undefined)).toThrow(DEMO_API_ENV_VAR);
    expect(() => resolveDemoApiBaseUrl("   ")).toThrow(DEMO_API_ENV_VAR);
  });

  it("accepts http/https URLs and strips trailing slashes", () => {
    expect(resolveDemoApiBaseUrl("http://127.0.0.1:8001/")).toBe("http://127.0.0.1:8001");
  });

  it("rejects non-http schemes", () => {
    expect(() => resolveDemoApiBaseUrl("ftp://x")).toThrow(/http/);
  });
});

describe("demo read fetchers", () => {
  it("parses dashboard counts through the structural parser", async () => {
    const fetcher = async () => jsonResponse(dashboardPayload, 200, { "x-correlation-id": "c1" });
    const api = createDemoApi("http://127.0.0.1:8001");
    const patched = { ...api };
    void patched;
    const { RuntimeHttpClient } = await import("../runtime-client");
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8001", fetcher });
    const { parseCrmDashboardResponse } = await import("../../contracts/runtime-v1");
    const parsed = await client.get("/v1/demo/dashboard", {}, parseCrmDashboardResponse);
    expect(parsed.counts.leads_total).toBe(2);
    expect(parsed.counts.leads_by_stage[0]?.stage_name).toBe("Nuevo");
  });

  it("rejects malformed dashboard payloads instead of casting", async () => {
    const broken = {
      ...dashboardPayload,
      counts: { ...dashboardPayload.counts, leads_total: "many" },
    };
    const fetcher = async () => jsonResponse(broken, 200);
    const { RuntimeHttpClient } = await import("../runtime-client");
    const { parseCrmDashboardResponse } = await import("../../contracts/runtime-v1");
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8001", fetcher });
    await expect(client.get("/v1/demo/dashboard", {}, parseCrmDashboardResponse)).rejects.toThrow(
      /leads_total/,
    );
  });

  it("surfaces backend denials as RuntimeHttpError with the envelope code", async () => {
    const fetcher = async () =>
      jsonResponse(
        { error: { code: "tenant_access_denied", message: "access denied", correlation_id: "c9" } },
        403,
        { "x-correlation-id": "c9" },
      );
    const api = createDemoApi("http://127.0.0.1:8001");
    void api;
    const { RuntimeHttpClient } = await import("../runtime-client");
    const { parseCrmLeadsResponse } = await import("../../contracts/runtime-v1");
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8001", fetcher });
    try {
      await client.get("/v1/demo/leads", {}, parseCrmLeadsResponse);
      throw new Error("expected RuntimeHttpError");
    } catch (error) {
      expect(error).toBeInstanceOf(RuntimeHttpError);
      expect((error as RuntimeHttpError).envelope.error.code).toBe("tenant_access_denied");
    }
  });
});

describe("WU9 simulate layer", () => {
  const simulatePayload = {
    contract_version: "v1",
    environment: "dev-only-simulator",
    accepted: true,
    replayed: false,
    correlation_id: "c1",
    result: {
      resource_id: "lead-1", lead_created: false, contact_id: "k1",
      conversation_id: "conv-1", event_id: "sim-a-1", message_id: "m1",
      previous_stage_id: "s1", current_stage_id: "s2", follow_up_id: "f1",
      received_at: "2026-09-05T12:00:00.000Z", correlation_id: "c1",
    },
  };

  it("posts {} to /v1/demo/simulate and parses the durable result", async () => {
    const seen: { url: string; method: string; body?: string }[] = [];
    const fetcher = async (input: string, init: { headers: Record<string, string>; method?: string; body?: string }) => {
      seen.push({ url: input, method: init.method ?? "GET", body: init.body });
      return { ok: true, status: 200, headers: { "x-correlation-id": "c1" },
        json: async () => simulatePayload };
    };
    const api = createDemoApi("http://127.0.0.1:8001", fetcher);
    const result = await api.simulate({});
    expect(seen[0]?.url).toBe("http://127.0.0.1:8001/v1/demo/simulate");
    expect(seen[0]?.method).toBe("POST");
    expect(JSON.parse(seen[0]?.body ?? "null")).toEqual({});
    expect(result.replayed).toBe(false);
    expect(result.result.current_stage_id).toBe("s2");
  });

  it("rejects malformed simulate responses via the structural parser", async () => {
    const fetcher = async () => ({
      ok: true, status: 200, headers: {},
      json: async () => ({ ...simulatePayload, result: { ...simulatePayload.result, lead_created: "no" } }),
    });
    const api = createDemoApi("http://127.0.0.1:8001", fetcher);
    await expect(api.simulate({})).rejects.toThrow(/lead_created/);
  });

  it("reset(true) requests the effect-clearing path and parses cleared_effects", async () => {
    const seen: string[] = [];
    const fetcher = async (input: string) => {
      seen.push(input);
      return { ok: true, status: 200, headers: {}, json: async () => ({
        contract_version: "v1", environment: "dev-only-demo-controls",
        reset: true, cleared_effects: 7, account_id: "a1", correlation_id: "c1" }) };
    };
    const api = createDemoApi("http://127.0.0.1:8001", fetcher);
    const result = await api.reset({}, true);
    expect(seen[0]).toBe("http://127.0.0.1:8001/v1/demo/reset?clear_effects=true");
    expect(result.cleared_effects).toBe(7);
  });

  it("describeSimulateResult distinguishes apply vs replay", () => {
    const parsed = {
      contract_version: "v1" as const, environment: "dev-only-simulator" as const,
      accepted: true as const, replayed: false,
      correlation_id: "c1" as never,
      result: {
        resource_id: "lead-99abcd", lead_created: false, contact_id: "k", conversation_id: "c",
        event_id: "e", message_id: "m", previous_stage_id: "s1", current_stage_id: "s2",
        follow_up_id: "f", received_at: "t", correlation_id: "c1" as never,
      },
    };
    expect(describeSimulateResult(parsed)).toMatch(/Simulación aplicada · prospecto/);
    expect(describeSimulateResult({ ...parsed, replayed: true })).toMatch(/repetición/);
  });

  it("isResetBlockedError only matches the WU9 409 envelope code", () => {
    const blocked = new RuntimeHttpError(409, { error: {
      code: "demo_effects_pending", message: "m", correlation_id: "c1" as never } });
    const other = new RuntimeHttpError(500, { error: {
      code: "demo_script_failed", message: "m", correlation_id: "c1" as never } });
    expect(isResetBlockedError(blocked)).toBe(true);
    expect(isResetBlockedError(other)).toBe(false);
    expect(isResetBlockedError(new Error("plain"))).toBe(false);
  });
});

describe("view helpers", () => {
  it("initials handles multiword, single and empty names", () => {
    expect(initials("Camila Torres")).toBe("CT");
    expect(initials("  Bruno  Salvatierra ")).toBe("BS");
    expect(initials("HighEstate")).toBe("HI");
    expect(initials("")).toBe("?");
  });

  it("unreadTotal sums unread_count across inbox items", () => {
    expect(
      unreadTotal({
        ...META,
        count: 2,
        items: [
          {
            conversation_id: "c1",
            contact_id: "k1",
            contact_name: "A",
            status: "open",
            unread_count: 3,
            updated_at: META.as_of,
            last_message: null,
          },
          {
            conversation_id: "c2",
            contact_id: "k2",
            contact_name: "B",
            status: "open",
            unread_count: 0,
            updated_at: META.as_of,
            last_message: null,
          },
        ],
      }),
    ).toBe(3);
  });

  it("formatMoney keeps the server string exact with thousands separators", () => {
    expect(formatMoney("185000.00", "USD")).toBe("USD 185,000.00");
    expect(formatMoney("1200", "USD")).toBe("USD 1,200.00");
    expect(formatMoney("no-numérico", "USD")).toBe("USD no-numérico");
  });

  it("UTC formatters are timezone-deterministic", () => {
    expect(formatTimeUtc("2026-09-08T15:04:00.000Z")).toBe("15:04");
    expect(formatDayUtc("2026-09-08T15:04:00.000Z")).toBe("mar 08 sep");
    expect(formatTimeUtc("no-fecha")).toBe("no-fecha");
  });
});

describe("buildAuditQuery (WU12)", () => {
  it("serializes only the whitelisted filters, trimmed, percent-encoded", () => {
    const qs = buildAuditQuery({
      contact_id: " c1 ", property_id: "p1", action: "crm.inbound.process",
      from: "2026-09-01T00:00:00Z", to: "2026-09-14T23:59:59Z", limit: 3,
    });
    const params = new URLSearchParams(qs.replace(/^\?/, ""));
    expect(params.get("contact_id")).toBe("c1");
    expect(params.get("property_id")).toBe("p1");
    expect(params.get("action")).toBe("crm.inbound.process");
    expect(params.get("from")).toBe("2026-09-01T00:00:00Z");
    expect(params.get("to")).toBe("2026-09-14T23:59:59Z");
    expect(params.get("limit")).toBe("3");
    expect(params.get("user_id")).toBeNull();
  });

  it("never serializes an account id, and empty controls are omitted", () => {
    // Claims that must never reach the querystring: blank filters plus two
    // account-id-shaped keys smuggled into the filters object at runtime.
    const smuggled = { contact_id: "", action: "   ", clientAccountId: "acct-1" };
    const qs = buildAuditQuery(smuggled as Parameters<typeof buildAuditQuery>[0]);
    expect(qs).toBe("");
  });

  it("carries cursor for load-more", () => {
    const qs = buildAuditQuery({ limit: 1, cursor: "eyJhIjoxfQ" });
    expect(qs).toContain("cursor=eyJhIjoxfQ");
    expect(qs).toContain("limit=1");
  });
});

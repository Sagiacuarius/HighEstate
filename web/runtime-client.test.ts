import { describe, expect, it } from "vitest";
import { RuntimeHttpClient, RuntimeHttpError, RuntimeHttpClientError, DemoSelectorClient } from "./runtime-client";
import { demoQueryCacheKey, parseDemoContextResponse } from "../contracts/runtime-v1";

function jsonResponse(body: unknown, status: number, headers: Record<string, string> = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers,
    json: async () => body,
  };
}

const validLivePayload = {
  status: "ok",
  service: "production-runtime-api",
  contract_version: "v1",
  correlation_id: "corr-client",
};

describe("RuntimeHttpClient base URL validation", () => {
  it("accepts http and https URLs with a host", () => {
    for (const url of ["http://127.0.0.1:8000", "https://api.example.com/", "HTTP://LOCALHOST:8000"]) {
      expect(() => new RuntimeHttpClient({ baseUrl: url })).not.toThrow();
    }
  });

  it("rejects empty, malformed or non-http schemes", () => {
    expect(() => new RuntimeHttpClient({ baseUrl: "" })).toThrow(RuntimeHttpClientError);
    expect(() => new RuntimeHttpClient({ baseUrl: "   " })).toThrow(RuntimeHttpClientError);
    expect(() => new RuntimeHttpClient({ baseUrl: "not-a-url" })).toThrow(/not a valid URL/);
    expect(() => new RuntimeHttpClient({ baseUrl: "ftp://example.com" })).toThrow(/http or https/);
    expect(() => new RuntimeHttpClient({ baseUrl: "ws://example.com" })).toThrow(/http or https/);
  });

  it("rejects URLs without host", () => {
    // "http://" alone fails URL parsing; path-only URLs parse but have no host.
    expect(() => new RuntimeHttpClient({ baseUrl: "/only/a/path" })).toThrow();
  });

  it("rejects embedded credentials", () => {
    expect(() => new RuntimeHttpClient({ baseUrl: "http://user:pass@example.com" })).toThrow(/credentials/);
    expect(() => new RuntimeHttpClient({ baseUrl: "https://user@example.com" })).toThrow(/credentials/);
  });
});

describe("RuntimeHttpClient requests", () => {
  it("propagates correlation and idempotency headers and validates the payload", async () => {
    const seen: Record<string, Record<string, string>> = {};
    const fetcher = async (input: string, init: { headers: Record<string, string> }) => {
      seen[input] = init.headers;
      return jsonResponse(validLivePayload, 200);
    };
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher });
    const live = await client.live({ correlationId: "corr-client", idempotencyKey: "idem-1" });
    expect(live.correlation_id).toBe("corr-client");
    expect(seen["http://127.0.0.1:8000/health/live"]["X-Correlation-Id"]).toBe("corr-client");
    expect(seen["http://127.0.0.1:8000/health/live"]["X-Idempotency-Key"]).toBe("idem-1");
  });

  it("generates a correlation ID when missing and never an idempotency key", async () => {
    const seenHeaders: Array<Record<string, string>> = [];
    const fetcher = async (_input: string, init: { headers: Record<string, string> }) => {
      seenHeaders.push(init.headers);
      return jsonResponse(validLivePayload, 200);
    };
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher });
    await client.live({});
    expect(seenHeaders[0]["X-Correlation-Id"].startsWith("auto-")).toBe(true);
    expect(seenHeaders[0]["X-Idempotency-Key"]).toBeUndefined();
  });

  it("rejects error responses whose X-Correlation-Id header is missing", async () => {
    const fetcher = async () =>
      jsonResponse(
        { error: { code: "not_found", message: "Resource not found", correlation_id: "corr-404" } },
        404,
        {},
      );
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher });
    await expect(client.ready()).rejects.toThrow(/missing the X-Correlation-Id header/);
  });

  it("rejects error responses whose X-Correlation-Id header is inconsistent", async () => {
    const fetcher = async () =>
      jsonResponse(
        { error: { code: "not_found", message: "Resource not found", correlation_id: "corr-404" } },
        404,
        { "x-correlation-id": "different-id" },
      );
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher });
    try {
      await client.ready();
      throw new Error("expected failure");
    } catch (error) {
      expect(error).toBeInstanceOf(RuntimeHttpClientError);
      expect((error as RuntimeHttpClientError).message).toMatch(/does not match error.correlation_id/);
    }
  });

  it("parses consistent v1 error envelopes into RuntimeHttpError", async () => {
    const fetcher = async () =>
      jsonResponse(
        { error: { code: "shutting_down", message: "Service is shutting down", correlation_id: "corr-503" } },
        503,
        { "x-correlation-id": "corr-503" },
      );
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher });
    try {
      await client.live();
      throw new Error("expected RuntimeHttpError");
    } catch (error) {
      expect(error).toBeInstanceOf(RuntimeHttpError);
      const httpError = error as RuntimeHttpError;
      expect(httpError.status).toBe(503);
      expect(httpError.envelope.error.code).toBe("shutting_down");
      expect(httpError.envelope.error.correlation_id).toBe("corr-503");
    }
  });

  it("rejects malformed success payloads instead of unsafe casts", async () => {
    const fetcher = async () => jsonResponse({ hello: "world" }, 200);
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher });
    await expect(client.live()).rejects.toThrow(/Invalid liveness response|Invalid health payload/);
  });

  it("rejects mismatched success-response correlation headers", async () => {
    const fetcher = async () => jsonResponse(validLivePayload, 200, { "x-correlation-id": "different" });
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher });
    await expect(client.live({ correlationId: "corr-client" })).rejects.toThrow(/does not match the response payload/);
  });

  it("reports a clear failure when an error response has no JSON envelope", async () => {
    const fetcher = async () => ({
      ok: false,
      status: 500,
      headers: {},
      json: async () => {
        throw new Error("no json");
      },
    });
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher });
    await expect(client.live()).rejects.toBeInstanceOf(RuntimeHttpClientError);
  });

  it("validates readiness dependencies structurally and wraps network failures", async () => {
    const payload = { ...validLivePayload, dependencies: {} };
    const okFetcher = async () => jsonResponse(payload, 200);
    const client = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher: okFetcher });
    const ready = await client.ready();
    expect(ready.dependencies).toEqual({});

    const failingFetcher = async () => {
      throw new Error("ECONNREFUSED");
    };
    const failing = new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher: failingFetcher });
    await expect(failing.live()).rejects.toThrow(RuntimeHttpClientError);
  });
});

describe("DemoSelectorClient context selection (WU7)", () => {
  const validContextPayload = {
    contract_version: "v1",
    environment: "dev-only-demo-selector",
    correlation_id: "wu7-select",
    user_id: "10000000-0000-0000-0000-000000000001",
    agency_id: "00000000-0000-0000-0000-000000000001",
    client_account_id: "20000000-0000-0000-0000-000000000001",
    client_account_name: "Aurea Realty North",
    permissions: ["client_account:read", "client_account:write"],
  };

  it("posts the account hint and parses the resolved context projection", async () => {
    let seen: { url: string; method?: string; body?: string } | undefined;
    const fetcher = async (input: string, init: { headers: Record<string, string>; method?: string; body?: string }) => {
      seen = { url: input, method: init.method, body: init.body };
      return jsonResponse(validContextPayload, 200, { "x-correlation-id": "wu7-select" });
    };
    const selector = new DemoSelectorClient(new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher }));
    const resolved = await selector.selectContext("20000000-0000-0000-0000-000000000001", {
      correlationId: "wu7-select",
    });
    expect(seen?.url).toBe("http://127.0.0.1:8000/v1/demo/context/select");
    expect(seen?.method).toBe("POST");
    expect(JSON.parse(seen?.body ?? "{}")).toEqual({
      requestedAccountId: "20000000-0000-0000-0000-000000000001",
    });
    expect(resolved.client_account_id).toBe("20000000-0000-0000-0000-000000000001");
    expect(resolved.permissions).toEqual(["client_account:read", "client_account:write"]);
  });

  it("surfaces selector denials as RuntimeHttpError with the envelope code", async () => {
    const fetcher = async () =>
      jsonResponse(
        { error: { code: "tenant_access_denied", message: "access denied", correlation_id: "wu7-deny" } },
        403,
        { "x-correlation-id": "wu7-deny" },
      );
    const selector = new DemoSelectorClient(new RuntimeHttpClient({ baseUrl: "http://127.0.0.1:8000", fetcher }));
    try {
      await selector.selectContext("20000000-0000-0000-0000-000000000002", { correlationId: "wu7-deny" });
      throw new Error("expected RuntimeHttpError");
    } catch (error) {
      expect(error).toBeInstanceOf(RuntimeHttpError);
      expect((error as RuntimeHttpError).status).toBe(403);
      expect((error as RuntimeHttpError).envelope.error.code).toBe("tenant_access_denied");
    }
  });

  it("rejects selector projections with a wrong environment or version", () => {
    expect(() => parseDemoContextResponse({ ...validContextPayload, environment: "production" })).toThrow(
      /environment/,
    );
    expect(() => parseDemoContextResponse({ ...validContextPayload, contract_version: "v2" })).toThrow(
      /contract version/,
    );
    expect(() => parseDemoContextResponse({ ...validContextPayload, permissions: "read" })).toThrow(/permissions/);
  });
});

describe("demoQueryCacheKey scoping (WU7)", () => {
  const agency = "00000000-0000-0000-0000-000000000001";
  const accountA = "20000000-0000-0000-0000-000000000001";
  const accountB = "20000000-0000-0000-0000-000000000002";

  it("embeds agency + account + surface in the key", () => {
    expect(demoQueryCacheKey(agency, accountA, "dashboard")).toBe(
      `mvp-demo:v1:${agency}:${accountA}:dashboard:`,
    );
  });

  it("partitions accounts: same surface yields different keys per account", () => {
    expect(demoQueryCacheKey(agency, accountA, "inbox")).not.toBe(demoQueryCacheKey(agency, accountB, "inbox"));
  });

  it("rejects blank or unsafe segments instead of building a cross-scope key", () => {
    expect(() => demoQueryCacheKey("", accountA, "dashboard")).toThrow(/agencyId/);
    expect(() => demoQueryCacheKey(agency, " ../x", "dashboard")).toThrow(/clientAccountId/);
    expect(() => demoQueryCacheKey(agency, accountA, "dash board")).toThrow(/surface/);
  });
});

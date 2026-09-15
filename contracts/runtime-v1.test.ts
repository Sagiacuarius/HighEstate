import { describe, expect, it } from "vitest";
import {
  CORRELATION_ID_MAX_LENGTH,
  correlationId,
  correlationIdOrGenerate,
  idempotencyKey,
  makeCorrelationId,
  optionalIdempotencyKey,
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

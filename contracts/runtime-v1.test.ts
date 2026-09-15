import { describe, expect, it } from "vitest";
import {
  CORRELATION_ID_MAX_LENGTH,
  correlationId,
  correlationIdOrGenerate,
  idempotencyKey,
  makeCorrelationId,
  optionalIdempotencyKey,
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

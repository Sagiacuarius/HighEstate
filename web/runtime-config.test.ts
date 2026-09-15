import { describe, expect, it } from "vitest";
import { requireRuntimeApiBaseUrl } from "./runtime-config";

describe("web runtime configuration", () => {
  it("requires an explicit base URL", () => {
    expect(() => requireRuntimeApiBaseUrl(undefined)).toThrow(/RUNTIME_API_BASE_URL/);
    expect(() => requireRuntimeApiBaseUrl("   ")).toThrow(/RUNTIME_API_BASE_URL/);
    expect(requireRuntimeApiBaseUrl("http://127.0.0.1:8000/")).toBe("http://127.0.0.1:8000");
  });

  it("accepts only http/https URLs with a host and no credentials", () => {
    expect(requireRuntimeApiBaseUrl("http://localhost:8000")).toBe("http://localhost:8000");
    expect(requireRuntimeApiBaseUrl("https://api.example.com")).toBe("https://api.example.com");
    expect(() => requireRuntimeApiBaseUrl("not-a-url")).toThrow(/valid URL/);
    expect(() => requireRuntimeApiBaseUrl("ftp://api.example.com")).toThrow(/http or https/);
    expect(() => requireRuntimeApiBaseUrl("/relative/path")).toThrow();
    expect(() => requireRuntimeApiBaseUrl("http://user:pass@example.com")).toThrow(/credentials/);
    expect(() => requireRuntimeApiBaseUrl("https://user@example.com")).toThrow(/credentials/);
  });
});

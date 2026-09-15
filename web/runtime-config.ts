export function requireRuntimeApiBaseUrl(value: string | undefined): string {
  const normalized = value?.trim();
  if (!normalized) throw new Error("Missing required configuration: RUNTIME_API_BASE_URL");

  let parsed: URL;
  try {
    parsed = new URL(normalized);
  } catch {
    throw new Error("RUNTIME_API_BASE_URL must be a valid URL");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("RUNTIME_API_BASE_URL must use http or https");
  }
  if (!parsed.hostname) {
    throw new Error("RUNTIME_API_BASE_URL must include a host");
  }
  if (parsed.username || parsed.password) {
    throw new Error("RUNTIME_API_BASE_URL must not contain embedded credentials");
  }
  return normalized.replace(/\/+$/, "");
}

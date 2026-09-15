import {
  parseErrorEnvelope,
  parseLivenessResponse,
  parseReadinessResponse,
  parseDemoContextResponse,
  demoQueryCacheKey,
  correlationIdOrGenerate,
  optionalIdempotencyKey,
  type CorrelationId,
  type DemoContextResponse,
  type DemoSelectContextBody,
  type ErrorEnvelope,
  type IdempotencyKey,
  type LivenessResponse,
  type ReadinessResponse,
} from "../contracts/runtime-v1";

export type RuntimeHttpCallOptions = {
  correlationId?: string | null;
  idempotencyKey?: string | null;
};

export class RuntimeHttpClientError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options ? { cause: options.cause } : undefined);
    this.name = "RuntimeHttpClientError";
  }
}

/** Thrown for every non-2xx API response after parsing the v1 error envelope. */
export class RuntimeHttpError extends Error {
  constructor(
    readonly status: number,
    readonly envelope: ErrorEnvelope,
  ) {
    super(envelope.error.message);
    this.name = "RuntimeHttpError";
  }
}

export type RuntimeHttpClientOptions = {
  baseUrl: string;
  /** Minimal response shape the client needs; lets tests inject a stub without casting to Response. */
  fetcher?: (input: string, init: { headers: Record<string, string>; method?: string; body?: string }) => Promise<{
    ok: boolean;
    status: number;
    headers: Record<string, string>;
    json: () => Promise<unknown>;
  }>;
};

/** Strict base-url validation using the URL parser. Returns the normalized URL without trailing slashes. */
export function validateBaseUrl(candidate: string): string {
  const trimmed = candidate.trim();
  if (!trimmed) throw new RuntimeHttpClientError("Runtime API base URL is required");
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new RuntimeHttpClientError("Runtime API base URL is not a valid URL");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new RuntimeHttpClientError("Runtime API base URL must use http or https");
  }
  if (!parsed.hostname) throw new RuntimeHttpClientError("Runtime API base URL has no host");
  if (parsed.username || parsed.password) {
    throw new RuntimeHttpClientError("Runtime API base URL must not contain embedded credentials");
  }
  return trimmed.replace(/\/+$/, "");
}

type FetcherLike = NonNullable<RuntimeHttpClientOptions["fetcher"]>;

function defaultFetcher(): FetcherLike {
  return async (input, init) => {
    const response = await fetch(input, init as RequestInit);
    const headerMap: Record<string, string> = {};
    response.headers?.forEach?.((value: string, key: string) => {
      headerMap[key.toLowerCase()] = value;
    });
    return { ok: response.ok, status: response.status, headers: headerMap, json: () => response.json() };
  };
}

export class RuntimeHttpClient {
  private readonly fetcher: FetcherLike;
  private readonly baseUrl: string;

  constructor(options: RuntimeHttpClientOptions) {
    this.baseUrl = validateBaseUrl(options.baseUrl);
    this.fetcher = options.fetcher ?? defaultFetcher();
  }

  private async call<T>(
    path: string,
    options: RuntimeHttpCallOptions,
    parse: (value: unknown) => T & { correlation_id: string },
    init?: { method: string; body: string },
  ): Promise<T> {
    const requestCorrelationId: CorrelationId = correlationIdOrGenerate(options.correlationId);
    const key: IdempotencyKey | undefined = optionalIdempotencyKey(options.idempotencyKey);
    const headers: Record<string, string> = { "X-Correlation-Id": requestCorrelationId };
    if (key) headers["X-Idempotency-Key"] = key;

    let response: Awaited<ReturnType<FetcherLike>>;
    try {
      response = await this.fetcher(`${this.baseUrl}${path}`, {
        headers,
        ...(init ? { method: init.method, body: init.body } : {}),
      });
    } catch (cause: unknown) {
      throw new RuntimeHttpClientError(`Request to ${path} failed`, { cause });
    }

    let payload: unknown = undefined;
    let payloadConsumed = false;

    if (!response.ok) {
      try {
        payload = await response.json();
        payloadConsumed = true;
      } catch {
        throw new RuntimeHttpClientError(`${path} returned HTTP ${response.status} without a JSON error envelope`);
      }
      // The v1 contract requires the echoed X-Correlation-Id to match
      // error.correlation_id; absent or inconsistent headers are rejected.
      const envelope = parseErrorEnvelope(payload);
      const headerCorrelation = response.headers["x-correlation-id"];
      if (!headerCorrelation || !headerCorrelation.trim()) {
        throw new RuntimeHttpClientError(`Error response for ${path} is missing the X-Correlation-Id header`);
      }
      if (headerCorrelation.trim() !== envelope.error.correlation_id.trim()) {
        throw new RuntimeHttpClientError(
          `Error response X-Correlation-Id does not match error.correlation_id (${path})`,
        );
      }
      throw new RuntimeHttpError(response.status, envelope);
    }

    try {
      if (!payloadConsumed) payload = await response.json();
    } catch {
      throw new RuntimeHttpClientError(`${path} returned invalid JSON`);
    }
    const parsed = parse(payload);
    const successHeaderCorrelation = response.headers["x-correlation-id"];
    if (
      typeof successHeaderCorrelation === "string" &&
      successHeaderCorrelation.trim() &&
      successHeaderCorrelation.trim() !== parsed.correlation_id.trim()
    ) {
      throw new RuntimeHttpClientError("Response X-Correlation-Id does not match the response payload");
    }
    return parsed;
  }

  live(options: RuntimeHttpCallOptions = {}): Promise<LivenessResponse> {
    return this.call("/health/live", options, parseLivenessResponse);
  }

  ready(options: RuntimeHttpCallOptions = {}): Promise<ReadinessResponse> {
    return this.call("/health/ready", options, parseReadinessResponse);
  }

  /** POST helper sharing the v1 envelope + correlation contract (used by the demo selector). */
  post<T>(
    path: string,
    body: unknown,
    options: RuntimeHttpCallOptions,
    parse: (value: unknown) => T & { correlation_id: string },
  ): Promise<T> {
    return this.call(path, options, parse, { method: "POST", body: JSON.stringify(body) });
  }

  /** GET helper sharing the v1 envelope + correlation contract (used by the demo read APIs). */
  get<T>(
    path: string,
    options: RuntimeHttpCallOptions,
    parse: (value: unknown) => T & { correlation_id: string },
  ): Promise<T> {
    return this.call(path, options, parse);
  }
}

/**
 * MVP Commercial Demo WU7 — server-owned demo selector client (dev-only).
 * The requested account id is an intent/hint: the server resolves membership
 * and projects the resolved context, or rejects with the v1 error envelope.
 * Query cache keys always embed the server-resolved scope (agency + account).
 */
export class DemoSelectorClient {
  constructor(private readonly runtime: RuntimeHttpClient) {}

  /** Request a context switch; resolves server-side or rejects (prior context untouched). */
  selectContext(requestedAccountId: string, options: RuntimeHttpCallOptions = {}): Promise<DemoContextResponse> {
    const body: DemoSelectContextBody = { requestedAccountId };
    return this.runtime.post("/v1/demo/context/select", body, options, parseDemoContextResponse);
  }

  /** Scoped cache key for a read surface; scope must come from a resolved context. */
  cacheKey(resolved: Pick<DemoContextResponse, "agency_id" | "client_account_id">, surface: string, qualifier = ""): string {
    return demoQueryCacheKey(resolved.agency_id, resolved.client_account_id, surface, qualifier);
  }
}

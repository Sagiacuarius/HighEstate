/**
 * MVP Commercial Demo WU8 — typed fetch layer for the real-data dashboard.
 *
 * Every view reads the versioned WU6 read APIs through the WU8 demo
 * composition (selector-governed session account). Responses are validated
 * with the structural contract parsers — never cast. Account switching goes
 * through DemoSelectorClient (requested id is a hint; the server resolves
 * membership). No fixture data anywhere in this module.
 */
import { requireRuntimeApiBaseUrl } from "../runtime-config";
import {
  RuntimeHttpClient,
  RuntimeHttpError,
  DemoSelectorClient,
  type RuntimeHttpCallOptions,
  type RuntimeHttpClientOptions,
} from "../runtime-client";
import {
  parseAvailabilityResponse,
  parseCalendarCommandResponse,
  parseCalendarEventsResponse,
  parseCrmActivityResponse,
  parseCrmAgendaResponse,
  parseCrmAuditResponse,
  parseCrmDashboardResponse,
  parseCrmInboxResponse,
  parseCrmLeadsResponse,
  parseCrmMessagesResponse,
  parseCrmPipelineResponse,
  parseCrmPropertiesResponse,
  parseDemoContextResponse,
  parseDemoControlsResponse,
  parseDemoSimulateResponse,
  type AvailabilityResponse,
  type CalendarCommandResponse,
  type CalendarEventsResponse,
  type CrmActivityResponse,
  type CrmAgendaResponse,
  type CrmAuditResponse,
  type CrmDashboardResponse,
  type CrmInboxResponse,
  type CrmLeadsResponse,
  type CrmMessagesResponse,
  type CrmPipelineResponse,
  type CrmPropertiesResponse,
  type DemoContextResponse,
  type DemoControlsResponse,
  type DemoSimulateResponse,
} from "../../contracts/runtime-v1";

export const DEMO_API_ENV_VAR = "NEXT_PUBLIC_DEMO_API_BASE_URL";

/** Fail-fast base-URL resolution, evaluated lazily so builds never need the API. */
export function resolveDemoApiBaseUrl(value: string | undefined): string {
  const normalized = value?.trim();
  if (!normalized) {
    throw new Error(`Missing required configuration: ${DEMO_API_ENV_VAR}`);
  }
  return requireRuntimeApiBaseUrl(normalized);
}

export type DemoApi = {
  selectAccount: (requestedAccountId: string, options?: RuntimeHttpCallOptions) => Promise<DemoContextResponse>;
  current: (options?: RuntimeHttpCallOptions) => Promise<DemoContextResponse>;
  dashboard: (options?: RuntimeHttpCallOptions) => Promise<CrmDashboardResponse>;
  inbox: (options?: RuntimeHttpCallOptions) => Promise<CrmInboxResponse>;
  messages: (conversationId: string, options?: RuntimeHttpCallOptions) => Promise<CrmMessagesResponse>;
  leads: (options?: RuntimeHttpCallOptions) => Promise<CrmLeadsResponse>;
  properties: (options?: RuntimeHttpCallOptions) => Promise<CrmPropertiesResponse>;
  pipeline: (options?: RuntimeHttpCallOptions) => Promise<CrmPipelineResponse>;
  agenda: (options?: RuntimeHttpCallOptions) => Promise<CrmAgendaResponse>;
  activity: (options?: RuntimeHttpCallOptions) => Promise<CrmActivityResponse>;
  audit: (filters: AuditFilterState, options?: RuntimeHttpCallOptions) => Promise<CrmAuditResponse>;
  seed: (options?: RuntimeHttpCallOptions) => Promise<DemoControlsResponse>;
  reset: (options?: RuntimeHttpCallOptions, clearEffects?: boolean) => Promise<DemoControlsResponse>;
  simulate: (body: { body?: string; receivedAt?: string; idempotencyKey?: string },
             options?: RuntimeHttpCallOptions) => Promise<DemoSimulateResponse>;
  calendarEvents: (from: string, to: string, includeCancelled?: boolean,
                  options?: RuntimeHttpCallOptions) => Promise<CalendarEventsResponse>;
  calendarAvailability: (from: string, to: string,
                         options?: RuntimeHttpCallOptions) => Promise<AvailabilityResponse>;
  createCalendarEvent: (body: { title: string; startsAt: string; endsAt: string; description?: string;
                                contactId?: string; leadId?: string; propertyId?: string;
                                notes?: string; source?: string; idempotencyKey?: string },
                       options?: RuntimeHttpCallOptions) => Promise<CalendarCommandResponse>;
  updateCalendarEvent: (eventId: string, body: { title?: string; description?: string | null;
                                                 startsAt?: string; endsAt?: string;
                                                 contactId?: string | null; leadId?: string | null;
                                                 propertyId?: string | null; notes?: string | null;
                                                 status?: string; idempotencyKey?: string },
                       options?: RuntimeHttpCallOptions) => Promise<CalendarCommandResponse>;
  cancelCalendarEvent: (eventId: string, idempotencyKey?: string,
                        options?: RuntimeHttpCallOptions) => Promise<CalendarCommandResponse>;
};

export type DemoApiFetcher = NonNullable<RuntimeHttpClientOptions["fetcher"]>;

/** Test seam: the dashboard never injects a fetcher; unit tests stub transport. */
export function createDemoApi(baseUrl: string, fetcher?: DemoApiFetcher): DemoApi {
  const runtime = new RuntimeHttpClient({ baseUrl, ...(fetcher ? { fetcher } : {}) });
  const selector = new DemoSelectorClient(runtime);
  return {
    selectAccount: (requestedAccountId, options) => selector.selectContext(requestedAccountId, options),
    current: (options) => runtime.get("/v1/demo/context/current", options ?? {}, parseDemoContextResponse),
    dashboard: (options) => runtime.get("/v1/demo/dashboard", options ?? {}, parseCrmDashboardResponse),
    inbox: (options) => runtime.get("/v1/demo/inbox", options ?? {}, parseCrmInboxResponse),
    messages: (conversationId, options) =>
      runtime.get(
        `/v1/demo/inbox/${encodeURIComponent(conversationId)}/messages`,
        options ?? {},
        parseCrmMessagesResponse,
      ),
    leads: (options) => runtime.get("/v1/demo/leads", options ?? {}, parseCrmLeadsResponse),
    properties: (options) => runtime.get("/v1/demo/properties", options ?? {}, parseCrmPropertiesResponse),
    pipeline: (options) => runtime.get("/v1/demo/pipeline", options ?? {}, parseCrmPipelineResponse),
    agenda: (options) => runtime.get("/v1/demo/agenda", options ?? {}, parseCrmAgendaResponse),
    activity: (options) => runtime.get("/v1/demo/activity", options ?? {}, parseCrmActivityResponse),
    audit: (filters, options) =>
      runtime.get(`/v1/demo/audit${buildAuditQuery(filters)}`, options ?? {}, parseCrmAuditResponse),
    seed: (options) => runtime.post("/v1/demo/seed", {}, options ?? {}, parseDemoControlsResponse),
    reset: (options, clearEffects) =>
      runtime.post(
        `/v1/demo/reset${clearEffects ? "?clear_effects=true" : ""}`,
        {},
        options ?? {},
        parseDemoControlsResponse,
      ),
    simulate: (body, options) =>
      runtime.post("/v1/demo/simulate", body, options ?? {}, parseDemoSimulateResponse),
    calendarEvents: (from, to, includeCancelled, options) => {
      const params = new URLSearchParams({ from, to });
      if (includeCancelled) params.set("include_cancelled", "true");
      return runtime.get(`/v1/demo/calendar/events?${params.toString()}`, options ?? {}, parseCalendarEventsResponse);
    },
    calendarAvailability: (from, to, options) => {
      const params = new URLSearchParams({ from, to });
      return runtime.get(`/v1/demo/calendar/availability?${params.toString()}`, options ?? {}, parseAvailabilityResponse);
    },
    createCalendarEvent: (body, options) =>
      runtime.post("/v1/demo/calendar/events", body, options ?? {}, parseCalendarCommandResponse),
    updateCalendarEvent: (eventId, body, options) =>
      runtime.put(`/v1/demo/calendar/events/${encodeURIComponent(eventId)}`, body, options ?? {}, parseCalendarCommandResponse),
    cancelCalendarEvent: (eventId, idempotencyKey, options) => {
      const params = new URLSearchParams();
      if (idempotencyKey?.trim()) params.set("idempotencyKey", idempotencyKey.trim());
      const query = params.toString() ? `?${params.toString()}` : "";
      return runtime.delete(`/v1/demo/calendar/events/${encodeURIComponent(eventId)}${query}`, options ?? {}, parseCalendarCommandResponse);
    },
  };
}

/* --- Pure view helpers (deterministic, unit-tested) --- */

/**
 * WU9 Inbox simulator button outcomes. page.tsx owns only the state wiring;
 * the branching below is the testable behavior contract of the button.
 */
export function describeSimulateResult(result: DemoSimulateResponse): string {
  return result.replayed
    ? "Simulación reproducida (repetición): mismo resultado, sin efecto extra"
    : `Simulación aplicada · prospecto ${result.result.resource_id.slice(0, 8)}… · seguimiento creado`;
}

/** True when a reset failed closed because of pending simulator effects (WU9 409). */
export function isResetBlockedError(error: unknown): boolean {
  return error instanceof RuntimeHttpError && error.envelope.error.code === "demo_effects_pending";
}

/** Avatar initials from a display name: "Camila Torres" -> "CT". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return (parts[0].slice(0, 2) || "?").toUpperCase();
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

/** Total unread across inbox items (drives the nav badge — DB-derived). */
export function unreadTotal(inbox: CrmInboxResponse): number {
  return inbox.items.reduce((sum, item) => sum + item.unread_count, 0);
}

/** "185000.00" + "USD" -> "USD 185,000.00" (server string stays exact). */
export function formatMoney(price: string, currency: string): string {
  const amount = Number(price);
  if (!Number.isFinite(amount)) return `${currency} ${price}`;
  return `${currency} ${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** ISO-8601 UTC -> "HH:MM" (UTC, deterministic across timezones). */
export function formatTimeUtc(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}

/** ISO-8601 UTC -> "vie 05 sep" style day label (UTC, Spanish short). */
export function formatDayUtc(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const days = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
  const months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${days[date.getUTCDay()]} ${pad(date.getUTCDate())} ${months[date.getUTCMonth()]}`;
}

/* --- WU12 audit screen helpers (pure, unit-tested) --- */

/** Filter state of the Auditoría view; empty strings mean "no filter". */
export type AuditFilterState = {
  contact_id?: string;
  property_id?: string;
  user_id?: string;
  action?: string;
  from?: string;
  to?: string;
  limit?: number;
  cursor?: string | null;
};

/**
 * Querystring builder for GET /v1/demo/audit. Only the whitelisted filter
 * keys are ever serialized — notably NEVER a client account id (scope is
 * server-derived), and blank controls are omitted so the server default
 * applies. Values are percent-encoded by URLSearchParams.
 */
export function buildAuditQuery(filters: AuditFilterState): string {
  const params = new URLSearchParams();
  const keys = ["contact_id", "property_id", "user_id", "action", "from", "to", "cursor"] as const;
  for (const key of keys) {
    const value = filters[key];
    if (typeof value === "string" && value.trim()) params.set(key, value.trim());
  }
  if (typeof filters.limit === "number" && Number.isFinite(filters.limit)) {
    params.set("limit", String(Math.trunc(filters.limit)));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

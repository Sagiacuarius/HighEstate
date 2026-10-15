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
  parseAgentDraftsResponse,
  parseAgentDraftResponse,
  parseAgentDraftCommandResponse,
  parseAgentPromptPreviewResponse,
  parseAgentPromptResponse,
  parseAgentPromptWriteResponse,
  parseAgentPromptsResponse,
  parseAgentSettingsResponse,
  parseAgentSettingsWriteResponse,
  parseAgentStateResponse,
  parseAvailabilityResponse,
  parseCalendarCommandResponse,
  parseCalendarConfigResponse,
  parseCalendarConfigWriteResponse,
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
  parseKbArticleResponse,
  parseKbArticleWriteResponse,
  parseKbArticlesResponse,
  parseKbSelfTestResponse,
  parseLeadFieldsResponse,
  parseLeadFieldsWriteResponse,
  parseCustomFieldDefinitionsResponse,
  parseCustomFieldValuesResponse,
  parseCustomFieldValuesWriteResponse,
  parseCustomFieldWriteResponse,
  parseContactsByTagResponse,
  parseContactTagsResponse,
  type AgentDraftsResponse,
  type AgentDraftResponse,
  type AgentDraftCommandResponse,
  type AgentPromptPreviewResponse,
  type AgentPromptResponse,
  type AgentPromptWriteResponse,
  type AgentPromptsResponse,
  type AgentSettingsResponse,
  type AgentSettingsWriteResponse,
  type AgentStateResponse,
  type AvailabilityResponse,
  type CalendarCommandResponse,
  type CalendarConfigInput,
  type CalendarConfigResponse,
  type CalendarConfigWriteResponse,
  type CalendarEventsResponse,
  type ConsoleSettingsInput,
  type CreateKbArticleInput,
  type CreatePromptInput,
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
  type KbArticleResponse,
  type KbArticleWriteResponse,
  type KbArticlesResponse,
  type KbSelfTestResponse,
  type LeadFieldsInput,
  type LeadFieldsResponse,
  type LeadFieldsWriteResponse,
  type CreateCustomFieldInput,
  type UpdateCustomFieldInput,
  type SetLeadCustomValuesInput,
  type ContactTagInput,
  type CustomFieldDefinitionsResponse,
  type CustomFieldValuesResponse,
  type CustomFieldValuesWriteResponse,
  type CustomFieldWriteResponse,
  type ContactsByTagResponse,
  type ContactTagsResponse,
  type UpdateKbArticleInput,
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
  agentDrafts: (conversationId?: string, options?: RuntimeHttpCallOptions) => Promise<AgentDraftsResponse>;
  agentDraft: (draftId: string, options?: RuntimeHttpCallOptions) => Promise<AgentDraftResponse>;
  analyzeMessage: (body: string, options?: RuntimeHttpCallOptions) => Promise<AgentDraftCommandResponse>;
  approveDraft: (draftId: string, options?: RuntimeHttpCallOptions) => Promise<AgentDraftCommandResponse>;
  rejectDraft: (draftId: string, options?: RuntimeHttpCallOptions) => Promise<AgentDraftCommandResponse>;
  /** WU15 dev-only agent state: scope is server-derived, never a client account id. */
  getAgentState: (conversationId?: string, options?: RuntimeHttpCallOptions) => Promise<AgentStateResponse>;
  setAgentMode: (mode: AgentModeInput, options?: RuntimeHttpCallOptions) => Promise<AgentStateResponse>;
  resumeHandoff: (options?: RuntimeHttpCallOptions) => Promise<AgentStateResponse>;
  /** WU16 dev-only agent console: scope is server-derived, never a client account id. */
  getConsoleSettings: (options?: RuntimeHttpCallOptions) => Promise<AgentSettingsResponse>;
  updateConsoleSettings: (body: ConsoleSettingsInput, options?: RuntimeHttpCallOptions) => Promise<AgentSettingsWriteResponse>;
  listPrompts: (options?: RuntimeHttpCallOptions) => Promise<AgentPromptsResponse>;
  createPrompt: (body: CreatePromptInput, options?: RuntimeHttpCallOptions) => Promise<AgentPromptWriteResponse>;
  getPromptVersion: (version: number, options?: RuntimeHttpCallOptions) => Promise<AgentPromptResponse>;
  revertPrompt: (version: number, options?: RuntimeHttpCallOptions) => Promise<AgentPromptWriteResponse>;
  previewPrompt: (options?: RuntimeHttpCallOptions) => Promise<AgentPromptPreviewResponse>;
  listKbArticles: (category?: string, options?: RuntimeHttpCallOptions) => Promise<KbArticlesResponse>;
  createKbArticle: (body: CreateKbArticleInput, options?: RuntimeHttpCallOptions) => Promise<KbArticleWriteResponse>;
  updateKbArticle: (articleId: string, body: UpdateKbArticleInput, options?: RuntimeHttpCallOptions) => Promise<KbArticleWriteResponse>;
  deleteKbArticle: (articleId: string, options?: RuntimeHttpCallOptions) => Promise<KbArticleWriteResponse>;
  kbSelfTest: (question: string, options?: RuntimeHttpCallOptions) => Promise<KbSelfTestResponse>;
  getCalendarConfig: (options?: RuntimeHttpCallOptions) => Promise<CalendarConfigResponse>;
  updateCalendarConfig: (body: CalendarConfigInput, options?: RuntimeHttpCallOptions) => Promise<CalendarConfigWriteResponse>;
  getLeadFields: (options?: RuntimeHttpCallOptions) => Promise<LeadFieldsResponse>;
  updateLeadFields: (body: LeadFieldsInput, options?: RuntimeHttpCallOptions) => Promise<LeadFieldsWriteResponse>;
  /** WU17 configuration console: scope is server-derived, never a client account id. */
  listCustomFields: (options?: RuntimeHttpCallOptions) => Promise<CustomFieldDefinitionsResponse>;
  createCustomField: (body: CreateCustomFieldInput, options?: RuntimeHttpCallOptions) => Promise<CustomFieldWriteResponse>;
  updateCustomField: (fieldId: string, body: UpdateCustomFieldInput, options?: RuntimeHttpCallOptions) => Promise<CustomFieldWriteResponse>;
  deleteCustomField: (fieldId: string, options?: RuntimeHttpCallOptions) => Promise<CustomFieldWriteResponse>;
  getLeadCustomValues: (leadId: string, options?: RuntimeHttpCallOptions) => Promise<CustomFieldValuesResponse>;
  setLeadCustomValues: (leadId: string, body: SetLeadCustomValuesInput, options?: RuntimeHttpCallOptions) => Promise<CustomFieldValuesWriteResponse>;
  listContactsByTag: (tag: string, options?: RuntimeHttpCallOptions) => Promise<ContactsByTagResponse>;
  addContactTag: (contactId: string, body: ContactTagInput, options?: RuntimeHttpCallOptions) => Promise<ContactTagsResponse>;
  removeContactTag: (contactId: string, body: ContactTagInput, options?: RuntimeHttpCallOptions) => Promise<ContactTagsResponse>;
};

/** WU15 dev-only mode switch input: paused is handoff-only, never settable here. */
export type AgentModeInput = "manual" | "auto";

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
    agentDrafts: (conversationId, options) => {
      const params = new URLSearchParams();
      if (conversationId?.trim()) params.set("conversation_id", conversationId.trim());
      const query = params.toString() ? `?${params.toString()}` : "";
      return runtime.get(`/v1/demo/agent/drafts${query}`, options ?? {}, parseAgentDraftsResponse);
    },
    agentDraft: (draftId, options) =>
      runtime.get(`/v1/demo/agent/drafts/${encodeURIComponent(draftId)}`, options ?? {}, parseAgentDraftResponse),
    analyzeMessage: (body, options) =>
      runtime.post("/v1/demo/agent/analyze", { body }, options ?? {}, parseAgentDraftCommandResponse),
    approveDraft: (draftId, options) =>
      runtime.post(`/v1/demo/agent/drafts/${encodeURIComponent(draftId)}/approve`, {}, options ?? {}, parseAgentDraftCommandResponse),
    rejectDraft: (draftId, options) =>
      runtime.post(`/v1/demo/agent/drafts/${encodeURIComponent(draftId)}/reject`, {}, options ?? {}, parseAgentDraftCommandResponse),
    getAgentState: (conversationId, options) => {
      const params = new URLSearchParams();
      if (conversationId?.trim()) params.set("conversation_id", conversationId.trim());
      const query = params.toString() ? `?${params.toString()}` : "";
      return runtime.get(`/v1/demo/agent/state${query}`, options ?? {}, parseAgentStateResponse);
    },
    setAgentMode: (mode, options) =>
      runtime.post("/v1/demo/agent/state", { mode }, options ?? {}, parseAgentStateResponse),
    resumeHandoff: (options) =>
      runtime.post("/v1/demo/agent/handoff/resume", {}, options ?? {}, parseAgentStateResponse),
    getConsoleSettings: (options) =>
      runtime.get("/v1/demo/agent/settings", options ?? {}, parseAgentSettingsResponse),
    updateConsoleSettings: (body, options) =>
      runtime.put("/v1/demo/agent/settings", body, options ?? {}, parseAgentSettingsWriteResponse),
    listPrompts: (options) =>
      runtime.get("/v1/demo/agent/prompts", options ?? {}, parseAgentPromptsResponse),
    createPrompt: (body, options) =>
      runtime.post("/v1/demo/agent/prompts", body, options ?? {}, parseAgentPromptWriteResponse),
    getPromptVersion: (version, options) =>
      runtime.get(`/v1/demo/agent/prompts/${encodeURIComponent(String(version))}`, options ?? {}, parseAgentPromptResponse),
    revertPrompt: (version, options) =>
      runtime.post(`/v1/demo/agent/prompts/${encodeURIComponent(String(version))}/revert`, {}, options ?? {}, parseAgentPromptWriteResponse),
    previewPrompt: (options) =>
      runtime.get("/v1/demo/agent/prompts/composed-preview", options ?? {}, parseAgentPromptPreviewResponse),
    listKbArticles: (category, options) => {
      const params = new URLSearchParams();
      if (category?.trim()) params.set("category", category.trim());
      const query = params.toString() ? `?${params.toString()}` : "";
      return runtime.get(`/v1/demo/agent/kb/articles${query}`, options ?? {}, parseKbArticlesResponse);
    },
    createKbArticle: (body, options) =>
      runtime.post("/v1/demo/agent/kb/articles", body, options ?? {}, parseKbArticleWriteResponse),
    updateKbArticle: (articleId, body, options) =>
      runtime.put(`/v1/demo/agent/kb/articles/${encodeURIComponent(articleId)}`, body, options ?? {}, parseKbArticleWriteResponse),
    deleteKbArticle: (articleId, options) =>
      runtime.delete(`/v1/demo/agent/kb/articles/${encodeURIComponent(articleId)}`, options ?? {}, parseKbArticleWriteResponse),
    kbSelfTest: (question, options) =>
      runtime.post("/v1/demo/agent/kb/self-test", { question }, options ?? {}, parseKbSelfTestResponse),
    getCalendarConfig: (options) =>
      runtime.get("/v1/demo/agent/calendar-config", options ?? {}, parseCalendarConfigResponse),
    updateCalendarConfig: (body, options) =>
      runtime.put("/v1/demo/agent/calendar-config", body, options ?? {}, parseCalendarConfigWriteResponse),
    getLeadFields: (options) =>
      runtime.get("/v1/demo/agent/lead-fields", options ?? {}, parseLeadFieldsResponse),
    updateLeadFields: (body, options) =>
      runtime.put("/v1/demo/agent/lead-fields", body, options ?? {}, parseLeadFieldsWriteResponse),
    listCustomFields: (options) =>
      runtime.get("/v1/demo/agent/custom-fields", options ?? {}, parseCustomFieldDefinitionsResponse),
    createCustomField: (body, options) =>
      runtime.post("/v1/demo/agent/custom-fields", body, options ?? {}, parseCustomFieldWriteResponse),
    updateCustomField: (fieldId, body, options) =>
      runtime.put(`/v1/demo/agent/custom-fields/${encodeURIComponent(fieldId)}`, body, options ?? {}, parseCustomFieldWriteResponse),
    deleteCustomField: (fieldId, options) =>
      runtime.delete(`/v1/demo/agent/custom-fields/${encodeURIComponent(fieldId)}`, options ?? {}, parseCustomFieldWriteResponse),
    getLeadCustomValues: (leadId, options) =>
      runtime.get(`/v1/demo/agent/leads/${encodeURIComponent(leadId)}/custom-values`, options ?? {}, parseCustomFieldValuesResponse),
    setLeadCustomValues: (leadId, body, options) =>
      runtime.put(`/v1/demo/agent/leads/${encodeURIComponent(leadId)}/custom-values`, body, options ?? {}, parseCustomFieldValuesWriteResponse),
    listContactsByTag: (tag, options) => {
      const params = new URLSearchParams();
      if (tag.trim()) params.set("tag", tag.trim());
      return runtime.get(`/v1/demo/agent/contacts${params.toString() ? `?${params.toString()}` : ""}`, options ?? {}, parseContactsByTagResponse);
    },
    addContactTag: (contactId, body, options) =>
      runtime.post(`/v1/demo/agent/contacts/${encodeURIComponent(contactId)}/tags`, body, options ?? {}, parseContactTagsResponse),
    removeContactTag: (contactId, body, options) =>
      runtime.delete(`/v1/demo/agent/contacts/${encodeURIComponent(contactId)}/tags`, options ?? {}, parseContactTagsResponse, body),
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

/* --- WU14 conversational agent helpers (pure, unit-tested) --- */

/** Spanish labels for the bounded agent vocabulary (server stays language-neutral). */
export const INTENT_LABELS: Record<string, string> = {
  search_properties: "Búsqueda de propiedades",
  schedule_visit: "Pedido de visita",
  reschedule: "Reprogramar visita",
  ask_question: "Consulta",
  update_preferences: "Actualizar preferencias",
  general: "General",
  handoff: "Derivación a humano",
};

export const ACTION_LABELS: Record<string, string> = {
  create_event: "Crear evento en el calendario",
  reschedule_event: "Reprogramar evento",
  register_interest: "Registrar interés en propiedad",
  create_follow_up: "Crear seguimiento",
  transition_lead: "Avanzar prospecto de etapa",
  send_reply: "Enviar respuesta al lead",
  handoff_to_human: "Derivar a un humano",
};

/** Spanish label for the persisted policy verdict (server stays language-neutral). */
export function policyDecisionLabel(decision: string): string {
  return decision === "auto" ? "decisión automática" : decision === "escalate" ? "derivado" : decision;
}

/** Spanish label for the dev-only agent mode (server stays language-neutral). */
export function agentModeLabel(mode: string): string {
  if (mode === "auto") return "Automático";
  if (mode === "manual") return "Manual";
  if (mode === "paused") return "Pausado";
  return mode;
}

const CRITERIA_VALUE_LABELS: Record<string, Record<string, string>> = {
  operation_type: { sale: "venta", rent: "alquiler", temporary_rent: "temporario" },
  property_type: {
    apartment: "departamento", house: "casa", ph: "PH", land: "terreno",
    office: "oficina", commercial: "local", warehouse: "depósito",
  },
};

/** Criteria chips for a draft card, e.g. "alquiler · Rosario · hasta 150.000". */
export function describeCriteria(criteria: Record<string, unknown>): string[] {
  const chips: string[] = [];
  const op = criteria.operation_type;
  if (typeof op === "string") chips.push(CRITERIA_VALUE_LABELS.operation_type[op] ?? op);
  const pt = criteria.property_type;
  if (typeof pt === "string") chips.push(CRITERIA_VALUE_LABELS.property_type[pt] ?? pt);
  if (typeof criteria.city === "string" && criteria.city.trim()) chips.push(criteria.city.trim());
  const budget = criteria.budget_max;
  if (typeof budget === "number" && Number.isFinite(budget)) {
    chips.push(`hasta ${budget.toLocaleString("en-US")}`);
  }
  const rooms = criteria.rooms_min;
  if (typeof rooms === "number") chips.push(`${rooms} ambiente${rooms === 1 ? "" : "s"}`);
  const baths = criteria.bathrooms_min;
  if (typeof baths === "number") chips.push(`${baths} baño${baths === 1 ? "" : "s"}`);
  if (criteria.parking === true) chips.push("cochera");
  const date = criteria.preferred_date;
  const time = criteria.preferred_time;
  if (typeof date === "string") {
    chips.push(typeof time === "string" ? `visita ${date} ${time}` : `visita ${date}`);
  }
  return chips;
}

/** One-line summary of an executed draft's saga outcome, e.g. "2 aplicadas · 1 denegada". */
export function describeExecution(result: Array<Record<string, unknown>> | null): string | null {
  if (!result || result.length === 0) return null;
  const applied = result.filter((e) => e.outcome === "success" || e.outcome === "replayed").length;
  const denied = result.filter((e) => e.outcome === "denied" || e.outcome === "conflict"
    || e.outcome === "invalid" || e.outcome === "unsupported").length;
  const parts: string[] = [];
  if (applied > 0) parts.push(`${applied} aplicada${applied === 1 ? "" : "s"}`);
  if (denied > 0) parts.push(`${denied} sin aplicar`);
  return parts.join(" · ");
}

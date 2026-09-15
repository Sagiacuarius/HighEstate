export const RUNTIME_CONTRACT_VERSION = "v1" as const;

/** Shared limits — keep identical in api/app.py. */
export const CORRELATION_ID_MAX_LENGTH = 128;
export const IDEMPOTENCY_KEY_MAX_LENGTH = 256;

const HEADER_SAFE_PATTERN = /^[\x21-\x7E]+$/;

function validateHeaderValue(value: string, maxLength: number, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} cannot be empty`);
  if (normalized.length > maxLength) throw new Error(`${label} exceeds ${maxLength} characters`);
  if (!HEADER_SAFE_PATTERN.test(normalized)) throw new Error(`${label} contains invalid characters`);
  return normalized;
}

export type CorrelationId = string & { readonly __brand: "CorrelationId" };
export type IdempotencyKey = string & { readonly __brand: "IdempotencyKey" };

/**
 * Validates a caller-supplied correlation ID.
 * Throws on empty/oversized/invalid values; use makeCorrelationId() when absent.
 */
export function correlationId(value: string): CorrelationId {
  return validateHeaderValue(value, CORRELATION_ID_MAX_LENGTH, "Correlation ID") as CorrelationId;
}

/**
 * Validates a caller-supplied idempotency key.
 * Throws on empty/oversized/invalid values; idempotency is optional per request.
 */
export function idempotencyKey(value: string): IdempotencyKey {
  return validateHeaderValue(value, IDEMPOTENCY_KEY_MAX_LENGTH, "Idempotency key") as IdempotencyKey;
}

/** Returns the validated header value or generates one when missing (correlation only). */
export function correlationIdOrGenerate(value?: string | null): CorrelationId {
  const normalized = value?.trim();
  if (!normalized) return makeCorrelationId();
  return correlationId(normalized);
}

/** Returns the validated key or undefined when absent — never generates idempotency keys. */
export function optionalIdempotencyKey(value?: string | null): IdempotencyKey | undefined {
  const normalized = value?.trim();
  if (!normalized) return undefined;
  return idempotencyKey(normalized);
}

export function makeCorrelationId(): CorrelationId {
  const generated = `auto-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return correlationId(generated);
}

export type ErrorEnvelope = {
  error: {
    code: string;
    message: string;
    correlation_id: CorrelationId;
  };
};

export type LivenessResponse = {
  status: "ok";
  service: string;
  contract_version: typeof RUNTIME_CONTRACT_VERSION;
  correlation_id: CorrelationId;
};

export type ReadinessResponse = LivenessResponse & {
  dependencies: Record<string, "not_checked" | "ok" | "unavailable">;
};

export type RuntimeRequestHeaders = {
  "X-Correlation-Id"?: string;
  "X-Idempotency-Key"?: string;
};

/**
 * MVP Commercial Demo WU4 — dev-only inbound boundary shapes
 * (mirror api/composition_wu4_mvp.py + api/inbound_boundary.py).
 * clientAccountId is a claim, never authority: the server resolves the
 * TenantContext and rejects mismatches with the shared non-enumerating 403.
 */
export type DevInboundMessageBody = {
  eventId: string;
  clientAccountId: string;
  conversationId: string;
  message: {
    id: string;
    conversationId: string;
    clientAccountId: string;
    direction: "inbound";
    body: string;
    receivedAt: string;
  };
};

export type DevInboundAcceptedResponse = {
  accepted: true;
  environment: "dev-only-inbound-boundary";
  event: DevInboundMessageBody;
  correlation_id: CorrelationId;
};

export function parseErrorEnvelope(value: unknown): ErrorEnvelope {
  if (!value || typeof value !== "object") throw new Error("Invalid error envelope");
  const error = (value as { error?: unknown }).error;
  if (!error || typeof error !== "object") throw new Error("Invalid error envelope");
  const candidate = error as Record<string, unknown>;
  if (
    typeof candidate.code !== "string" ||
    !candidate.code ||
    typeof candidate.message !== "string" ||
    typeof candidate.correlation_id !== "string"
  ) {
    throw new Error("Invalid error envelope");
  }
  return { error: { code: candidate.code, message: candidate.message, correlation_id: correlationId(candidate.correlation_id) } };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validateCommonFields(candidate: Record<string, unknown>): void {
  if (candidate.status !== "ok") throw new Error("Invalid health payload status");
  if (typeof candidate.service !== "string" || !candidate.service) throw new Error("Invalid health payload service");
  if (candidate.contract_version !== RUNTIME_CONTRACT_VERSION) throw new Error("Unsupported contract version");
  if (typeof candidate.correlation_id !== "string") throw new Error("Invalid health payload correlation id");
  correlationId(candidate.correlation_id as string);
}

/** Structural validation — replaces unsafe casts as the client's only validation path. */
export function parseLivenessResponse(value: unknown): LivenessResponse {
  if (!isRecord(value)) throw new Error("Invalid liveness response");
  validateCommonFields(value);
  return {
    status: "ok",
    service: value.service as string,
    contract_version: RUNTIME_CONTRACT_VERSION,
    correlation_id: value.correlation_id as CorrelationId,
  };
}

export function parseReadinessResponse(value: unknown): ReadinessResponse {
  if (!isRecord(value)) throw new Error("Invalid readiness response");
  validateCommonFields(value);
  if (!isRecord(value.dependencies)) throw new Error("Invalid readiness dependencies");
  for (const dependencyState of Object.values(value.dependencies)) {
    if (dependencyState !== "not_checked" && dependencyState !== "ok" && dependencyState !== "unavailable") {
      throw new Error("Invalid dependency state");
    }
  }
  return {
    status: "ok",
    service: value.service as string,
    contract_version: RUNTIME_CONTRACT_VERSION,
    correlation_id: value.correlation_id as CorrelationId,
    dependencies: value.dependencies as ReadinessResponse["dependencies"],
  };
}

/**
 * MVP Commercial Demo WU5 — dev-only inbound COMMAND result shapes
 * (mirror api/application/crm_commands_wu5.py + api/composition_wu5_mvp.py).
 * Same route as WU4; the response now carries the durable atomic command
 * outcome. replayed=true means zero extra effects (canonical stored result).
 */
export type DevInboundCommandResult = {
  resource_id: string;
  lead_created: boolean;
  contact_id: string;
  conversation_id: string;
  event_id: string;
  message_id: string;
  previous_stage_id: string;
  current_stage_id: string;
  follow_up_id: string;
  received_at: string;
  correlation_id: string;
};

export type DevInboundCommandResponse = {
  accepted: true;
  replayed: boolean;
  environment: "dev-only-inbound-command";
  result: DevInboundCommandResult;
  correlation_id: CorrelationId;
};

/**
 * MVP Commercial Demo WU6 — versioned tenant-scoped READ API v1 shapes
 * (mirror api/queries/crm_reads.py + api/composition_wu6_mvp.py, prefix /v1/crm).
 * Every success envelope carries the server-derived scope (account_id), the DB
 * clock (as_of) and the request correlation id. Counts are DB-derived.
 */
export type CrmReadMeta = {
  account_id: string;
  as_of: string;
  correlation_id: string;
};

export type CrmDashboardCounts = {
  leads_total: number;
  leads_by_status: Record<string, number>;
  properties_total: number;
  properties_by_availability: Record<string, number>;
  conversations_open: number;
  unread_messages: number;
  follow_ups_pending: number;
  follow_ups_overdue: number;
  leads_by_stage: Array<{ stage_id: string; stage_name: string; position: number; count: number }>;
};

export type CrmDashboardResponse = CrmReadMeta & { counts: CrmDashboardCounts };

export type CrmInboxItem = {
  conversation_id: string;
  contact_id: string;
  contact_name: string;
  status: string;
  unread_count: number;
  updated_at: string;
  last_message: {
    message_id: string;
    direction: "inbound" | "outbound";
    body: string;
    created_at: string;
  } | null;
};

export type CrmInboxResponse = CrmReadMeta & { items: CrmInboxItem[]; count: number };

export type CrmLeadView = {
  lead_id: string;
  contact_id: string;
  contact_name: string;
  pipeline_id: string;
  pipeline_name: string;
  stage_id: string;
  stage_name: string;
  status: string;
  selected_property_id: string | null;
  source: string;
  created_at: string;
  updated_at: string;
};

export type CrmLeadsResponse = CrmReadMeta & { items: CrmLeadView[]; count: number };

export type CrmPropertyView = {
  property_id: string;
  title: string;
  operation_type: "sale" | "rent";
  location: string;
  bedrooms: number;
  price: string;
  currency: string;
  availability: "available" | "reserved" | "unavailable";
};

export type CrmPropertiesResponse = CrmReadMeta & { items: CrmPropertyView[]; count: number };
export type CrmPropertyDetailResponse = CrmReadMeta & CrmPropertyView;

export type CrmPipelineView = {
  pipeline_id: string;
  name: string;
  stages: Array<{ stage_id: string; name: string; position: number; lead_count: number }>;
};

export type CrmPipelineResponse = CrmReadMeta & { items: CrmPipelineView[]; count: number };

export type CrmAgendaItem = {
  follow_up_id: string;
  lead_id: string | null;
  contact_id: string | null;
  contact_name: string;
  title: string;
  due_at: string;
  status: string;
  overdue: boolean;
  due_today: boolean;
};

export type CrmAgendaResponse = CrmReadMeta & {
  items: CrmAgendaItem[];
  counts: { pending: number; overdue: number; due_today: number; completed: number };
};

export type CrmActivityItem = {
  activity_id: string;
  action: string;
  outcome: string;
  resource_type: string;
  resource_id: string | null;
  actor_id: string;
  correlation_id: string;
  idempotency_key: string | null;
  reason: string | null;
  created_at: string;
};

export type CrmActivityResponse = CrmReadMeta & {
  items: CrmActivityItem[];
  total: number;
  limit: number;
};

export type CrmMessagesResponse = CrmReadMeta & {
  conversation_id: string;
  items: Array<{ message_id: string; direction: string; body: string; created_at: string | null }>;
  count: number;
};

/**
 * MVP Commercial Demo WU8 — structural parsers for the versioned read APIs
 * (mirror api/queries/crm_reads.py via api/composition_wu8_mvp.py).
 * The client's only validation path: never trust casts on network payloads.
 */

function reqStr(record: Record<string, unknown>, field: string, label: string): string {
  const value = record[field];
  if (typeof value !== "string" || !value) throw new Error(`Invalid ${label}`);
  return value;
}

function reqNum(record: Record<string, unknown>, field: string, label: string): number {
  const value = record[field];
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`Invalid ${label}`);
  return value;
}

function reqBool(record: Record<string, unknown>, field: string, label: string): boolean {
  const value = record[field];
  if (typeof value !== "boolean") throw new Error(`Invalid ${label}`);
  return value;
}

function optStr(record: Record<string, unknown>, field: string, label: string): string | null {
  const value = record[field];
  if (value === null) return null;
  if (typeof value !== "string") throw new Error(`Invalid ${label}`);
  return value;
}

function reqRecord(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`Invalid ${label}`);
  return value;
}

function reqArray(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`Invalid ${label}`);
  return value;
}

function parseCrmReadMeta(value: unknown, label: string): CrmReadMeta {
  const record = reqRecord(value, label);
  const correlation = reqStr(record, "correlation_id", `${label}.correlation_id`);
  correlationId(correlation);
  return {
    account_id: reqStr(record, "account_id", `${label}.account_id`),
    as_of: reqStr(record, "as_of", `${label}.as_of`),
    correlation_id: correlation as CorrelationId,
  };
}

export function parseCrmDashboardResponse(value: unknown): CrmDashboardResponse {
  const meta = parseCrmReadMeta(value, "dashboard");
  const counts = reqRecord((value as Record<string, unknown>).counts, "dashboard.counts");
  const numMap = (field: string): Record<string, number> => {
    const map = reqRecord(counts[field], `dashboard.counts.${field}`);
    for (const entry of Object.values(map)) {
      if (typeof entry !== "number" || !Number.isFinite(entry)) {
        throw new Error(`Invalid dashboard.counts.${field} entry`);
      }
    }
    return map as Record<string, number>;
  };
  const leadsByStage = reqArray(counts.leads_by_stage, "dashboard.counts.leads_by_stage").map((entry) => {
    const stage = reqRecord(entry, "dashboard stage");
    return {
      stage_id: reqStr(stage, "stage_id", "dashboard stage_id"),
      stage_name: reqStr(stage, "stage_name", "dashboard stage_name"),
      position: reqNum(stage, "position", "dashboard position"),
      count: reqNum(stage, "count", "dashboard count"),
    };
  });
  return {
    ...meta,
    counts: {
      leads_total: reqNum(counts, "leads_total", "dashboard.counts.leads_total"),
      leads_by_status: numMap("leads_by_status"),
      properties_total: reqNum(counts, "properties_total", "dashboard.counts.properties_total"),
      properties_by_availability: numMap("properties_by_availability"),
      conversations_open: reqNum(counts, "conversations_open", "dashboard.counts.conversations_open"),
      unread_messages: reqNum(counts, "unread_messages", "dashboard.counts.unread_messages"),
      follow_ups_pending: reqNum(counts, "follow_ups_pending", "dashboard.counts.follow_ups_pending"),
      follow_ups_overdue: reqNum(counts, "follow_ups_overdue", "dashboard.counts.follow_ups_overdue"),
      leads_by_stage: leadsByStage,
    },
  };
}

export function parseCrmInboxResponse(value: unknown): CrmInboxResponse {
  const meta = parseCrmReadMeta(value, "inbox");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "inbox.items").map((entry) => {
    const item = reqRecord(entry, "inbox item");
    const rawLast = item.last_message;
    let lastMessage: CrmInboxItem["last_message"] = null;
    if (rawLast !== null) {
      const last = reqRecord(rawLast, "inbox last_message");
      const direction = reqStr(last, "direction", "inbox direction");
      if (direction !== "inbound" && direction !== "outbound") throw new Error("Invalid inbox direction");
      lastMessage = {
        message_id: reqStr(last, "message_id", "inbox message_id"),
        direction: direction as NonNullable<CrmInboxItem["last_message"]>["direction"],
        body: reqStr(last, "body", "inbox body"),
        created_at: reqStr(last, "created_at", "inbox created_at"),
      };
    }
    return {
      conversation_id: reqStr(item, "conversation_id", "inbox conversation_id"),
      contact_id: reqStr(item, "contact_id", "inbox contact_id"),
      contact_name: reqStr(item, "contact_name", "inbox contact_name"),
      status: reqStr(item, "status", "inbox status"),
      unread_count: reqNum(item, "unread_count", "inbox unread_count"),
      updated_at: reqStr(item, "updated_at", "inbox updated_at"),
      last_message: lastMessage,
    };
  });
  return { ...meta, items, count: reqNum(record, "count", "inbox.count") };
}

export function parseCrmMessagesResponse(value: unknown): CrmMessagesResponse {
  const meta = parseCrmReadMeta(value, "messages");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "messages.items").map((entry) => {
    const item = reqRecord(entry, "message");
    return {
      message_id: reqStr(item, "message_id", "message message_id"),
      direction: reqStr(item, "direction", "message direction"),
      body: reqStr(item, "body", "message body"),
      created_at: optStr(item, "created_at", "message created_at"),
    };
  });
  return {
    ...meta,
    conversation_id: reqStr(record, "conversation_id", "messages.conversation_id"),
    items,
    count: reqNum(record, "count", "messages.count"),
  };
}

export function parseCrmLeadsResponse(value: unknown): CrmLeadsResponse {
  const meta = parseCrmReadMeta(value, "leads");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "leads.items").map((entry) => {
    const item = reqRecord(entry, "lead");
    return {
      lead_id: reqStr(item, "lead_id", "lead lead_id"),
      contact_id: reqStr(item, "contact_id", "lead contact_id"),
      contact_name: reqStr(item, "contact_name", "lead contact_name"),
      pipeline_id: reqStr(item, "pipeline_id", "lead pipeline_id"),
      pipeline_name: reqStr(item, "pipeline_name", "lead pipeline_name"),
      stage_id: reqStr(item, "stage_id", "lead stage_id"),
      stage_name: reqStr(item, "stage_name", "lead stage_name"),
      status: reqStr(item, "status", "lead status"),
      selected_property_id: optStr(item, "selected_property_id", "lead selected_property_id"),
      source: reqStr(item, "source", "lead source"),
      created_at: reqStr(item, "created_at", "lead created_at"),
      updated_at: reqStr(item, "updated_at", "lead updated_at"),
    };
  });
  return { ...meta, items, count: reqNum(record, "count", "leads.count") };
}

export function parseCrmPropertiesResponse(value: unknown): CrmPropertiesResponse {
  const meta = parseCrmReadMeta(value, "properties");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "properties.items").map((entry) => {
    const item = reqRecord(entry, "property");
    const operation = reqStr(item, "operation_type", "property operation_type");
    if (operation !== "sale" && operation !== "rent") throw new Error("Invalid property operation_type");
    const availability = reqStr(item, "availability", "property availability");
    if (availability !== "available" && availability !== "reserved" && availability !== "unavailable") {
      throw new Error("Invalid property availability");
    }
    return {
      property_id: reqStr(item, "property_id", "property property_id"),
      title: reqStr(item, "title", "property title"),
      operation_type: operation as CrmPropertyView["operation_type"],
      location: reqStr(item, "location", "property location"),
      bedrooms: reqNum(item, "bedrooms", "property bedrooms"),
      price: reqStr(item, "price", "property price"),
      currency: reqStr(item, "currency", "property currency"),
      availability: availability as CrmPropertyView["availability"],
    };
  });
  return { ...meta, items, count: reqNum(record, "count", "properties.count") };
}

export function parseCrmPipelineResponse(value: unknown): CrmPipelineResponse {
  const meta = parseCrmReadMeta(value, "pipeline");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "pipeline.items").map((entry) => {
    const view = reqRecord(entry, "pipeline view");
    const stages = reqArray(view.stages, "pipeline stages").map((stageEntry) => {
      const stage = reqRecord(stageEntry, "pipeline stage");
      return {
        stage_id: reqStr(stage, "stage_id", "pipeline stage_id"),
        name: reqStr(stage, "name", "pipeline stage name"),
        position: reqNum(stage, "position", "pipeline position"),
        lead_count: reqNum(stage, "lead_count", "pipeline lead_count"),
      };
    });
    return {
      pipeline_id: reqStr(view, "pipeline_id", "pipeline pipeline_id"),
      name: reqStr(view, "name", "pipeline name"),
      stages,
    };
  });
  return { ...meta, items, count: reqNum(record, "count", "pipeline.count") };
}

export function parseCrmAgendaResponse(value: unknown): CrmAgendaResponse {
  const meta = parseCrmReadMeta(value, "agenda");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "agenda.items").map((entry) => {
    const item = reqRecord(entry, "agenda item");
    return {
      follow_up_id: reqStr(item, "follow_up_id", "agenda follow_up_id"),
      lead_id: optStr(item, "lead_id", "agenda lead_id"),
      contact_id: optStr(item, "contact_id", "agenda contact_id"),
      contact_name: reqStr(item, "contact_name", "agenda contact_name"),
      title: reqStr(item, "title", "agenda title"),
      due_at: reqStr(item, "due_at", "agenda due_at"),
      status: reqStr(item, "status", "agenda status"),
      overdue: reqBool(item, "overdue", "agenda overdue"),
      due_today: reqBool(item, "due_today", "agenda due_today"),
    };
  });
  const counts = reqRecord(record.counts, "agenda.counts");
  return {
    ...meta,
    items,
    counts: {
      pending: reqNum(counts, "pending", "agenda.counts.pending"),
      overdue: reqNum(counts, "overdue", "agenda.counts.overdue"),
      due_today: reqNum(counts, "due_today", "agenda.counts.due_today"),
      completed: reqNum(counts, "completed", "agenda.counts.completed"),
    },
  };
}

export function parseCrmActivityResponse(value: unknown): CrmActivityResponse {
  const meta = parseCrmReadMeta(value, "activity");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "activity.items").map((entry) => {
    const item = reqRecord(entry, "activity item");
    return {
      activity_id: reqStr(item, "activity_id", "activity activity_id"),
      action: reqStr(item, "action", "activity action"),
      outcome: reqStr(item, "outcome", "activity outcome"),
      resource_type: reqStr(item, "resource_type", "activity resource_type"),
      resource_id: optStr(item, "resource_id", "activity resource_id"),
      actor_id: reqStr(item, "actor_id", "activity actor_id"),
      correlation_id: reqStr(item, "correlation_id", "activity correlation_id"),
      idempotency_key: optStr(item, "idempotency_key", "activity idempotency_key"),
      reason: optStr(item, "reason", "activity reason"),
      created_at: reqStr(item, "created_at", "activity created_at"),
    };
  });
  return {
    ...meta,
    items,
    total: reqNum(record, "total", "activity.total"),
    limit: reqNum(record, "limit", "activity.limit"),
  };
}

/**
 * Audit Screen WU12 — versioned response of GET /v1/demo/audit
 * (mirror api/queries/audit_screen_wu12.py). Read-only exposure of the
 * accepted audit_records trail with filters + keyset cursor. Items NEVER
 * carry metadata: a parser-received item with that key is a hard failure,
 * the client-side backstop of the server-side projection.
 */
export type CrmAuditOutcome = "success" | "denied" | "failure";

export type CrmAuditItem = {
  audit_id: string;
  action: string;
  outcome: CrmAuditOutcome;
  resource_type: string;
  resource_id: string | null;
  actor_id: string | null;
  correlation_id: string;
  idempotency_key: string | null;
  reason: string | null;
  created_at: string;
};

export type CrmAuditFilters = {
  contact_id: string | null;
  property_id: string | null;
  user_id: string | null;
  action: string | null;
  from: string | null;
  to: string | null;
};

export type CrmAuditResponse = CrmReadMeta & {
  items: CrmAuditItem[];
  count: number;
  total: number;
  limit: number;
  has_more: boolean;
  next_cursor: string | null;
  filters: CrmAuditFilters;
};

const AUDIT_OUTCOMES: readonly string[] = ["success", "denied", "failure"];

function reqOutcome(value: unknown, label: string): CrmAuditOutcome {
  if (typeof value !== "string" || !AUDIT_OUTCOMES.includes(value)) {
    throw new Error(`Invalid ${label}`);
  }
  return value as CrmAuditOutcome;
}

export function parseCrmAuditResponse(value: unknown): CrmAuditResponse {
  const meta = parseCrmReadMeta(value, "audit");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "audit.items").map((entry) => {
    const item = reqRecord(entry, "audit item");
    if ("metadata" in item) throw new Error("Invalid audit item: metadata must never be exposed");
    return {
      audit_id: reqStr(item, "audit_id", "audit audit_id"),
      action: reqStr(item, "action", "audit action"),
      outcome: reqOutcome(item.outcome, "audit outcome"),
      resource_type: reqStr(item, "resource_type", "audit resource_type"),
      resource_id: optStr(item, "resource_id", "audit resource_id"),
      actor_id: optStr(item, "actor_id", "audit actor_id"),
      correlation_id: reqStr(item, "correlation_id", "audit correlation_id"),
      idempotency_key: optStr(item, "idempotency_key", "audit idempotency_key"),
      reason: optStr(item, "reason", "audit reason"),
      created_at: reqStr(item, "created_at", "audit created_at"),
    };
  });
  const filtersRaw = reqRecord(record.filters, "audit.filters");
  const filters: CrmAuditFilters = {
    contact_id: optStr(filtersRaw, "contact_id", "audit.filters.contact_id"),
    property_id: optStr(filtersRaw, "property_id", "audit.filters.property_id"),
    user_id: optStr(filtersRaw, "user_id", "audit.filters.user_id"),
    action: optStr(filtersRaw, "action", "audit.filters.action"),
    from: optStr(filtersRaw, "from", "audit.filters.from"),
    to: optStr(filtersRaw, "to", "audit.filters.to"),
  };
  return {
    ...meta,
    items,
    count: reqNum(record, "count", "audit.count"),
    total: reqNum(record, "total", "audit.total"),
    limit: reqNum(record, "limit", "audit.limit"),
    has_more: reqBool(record, "has_more", "audit.has_more"),
    next_cursor: optStr(record, "next_cursor", "audit.next_cursor"),
    filters,
  };
}

/**
 * MVP Commercial Demo WU7 — server-owned demo selector shapes
 * (mirror api/identity/demo_identity.py + api/composition_wu7_mvp.py).
 * requestedAccountId is an intent/hint only; the server resolves membership
 * and projects the resolved context. Denials share the v1 error envelope.
 */
export type DemoSelectContextBody = {
  requestedAccountId: string;
};

export type DemoContextResponse = {
  contract_version: typeof RUNTIME_CONTRACT_VERSION;
  environment: "dev-only-demo-selector";
  correlation_id: CorrelationId;
  user_id: string;
  agency_id: string;
  client_account_id: string;
  client_account_name: string;
  permissions: string[];
};

export type DemoControlsResponse = {
  contract_version: typeof RUNTIME_CONTRACT_VERSION;
  environment: "dev-only-demo-controls";
  seeded?: true;
  reset?: true;
  /** WU9: rows purged by an effect-clearing reset (unmarked simulator effects). */
  cleared_effects?: number;
  account_id: string;
  correlation_id: CorrelationId;
};

/** Structural validation — the client's only validation path for selector projections. */
export function parseDemoContextResponse(value: unknown): DemoContextResponse {
  if (!isRecord(value)) throw new Error("Invalid demo context response");
  if (value.contract_version !== RUNTIME_CONTRACT_VERSION) throw new Error("Unsupported contract version");
  if (value.environment !== "dev-only-demo-selector") throw new Error("Invalid demo context environment");
  for (const field of ["correlation_id", "user_id", "agency_id", "client_account_id", "client_account_name"] as const) {
    if (typeof value[field] !== "string" || !(value[field] as string)) throw new Error(`Invalid demo context ${field}`);
  }
  correlationId(value.correlation_id as string);
  if (!Array.isArray(value.permissions) || !value.permissions.every((p) => typeof p === "string")) {
    throw new Error("Invalid demo context permissions");
  }
  return {
    contract_version: RUNTIME_CONTRACT_VERSION,
    environment: "dev-only-demo-selector",
    correlation_id: value.correlation_id as CorrelationId,
    user_id: value.user_id as string,
    agency_id: value.agency_id as string,
    client_account_id: value.client_account_id as string,
    client_account_name: value.client_account_name as string,
    permissions: [...(value.permissions as string[])],
  };
}

/** Structural validation — the client's only validation path for seed/reset controls. */
export function parseDemoControlsResponse(value: unknown): DemoControlsResponse {
  if (!isRecord(value)) throw new Error("Invalid demo controls response");
  if (value.contract_version !== RUNTIME_CONTRACT_VERSION) throw new Error("Unsupported contract version");
  if (value.environment !== "dev-only-demo-controls") throw new Error("Invalid demo controls environment");
  if (typeof value.account_id !== "string" || !value.account_id) throw new Error("Invalid demo controls account_id");
  if (typeof value.correlation_id !== "string" || !value.correlation_id) {
    throw new Error("Invalid demo controls correlation_id");
  }
  correlationId(value.correlation_id as string);
  const out: DemoControlsResponse = {
    contract_version: RUNTIME_CONTRACT_VERSION,
    environment: "dev-only-demo-controls",
    account_id: value.account_id as string,
    correlation_id: value.correlation_id as CorrelationId,
  };
  if (value.seeded === true) out.seeded = true;
  if (value.reset === true) out.reset = true;
  if (out.seeded !== true && out.reset !== true) throw new Error("Invalid demo controls outcome");
  if (value.cleared_effects !== undefined) {
    out.cleared_effects = reqNum(value, "cleared_effects", "demo controls cleared_effects");
  }
  return out;
}

/**
 * MVP Commercial Demo WU9 — simulator surface contracts (api/routes/simulator_wu9.py).
 * The canonical payload shape mirrors the WU1/WU4 inbound event contract: the
 * client only supplies the optional body/receivedAt overrides; ids and scope
 * are server-owned.
 */
export type DemoSimulateResult = {
  resource_id: string;
  lead_created: boolean;
  contact_id: string;
  conversation_id: string;
  event_id: string;
  message_id: string;
  previous_stage_id: string;
  current_stage_id: string;
  follow_up_id: string;
  received_at: string;
  correlation_id: CorrelationId;
};

export type DemoSimulateResponse = {
  contract_version: typeof RUNTIME_CONTRACT_VERSION;
  environment: "dev-only-simulator";
  accepted: true;
  replayed: boolean;
  correlation_id: CorrelationId;
  result: DemoSimulateResult;
};

export function parseDemoSimulateResponse(value: unknown): DemoSimulateResponse {
  if (!isRecord(value)) throw new Error("Invalid simulate response");
  if (value.contract_version !== RUNTIME_CONTRACT_VERSION) throw new Error("Unsupported contract version");
  if (value.environment !== "dev-only-simulator") throw new Error("Invalid simulate environment");
  if (value.accepted !== true) throw new Error("Invalid simulate accepted flag");
  if (typeof value.replayed !== "boolean") throw new Error("Invalid simulate replayed flag");
  const correlation = reqStr(value, "correlation_id", "simulate.correlation_id");
  correlationId(correlation);
  const result = reqRecord(value.result, "simulate.result");
  const parsedResult: DemoSimulateResult = {
    resource_id: reqStr(result, "resource_id", "simulate.result.resource_id"),
    lead_created: reqBool(result, "lead_created", "simulate.result.lead_created"),
    contact_id: reqStr(result, "contact_id", "simulate.result.contact_id"),
    conversation_id: reqStr(result, "conversation_id", "simulate.result.conversation_id"),
    event_id: reqStr(result, "event_id", "simulate.result.event_id"),
    message_id: reqStr(result, "message_id", "simulate.result.message_id"),
    previous_stage_id: reqStr(result, "previous_stage_id", "simulate.result.previous_stage_id"),
    current_stage_id: reqStr(result, "current_stage_id", "simulate.result.current_stage_id"),
    follow_up_id: reqStr(result, "follow_up_id", "simulate.result.follow_up_id"),
    received_at: reqStr(result, "received_at", "simulate.result.received_at"),
    correlation_id: correlationId(reqStr(result, "correlation_id", "simulate.result.correlation_id")),
  };
  return {
    contract_version: RUNTIME_CONTRACT_VERSION,
    environment: "dev-only-simulator",
    accepted: true,
    replayed: value.replayed,
    correlation_id: correlation as CorrelationId,
    result: parsedResult,
  };
}

/**
 * Scoped query cache key shared with api/queries/demo_cache.py.
 * Format mvp-demo:v1:{agencyId}:{clientAccountId}:{surface}:{qualifier} —
 * the scope is always server-derived; a client must never splice ids from
 * untrusted input into this key.
 */
export function demoQueryCacheKey(
  agencyId: string,
  clientAccountId: string,
  surface: string,
  qualifier = "",
): string {
  for (const [label, segment] of [["agencyId", agencyId], ["clientAccountId", clientAccountId], ["surface", surface]] as const) {
    if (!segment || segment.trim() !== segment || /[\s/:]/.test(segment)) {
      throw new Error(`Invalid demo cache key ${label}`);
    }
  }
  if (/[\s/:]/.test(qualifier)) throw new Error("Invalid demo cache key qualifier");
  return `mvp-demo:v1:${agencyId}:${clientAccountId}:${surface}:${qualifier.trim()}`;
}

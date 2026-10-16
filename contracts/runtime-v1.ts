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

export type PropertyCreateInput = {
  title: string;
  operation_type?: "sale" | "rent" | "temporary_rent";
  status?: "capturing" | "available" | "reserved" | "sold" | "rented" | "paused";
  price_amount: string | number;
  price_currency?: "USD" | "ARS";
  property_type?: "apartment" | "house" | "ph" | "land" | "office" | "commercial" | "warehouse";
  address?: string; neighborhood?: string; city?: string;
  rooms?: number; bathrooms?: number; parking_spaces?: number;
  covered_area_m2?: string | number; total_area_m2?: string | number;
  reference_code?: string; amenity_ids?: string[];
};

export type PropertyUpdateInput = Partial<Omit<PropertyCreateInput, "status" | "amenity_ids">>;
export type PropertyWriteResponse = {
  contract_version: typeof RUNTIME_CONTRACT_VERSION;
  environment: "dev-only-property-catalog";
  accepted: true;
  replayed: boolean;
  correlation_id: CorrelationId;
  result: { resource_id: string; reference_code?: string | null; status?: string; operation_type?: string; changed_fields?: string[]; correlation_id: string };
};

function strictKeys(record: Record<string, unknown>, allowed: readonly string[], label: string): void {
  for (const key of Object.keys(record)) if (!allowed.includes(key)) throw new Error(`Invalid ${label} key: unknown key "${key}"`);
}

export function parsePropertyWriteResponse(value: unknown): PropertyWriteResponse {
  const record = reqRecord(value, "property write response");
  strictKeys(record, ["contract_version", "environment", "accepted", "replayed", "correlation_id", "result"], "property write response");
  if (record.contract_version !== RUNTIME_CONTRACT_VERSION || record.environment !== "dev-only-property-catalog" || record.accepted !== true || typeof record.replayed !== "boolean") throw new Error("Invalid property write response");
  const correlation = reqStr(record, "correlation_id", "property write correlation_id");
  correlationId(correlation);
  const result = reqRecord(record.result, "property write result");
  strictKeys(result, ["resource_id", "reference_code", "status", "operation_type", "changed_fields", "correlation_id"], "property write result");
  const resultCorrelation = reqStr(result, "correlation_id", "property result correlation_id");
  if (resultCorrelation !== correlation) throw new Error("Invalid property result correlation_id");
  if (result.changed_fields !== undefined && (!Array.isArray(result.changed_fields) || result.changed_fields.some((item) => typeof item !== "string"))) throw new Error("Invalid property result changed_fields");
  return { contract_version: RUNTIME_CONTRACT_VERSION, environment: "dev-only-property-catalog", accepted: true, replayed: record.replayed, correlation_id: correlation as CorrelationId, result: result as PropertyWriteResponse["result"] };
}

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
 * Calendar / Agenda WU13 — event + availability shapes
 * (mirror api/queries/calendar_reads_wu13.py + api/routes/calendar_wu13.py).
 */
export type CalendarEventStatus = "scheduled" | "cancelled" | "completed";
export type CalendarEventSource = "manual" | "agent";

export type CalendarEventView = {
  event_id: string;
  title: string;
  description: string | null;
  starts_at: string;
  ends_at: string;
  contact_id: string | null;
  lead_id: string | null;
  property_id: string | null;
  status: CalendarEventStatus;
  source: CalendarEventSource;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type CalendarEventsResponse = CrmReadMeta & {
  items: CalendarEventView[];
  count: number;
  from: string;
  to: string;
  include_cancelled: boolean;
};

export type AvailabilitySlot = {
  starts_at: string;
  ends_at: string;
};

export type AvailabilityResponse = CrmReadMeta & {
  items: AvailabilitySlot[];
  count: number;
  from: string;
  to: string;
};

export type CalendarCommandResult = {
  resource_id: string;
  status: string;
  starts_at?: string;
  ends_at?: string;
  correlation_id: string;
};

export type CalendarCommandResponse = {
  contract_version: typeof RUNTIME_CONTRACT_VERSION;
  environment: "dev-only-calendar";
  accepted: true;
  replayed: boolean;
  correlation_id: CorrelationId;
  result: CalendarCommandResult;
};

function reqCalendarStatus(value: unknown, label: string): CalendarEventStatus {
  if (value === "scheduled" || value === "cancelled" || value === "completed") return value;
  throw new Error(`Invalid ${label}: must be scheduled|cancelled|completed`);
}

function reqCalendarSource(value: unknown, label: string): CalendarEventSource {
  if (value === "manual" || value === "agent") return value;
  throw new Error(`Invalid ${label}: must be manual|agent`);
}

export function parseCalendarEventsResponse(value: unknown): CalendarEventsResponse {
  const meta = parseCrmReadMeta(value, "calendar");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "calendar.items").map((entry) => {
    const item = reqRecord(entry, "calendar item");
    return {
      event_id: reqStr(item, "event_id", "calendar event_id"),
      title: reqStr(item, "title", "calendar title"),
      description: optStr(item, "description", "calendar description"),
      starts_at: reqStr(item, "starts_at", "calendar starts_at"),
      ends_at: reqStr(item, "ends_at", "calendar ends_at"),
      contact_id: optStr(item, "contact_id", "calendar contact_id"),
      lead_id: optStr(item, "lead_id", "calendar lead_id"),
      property_id: optStr(item, "property_id", "calendar property_id"),
      status: reqCalendarStatus(item.status, "calendar status"),
      source: reqCalendarSource(item.source, "calendar source"),
      notes: optStr(item, "notes", "calendar notes"),
      created_at: reqStr(item, "created_at", "calendar created_at"),
      updated_at: reqStr(item, "updated_at", "calendar updated_at"),
    };
  });
  return {
    ...meta,
    items,
    count: reqNum(record, "count", "calendar.count"),
    from: reqStr(record, "from", "calendar.from"),
    to: reqStr(record, "to", "calendar.to"),
    include_cancelled: reqBool(record, "include_cancelled", "calendar.include_cancelled"),
  };
}

export function parseAvailabilityResponse(value: unknown): AvailabilityResponse {
  const meta = parseCrmReadMeta(value, "availability");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "availability.items").map((entry) => {
    const item = reqRecord(entry, "availability item");
    return {
      starts_at: reqStr(item, "starts_at", "availability starts_at"),
      ends_at: reqStr(item, "ends_at", "availability ends_at"),
    };
  });
  return {
    ...meta,
    items,
    count: reqNum(record, "count", "availability.count"),
    from: reqStr(record, "from", "availability.from"),
    to: reqStr(record, "to", "availability.to"),
  };
}

export function parseCalendarCommandResponse(value: unknown): CalendarCommandResponse {
  if (!isRecord(value)) throw new Error("Invalid calendar command response");
  if (value.contract_version !== RUNTIME_CONTRACT_VERSION) throw new Error("Unsupported contract version");
  if (value.environment !== "dev-only-calendar") throw new Error("Invalid calendar environment");
  if (value.accepted !== true) throw new Error("Invalid calendar accepted flag");
  if (typeof value.replayed !== "boolean") throw new Error("Invalid calendar replayed flag");
  const correlation = reqStr(value, "correlation_id", "calendar.correlation_id");
  correlationId(correlation);
  const result = reqRecord(value.result, "calendar.result");
  const parsed: CalendarCommandResult = {
    resource_id: reqStr(result, "resource_id", "calendar.result.resource_id"),
    status: reqStr(result, "status", "calendar.result.status"),
    correlation_id: reqStr(result, "correlation_id", "calendar.result.correlation_id"),
  };
  const startsAt = optStr(result, "starts_at", "calendar.result.starts_at");
  const endsAt = optStr(result, "ends_at", "calendar.result.ends_at");
  if (startsAt !== null) parsed.starts_at = startsAt;
  if (endsAt !== null) parsed.ends_at = endsAt;
  return {
    contract_version: RUNTIME_CONTRACT_VERSION,
    environment: "dev-only-calendar",
    accepted: true,
    replayed: value.replayed,
    correlation_id: correlation as CorrelationId,
    result: parsed,
  };
}

/**
 * Conversational Agent WU14 — draft + command response shapes
 * (mirror api/routes/agent_wu14.py). A draft is the bounded proposal the
 * advisor confirms; criteria/params pass through as validated records, enums
 * and required identifiers are strict.
 */
export type AgentDraftStatus = "proposed" | "approved" | "rejected" | "executed" | "auto";
export type AgentIntent =
  | "search_properties" | "schedule_visit" | "reschedule"
  | "ask_question" | "update_preferences" | "general" | "handoff";
export type AgentActionType =
  | "create_follow_up" | "register_interest" | "create_event"
  | "reschedule_event" | "transition_lead" | "send_reply" | "handoff_to_human";
export type AgentPolicyDecision = "auto" | "escalate";
export type AgentExecutionMode = "human" | "agent_policy";
export type AgentMode = "manual" | "auto" | "paused";

export type AgentProposedAction = {
  type: AgentActionType;
  params: Record<string, unknown>;
};

export type AgentDraft = {
  draft_id: string;
  status: AgentDraftStatus;
  intent: AgentIntent;
  criteria: Record<string, unknown>;
  matched_property_ids: string[];
  proposed_actions: AgentProposedAction[];
  reply_text: string | null;
  model_provider: string;
  model_name: string;
  policy_decision: AgentPolicyDecision;
  execution_mode: AgentExecutionMode;
  conversation_id: string;
  contact_id: string;
  lead_id: string | null;
  created_at: string;
  decided_at: string | null;
  execution_result: Array<Record<string, unknown>> | null;
};

export type AgentDraftCommandResponse = {
  contract_version: typeof RUNTIME_CONTRACT_VERSION;
  environment: "dev-only-agent";
  accepted: true;
  replayed: boolean;
  /** WU15: true when the analyze auto path decided + dispatched + finalized. */
  auto_executed?: boolean;
  correlation_id: CorrelationId;
  draft: AgentDraft;
};

export type AgentDraftsResponse = CrmReadMeta & {
  items: AgentDraft[];
  count: number;
};

export type AgentDraftResponse = CrmReadMeta & {
  draft: AgentDraft;
};

function reqAgentStatus(value: unknown, label: string): AgentDraftStatus {
  if (value === "proposed" || value === "approved" || value === "rejected" || value === "executed" || value === "auto") return value;
  throw new Error(`Invalid ${label}: must be proposed|approved|rejected|executed|auto`);
}

function reqPolicyDecision(value: unknown, label: string): AgentPolicyDecision {
  if (value === "auto" || value === "escalate") return value;
  throw new Error(`Invalid ${label}: must be auto|escalate`);
}

function reqExecutionMode(value: unknown, label: string): AgentExecutionMode {
  if (value === "human" || value === "agent_policy") return value;
  throw new Error(`Invalid ${label}: must be human|agent_policy`);
}

const AGENT_INTENTS: readonly AgentIntent[] = [
  "search_properties", "schedule_visit", "reschedule", "ask_question",
  "update_preferences", "general", "handoff"];
const AGENT_ACTION_TYPES: readonly AgentActionType[] = [
  "create_follow_up", "register_interest", "create_event", "reschedule_event",
  "transition_lead", "send_reply", "handoff_to_human"];

function parseAgentDraft(value: unknown, label: string): AgentDraft {
  const record = reqRecord(value, label);
  const intent = reqStr(record, "intent", `${label}.intent`);
  if (!AGENT_INTENTS.includes(intent as AgentIntent)) {
    throw new Error(`Invalid ${label}.intent: unknown intent "${intent}"`);
  }
  const actions = reqArray(record.proposed_actions, `${label}.proposed_actions`).map((entry) => {
    const action = reqRecord(entry, `${label} action`);
    for (const key of Object.keys(action)) {
      if (key !== "type" && key !== "params") {
        throw new Error(`Invalid ${label} action key: unknown key "${key}"`);
      }
    }
    const type = reqStr(action, "type", `${label} action.type`);
    if (!AGENT_ACTION_TYPES.includes(type as AgentActionType)) {
      throw new Error(`Invalid ${label} action.type: unknown type "${type}"`);
    }
    return { type: type as AgentActionType, params: reqRecord(action.params, `${label} action.params`) };
  });
  const matched = reqArray(record.matched_property_ids, `${label}.matched_property_ids`)
    .map((id) => {
      if (typeof id !== "string") throw new Error(`Invalid ${label}.matched_property_ids entry`);
      return id;
    });
  const execution = record.execution_result === null || record.execution_result === undefined
    ? null
    : reqArray(record.execution_result, `${label}.execution_result`).map((e) => reqRecord(e, `${label} execution entry`));
  const criteria = reqRecord(record.criteria, `${label}.criteria`);
  return {
    draft_id: reqStr(record, "draft_id", `${label}.draft_id`),
    status: reqAgentStatus(record.status, `${label}.status`),
    intent: intent as AgentIntent,
    criteria,
    matched_property_ids: matched,
    proposed_actions: actions,
    reply_text: optStr(record, "reply_text", `${label}.reply_text`),
    model_provider: reqStr(record, "model_provider", `${label}.model_provider`),
    model_name: reqStr(record, "model_name", `${label}.model_name`),
    policy_decision: reqPolicyDecision(record.policy_decision, `${label}.policy_decision`),
    execution_mode: reqExecutionMode(record.execution_mode, `${label}.execution_mode`),
    conversation_id: reqStr(record, "conversation_id", `${label}.conversation_id`),
    contact_id: reqStr(record, "contact_id", `${label}.contact_id`),
    lead_id: optStr(record, "lead_id", `${label}.lead_id`),
    created_at: reqStr(record, "created_at", `${label}.created_at`),
    decided_at: optStr(record, "decided_at", `${label}.decided_at`),
    execution_result: execution,
  };
}

export function parseAgentDraftCommandResponse(value: unknown): AgentDraftCommandResponse {
  if (!isRecord(value)) throw new Error("Invalid agent command response");
  if (value.contract_version !== RUNTIME_CONTRACT_VERSION) throw new Error("Unsupported contract version");
  if (value.environment !== "dev-only-agent") throw new Error("Invalid agent environment");
  if (value.accepted !== true) throw new Error("Invalid agent accepted flag");
  if (typeof value.replayed !== "boolean") throw new Error("Invalid agent replayed flag");
  const correlation = reqStr(value, "correlation_id", "agent.correlation_id");
  correlationId(correlation);
  const out: AgentDraftCommandResponse = {
    contract_version: RUNTIME_CONTRACT_VERSION,
    environment: "dev-only-agent",
    accepted: true,
    replayed: value.replayed,
    correlation_id: correlation as CorrelationId,
    draft: parseAgentDraft(value.draft, "agent.draft"),
  };
  if (value.auto_executed !== undefined) {
    if (typeof value.auto_executed !== "boolean") throw new Error("Invalid agent auto_executed flag");
    out.auto_executed = value.auto_executed;
  }
  return out;
}

export function parseAgentDraftsResponse(value: unknown): AgentDraftsResponse {
  const meta = parseCrmReadMeta(value, "agent drafts");
  const record = value as Record<string, unknown>;
  const items = reqArray(record.items, "agent drafts.items")
    .map((entry, index) => parseAgentDraft(entry, `agent draft[${index}]`));
  return { ...meta, items, count: reqNum(record, "count", "agent drafts.count") };
}

export function parseAgentDraftResponse(value: unknown): AgentDraftResponse {
  const meta = parseCrmReadMeta(value, "agent draft");
  const record = value as Record<string, unknown>;
  return { ...meta, draft: parseAgentDraft(record.draft, "agent.draft") };
}

/**
 * Agent Autonomy WU15 — dev-only agent state surface
 * (mirror api/routes/agent_wu14.py _state_shape: GET /v1/demo/agent/state,
 * POST /v1/demo/agent/state {mode: manual|auto}, POST /v1/demo/agent/handoff/resume).
 * Scope is server-derived; the client never sends account ids. handoff_open is
 * the agent_mode='paused' read.
 */
export type AgentStateResponse = {
  contract_version: typeof RUNTIME_CONTRACT_VERSION;
  environment: "dev-only-agent";
  conversation_id: string;
  agent_mode: AgentMode;
  handoff_open: boolean;
  account_id: string;
  correlation_id: CorrelationId;
};

const AGENT_STATE_KEYS: readonly string[] = [
  "contract_version", "environment", "conversation_id", "agent_mode",
  "handoff_open", "account_id", "correlation_id"];

function reqAgentMode(value: unknown, label: string): AgentMode {
  if (value === "manual" || value === "auto" || value === "paused") return value;
  throw new Error(`Invalid ${label}: must be manual|auto|paused`);
}

export function parseAgentStateResponse(value: unknown): AgentStateResponse {
  if (!isRecord(value)) throw new Error("Invalid agent state response");
  for (const key of Object.keys(value)) {
    if (!AGENT_STATE_KEYS.includes(key)) {
      throw new Error(`Invalid agent state key: unknown key "${key}"`);
    }
  }
  if (value.contract_version !== RUNTIME_CONTRACT_VERSION) throw new Error("Unsupported contract version");
  if (value.environment !== "dev-only-agent") throw new Error("Invalid agent environment");
  const correlation = reqStr(value, "correlation_id", "agent state correlation_id");
  correlationId(correlation);
  const handoffOpen = value.handoff_open;
  if (typeof handoffOpen !== "boolean") throw new Error("Invalid agent state handoff_open");
  return {
    contract_version: RUNTIME_CONTRACT_VERSION,
    environment: "dev-only-agent",
    conversation_id: reqStr(value, "conversation_id", "agent state conversation_id"),
    agent_mode: reqAgentMode(value.agent_mode, "agent state agent_mode"),
    handoff_open: handoffOpen,
    account_id: reqStr(value, "account_id", "agent state account_id"),
    correlation_id: correlation as CorrelationId,
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
 * Agent Console WU16 — dev-only console shapes
 * (mirror api/routes/agent_wu14.py console routes on build_agent_router:
 * GET/PUT /agent/settings, prompts CRUD + /prompts/composed-preview +
 * POST /prompts/:version/revert, kb/articles CRUD + POST /kb/self-test,
 * GET/PUT /agent/calendar-config, GET/PUT /agent/lead-fields).
 * Every success envelope is {contract_version, environment: dev-only-agent,
 * accepted, replayed, correlation_id, <resource>: payload}. Scope is
 * server-derived: request bodies never carry account ids. Unknown keys fail
 * closed (400 server-side, throw client-side).
 */
export const AGENT_CONSOLE_ENVIRONMENT = "dev-only-agent" as const;

/** The 8 CHECK-domain action names of agent_policy_matrix (WU18 adds set_custom_field_value). */
export const AGENT_CONSOLE_ACTION_TYPES: readonly string[] = [
  "send_reply",
  "register_interest",
  "create_follow_up",
  "transition_lead",
  "create_event",
  "reschedule_event",
  "handoff_to_human",
  "set_custom_field_value",
];

export type AgentConsoleVerdict = "auto" | "escalate";

/** The 8-value CHECK domain of knowledge_base_articles.category. */
export const KB_ARTICLE_CATEGORIES: readonly string[] = [
  "expensas",
  "escritura",
  "garantia",
  "documentos",
  "financiacion",
  "amenities",
  "requisitos",
  "visitas",
];

/**
 * The fixed CHECK columns of agent_field_allowlist.column_name. WU17 widens
 * the allowlist to also carry per-account custom field names, so the server is
 * the authority for the read projection and these remain a client-side label
 * reference only (no longer a parse gate).
 */
export const LEAD_FIELD_COLUMNS: readonly string[] = [
  "stage_id",
  "status",
  "selected_property_id",
];

export type AgentPolicyMatrixEntry = {
  action_type: string;
  verdict: AgentConsoleVerdict;
};

export type AgentSettings = {
  global_auto: boolean;
  calendar_auto_enabled: boolean;
  matrix: AgentPolicyMatrixEntry[];
  account_id: string;
};

export type AgentPromptVersion = {
  id: string;
  version: number;
  tone: string;
  instructions: string;
  composed_snapshot: string;
  is_active: boolean;
  created_at: string;
};

export type AgentPromptPreview = {
  version: number;
  composed_text: string;
};

export type AgentKbArticle = {
  id: string;
  category: string;
  question: string;
  keywords: string[];
  answer: string;
  deactivated_at: string | null;
  created_at: string;
};

export type KbSelfTestProbe =
  | { match: false }
  | { match: true; article: { id: string; category: string; question: string }; hits: number };

export type AgentCalendarConfig = {
  window_start: string;
  window_end: string;
  slot_minutes: number;
  working_days: number[];
  destination_label: string | null;
  /** WU17: resolved IANA zone (server fallback to the business constant). */
  timezone: string | null;
  account_id: string;
};

export type AgentLeadFields = {
  allowed: string[];
  account_id: string;
};

type AgentConsoleBase = {
  contract_version: typeof RUNTIME_CONTRACT_VERSION;
  environment: typeof AGENT_CONSOLE_ENVIRONMENT;
  accepted: true;
  replayed: boolean;
  correlation_id: CorrelationId;
};

export type AgentSettingsResponse = AgentConsoleBase & { settings: AgentSettings };
export type AgentPromptsResponse = AgentConsoleBase & {
  prompts: { count: number; items: AgentPromptVersion[] };
};
export type AgentPromptResponse = AgentConsoleBase & { prompt: AgentPromptVersion };
export type AgentPromptPreviewResponse = AgentConsoleBase & { preview: AgentPromptPreview };
export type KbArticlesResponse = AgentConsoleBase & {
  articles: { count: number; items: AgentKbArticle[] };
};
export type KbArticleResponse = AgentConsoleBase & { article: AgentKbArticle };
export type KbSelfTestResponse = AgentConsoleBase & { probe: KbSelfTestProbe };
export type CalendarConfigResponse = AgentConsoleBase & { calendar_config: AgentCalendarConfig };
export type LeadFieldsResponse = AgentConsoleBase & { allowlist: AgentLeadFields };

/**
 * Write responses mirror the command payloads (not the read projections):
 * PUT settings returns {resource_id, global_auto, calendar_auto_enabled,
 * matrix}; prompt create/revert returns {resource_id, version, tone,
 * instructions, composed_snapshot, reverted_from}; KB create returns
 * {resource_id, category, question} while update/deactivate return only
 * {resource_id}; calendar PUT returns the window without account_id;
 * allowlist PUT returns {resource_id, allowed}. The UI re-reads the GET
 * projection after every mutation; these parsers only prove the write
 * envelope round-trips with the same strictness.
 */
export type AgentSettingsWriteResponse = AgentConsoleBase & {
  settings: {
    resource_id: string;
    global_auto: boolean;
    calendar_auto_enabled: boolean;
    matrix: AgentPolicyMatrixEntry[];
  };
};

export type AgentPromptWriteResponse = AgentConsoleBase & {
  prompt: {
    resource_id: string;
    version: number;
    tone: string;
    instructions: string;
    composed_snapshot: string;
    reverted_from: number | null;
  };
};

export type KbArticleWriteResponse = AgentConsoleBase & {
  article: { resource_id: string; category: string | null; question: string | null };
};

export type CalendarConfigWriteResponse = AgentConsoleBase & {
  calendar_config: {
    resource_id: string;
    window_start: string;
    window_end: string;
    slot_minutes: number;
    working_days: number[];
    destination_label: string | null;
    timezone: string | null;
  };
};

export type LeadFieldsWriteResponse = AgentConsoleBase & {
  allowlist: { resource_id: string; allowed: string[] };
};

/** Write inputs — scope is server-derived, so no account id appears here. */
export type ConsoleSettingsInput = {
  global_auto?: boolean;
  calendar_auto_enabled?: boolean;
  matrix?: AgentPolicyMatrixEntry[];
  idempotencyKey?: string;
};

export type CreatePromptInput = {
  tone: string;
  instructions: string;
  idempotencyKey?: string;
};

export type CreateKbArticleInput = {
  category: string;
  question: string;
  keywords: string[];
  answer: string;
  idempotencyKey?: string;
};

export type UpdateKbArticleInput = {
  category?: string;
  question?: string;
  keywords?: string[];
  answer?: string;
  idempotencyKey?: string;
};

export type CalendarConfigInput = {
  window_start?: string;
  window_end?: string;
  slot_minutes?: number;
  working_days?: number[];
  destination_label?: string | null;
  timezone?: string;
  idempotencyKey?: string;
};

export type LeadFieldsInput = {
  allowed: string[];
  idempotencyKey?: string;
};

function parseConsoleEnvelope(value: unknown, resource: string, label: string): {
  correlation: CorrelationId;
  replayed: boolean;
  payload: Record<string, unknown>;
} {
  if (!isRecord(value)) throw new Error(`Invalid ${label} response`);
  const allowed = new Set([
    "contract_version",
    "environment",
    "accepted",
    "replayed",
    "correlation_id",
    resource,
  ]);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  if (value.contract_version !== RUNTIME_CONTRACT_VERSION) throw new Error("Unsupported contract version");
  if (value.environment !== AGENT_CONSOLE_ENVIRONMENT) throw new Error(`Invalid ${label} environment`);
  if (value.accepted !== true) throw new Error(`Invalid ${label} accepted flag`);
  if (typeof value.replayed !== "boolean") throw new Error(`Invalid ${label} replayed flag`);
  const correlation = reqStr(value, "correlation_id", `${label}.correlation_id`);
  correlationId(correlation);
  return {
    correlation: correlation as CorrelationId,
    replayed: value.replayed,
    payload: reqRecord(value[resource], `${label}.${resource}`),
  };
}

function consoleBase(correlation: CorrelationId, replayed: boolean): AgentConsoleBase {
  return {
    contract_version: RUNTIME_CONTRACT_VERSION,
    environment: AGENT_CONSOLE_ENVIRONMENT,
    accepted: true,
    replayed,
    correlation_id: correlation,
  };
}

function reqVerdict(value: unknown, label: string): AgentConsoleVerdict {
  if (value === "auto" || value === "escalate") return value;
  throw new Error(`Invalid ${label}: must be auto|escalate`);
}

function parseMatrixEntry(value: unknown, label: string): AgentPolicyMatrixEntry {
  const record = reqRecord(value, label);
  for (const key of Object.keys(record)) {
    if (key !== "action_type" && key !== "verdict") {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  const action = reqStr(record, "action_type", `${label}.action_type`);
  if (!AGENT_CONSOLE_ACTION_TYPES.includes(action)) {
    throw new Error(`Invalid ${label}.action_type: unknown action "${action}"`);
  }
  return { action_type: action, verdict: reqVerdict(record.verdict, `${label}.verdict`) };
}

export function parseAgentSettingsResponse(value: unknown): AgentSettingsResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "settings", "agent settings");
  for (const key of Object.keys(payload)) {
    if (key !== "global_auto" && key !== "calendar_auto_enabled" && key !== "matrix" && key !== "account_id") {
      throw new Error(`Invalid agent settings key: unknown key "${key}"`);
    }
  }
  const matrix = reqArray(payload.matrix, "agent settings.matrix")
    .map((entry, index) => parseMatrixEntry(entry, `agent settings.matrix[${index}]`));
  return {
    ...consoleBase(correlation, replayed),
    settings: {
      global_auto: reqBool(payload, "global_auto", "agent settings.global_auto"),
      calendar_auto_enabled: reqBool(payload, "calendar_auto_enabled", "agent settings.calendar_auto_enabled"),
      matrix,
      account_id: reqStr(payload, "account_id", "agent settings.account_id"),
    },
  };
}

function parsePromptVersion(value: unknown, label: string): AgentPromptVersion {
  const record = reqRecord(value, label);
  for (const key of Object.keys(record)) {
    if (
      key !== "id" && key !== "version" && key !== "tone" && key !== "instructions" &&
      key !== "composed_snapshot" && key !== "is_active" && key !== "created_at"
    ) {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  // instructions is legitimately "" on the seeded v1 (and any reverted
  // copy of it): empty is valid here, unlike every other required string.
  const instructions = record.instructions;
  if (typeof instructions !== "string") throw new Error(`Invalid ${label}.instructions`);
  return {
    id: reqStr(record, "id", `${label}.id`),
    version: reqNum(record, "version", `${label}.version`),
    tone: reqStr(record, "tone", `${label}.tone`),
    instructions,
    composed_snapshot: reqStr(record, "composed_snapshot", `${label}.composed_snapshot`),
    is_active: reqBool(record, "is_active", `${label}.is_active`),
    created_at: reqStr(record, "created_at", `${label}.created_at`),
  };
}

export function parseAgentPromptsResponse(value: unknown): AgentPromptsResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "prompts", "agent prompts");
  for (const key of Object.keys(payload)) {
    if (key !== "count" && key !== "items") {
      throw new Error(`Invalid agent prompts key: unknown key "${key}"`);
    }
  }
  const items = reqArray(payload.items, "agent prompts.items")
    .map((entry, index) => parsePromptVersion(entry, `agent prompt[${index}]`));
  return {
    ...consoleBase(correlation, replayed),
    prompts: { count: reqNum(payload, "count", "agent prompts.count"), items },
  };
}

export function parseAgentPromptResponse(value: unknown): AgentPromptResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "prompt", "agent prompt");
  return { ...consoleBase(correlation, replayed), prompt: parsePromptVersion(payload, "agent.prompt") };
}

export function parseAgentPromptPreviewResponse(value: unknown): AgentPromptPreviewResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "preview", "agent preview");
  for (const key of Object.keys(payload)) {
    if (key !== "version" && key !== "composed_text") {
      throw new Error(`Invalid agent preview key: unknown key "${key}"`);
    }
  }
  return {
    ...consoleBase(correlation, replayed),
    preview: {
      version: reqNum(payload, "version", "agent preview.version"),
      composed_text: reqStr(payload, "composed_text", "agent preview.composed_text"),
    },
  };
}

function reqStringList(value: unknown, label: string): string[] {
  return reqArray(value, label).map((entry) => {
    if (typeof entry !== "string") throw new Error(`Invalid ${label} entry`);
    return entry;
  });
}

function parseKbArticle(value: unknown, label: string): AgentKbArticle {
  const record = reqRecord(value, label);
  for (const key of Object.keys(record)) {
    if (
      key !== "id" && key !== "category" && key !== "question" && key !== "keywords" &&
      key !== "answer" && key !== "deactivated_at" && key !== "created_at"
    ) {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  const category = reqStr(record, "category", `${label}.category`);
  if (!KB_ARTICLE_CATEGORIES.includes(category)) {
    throw new Error(`Invalid ${label}.category: unknown category "${category}"`);
  }
  return {
    id: reqStr(record, "id", `${label}.id`),
    category,
    question: reqStr(record, "question", `${label}.question`),
    keywords: reqStringList(record.keywords, `${label}.keywords`),
    answer: reqStr(record, "answer", `${label}.answer`),
    deactivated_at: optStr(record, "deactivated_at", `${label}.deactivated_at`),
    created_at: reqStr(record, "created_at", `${label}.created_at`),
  };
}

export function parseKbArticlesResponse(value: unknown): KbArticlesResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "articles", "kb articles");
  for (const key of Object.keys(payload)) {
    if (key !== "count" && key !== "items") {
      throw new Error(`Invalid kb articles key: unknown key "${key}"`);
    }
  }
  const items = reqArray(payload.items, "kb articles.items")
    .map((entry, index) => parseKbArticle(entry, `kb article[${index}]`));
  return {
    ...consoleBase(correlation, replayed),
    articles: { count: reqNum(payload, "count", "kb articles.count"), items },
  };
}

export function parseKbArticleResponse(value: unknown): KbArticleResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "article", "kb article");
  return { ...consoleBase(correlation, replayed), article: parseKbArticle(payload, "kb.article") };
}

export function parseKbSelfTestResponse(value: unknown): KbSelfTestResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "probe", "kb probe");
  const match = payload.match;
  if (typeof match !== "boolean") throw new Error("Invalid kb probe.match");
  if (!match) {
    for (const key of Object.keys(payload)) {
      if (key !== "match") {
        throw new Error(`Invalid kb probe key: unknown key "${key}"`);
      }
    }
    return { ...consoleBase(correlation, replayed), probe: { match: false } };
  }
  for (const key of Object.keys(payload)) {
    if (key !== "match" && key !== "article" && key !== "hits") {
      throw new Error(`Invalid kb probe key: unknown key "${key}"`);
    }
  }
  const article = reqRecord(payload.article, "kb probe.article");
  for (const key of Object.keys(article)) {
    if (key !== "id" && key !== "category" && key !== "question") {
      throw new Error(`Invalid kb probe.article key: unknown key "${key}"`);
    }
  }
  const category = reqStr(article, "category", "kb probe.article.category");
  if (!KB_ARTICLE_CATEGORIES.includes(category)) {
    throw new Error(`Invalid kb probe.article.category: unknown category "${category}"`);
  }
  return {
    ...consoleBase(correlation, replayed),
    probe: {
      match: true,
      article: {
        id: reqStr(article, "id", "kb probe.article.id"),
        category,
        question: reqStr(article, "question", "kb probe.article.question"),
      },
      hits: reqNum(payload, "hits", "kb probe.hits"),
    },
  };
}

const WINDOW_PATTERN = /^\d{2}:\d{2}$/;

function reqWindow(value: unknown, label: string): string {
  if (typeof value !== "string" || !WINDOW_PATTERN.test(value)) {
    throw new Error(`Invalid ${label}: must be HH:MM`);
  }
  return value;
}

function optTimezone(value: unknown, label: string): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`Invalid ${label}: timezone must be a non-empty string`);
  }
  return value;
}

export function parseCalendarConfigResponse(value: unknown): CalendarConfigResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "calendar_config", "calendar config");
  for (const key of Object.keys(payload)) {
    if (
      key !== "window_start" && key !== "window_end" && key !== "slot_minutes" &&
      key !== "working_days" && key !== "destination_label" && key !== "timezone" &&
      key !== "account_id"
    ) {
      throw new Error(`Invalid calendar config key: unknown key "${key}"`);
    }
  }
  const days = reqArray(payload.working_days, "calendar config.working_days").map((day) => {
    if (typeof day !== "number" || !Number.isInteger(day) || day < 0 || day > 6) {
      throw new Error("Invalid calendar config.working_days entry: must be 0-6");
    }
    return day;
  });
  const slot = reqNum(payload, "slot_minutes", "calendar config.slot_minutes");
  if (!Number.isInteger(slot) || slot <= 0) {
    throw new Error("Invalid calendar config.slot_minutes: must be a positive integer");
  }
  return {
    ...consoleBase(correlation, replayed),
    calendar_config: {
      window_start: reqWindow(payload.window_start, "calendar config.window_start"),
      window_end: reqWindow(payload.window_end, "calendar config.window_end"),
      slot_minutes: slot,
      working_days: days,
      destination_label: optStr(payload, "destination_label", "calendar config.destination_label"),
      timezone: optTimezone(payload.timezone, "calendar config.timezone"),
      account_id: reqStr(payload, "account_id", "calendar config.account_id"),
    },
  };
}

export function parseLeadFieldsResponse(value: unknown): LeadFieldsResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "allowlist", "lead fields");
  for (const key of Object.keys(payload)) {
    if (key !== "allowed" && key !== "account_id") {
      throw new Error(`Invalid lead fields key: unknown key "${key}"`);
    }
  }
  const allowed = parseAllowedColumns(payload.allowed, "lead fields.allowed");
  return {
    ...consoleBase(correlation, replayed),
    allowlist: {
      allowed,
      account_id: reqStr(payload, "account_id", "lead fields.account_id"),
    },
  };
}

function optAbsentStr(record: Record<string, unknown>, field: string, label: string): string | null {
  const value = record[field];
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") throw new Error(`Invalid ${label}`);
  return value;
}

function parseWorkingDays(value: unknown, label: string): number[] {
  return reqArray(value, label).map((day) => {
    if (typeof day !== "number" || !Number.isInteger(day) || day < 0 || day > 6) {
      throw new Error(`Invalid ${label} entry: must be 0-6`);
    }
    return day;
  });
}

function parseSlotMinutes(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || (value as number) <= 0) {
    throw new Error(`Invalid ${label}: must be a positive integer`);
  }
  return value as number;
}

function parseAllowedColumns(value: unknown, label: string): string[] {
  const allowed = reqStringList(value, label);
  for (const column of allowed) {
    if (!column.trim()) {
      throw new Error(`Invalid ${label} entry: column name must be a non-empty string`);
    }
  }
  return allowed;
}

export function parseAgentSettingsWriteResponse(value: unknown): AgentSettingsWriteResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "settings", "agent settings write");
  for (const key of Object.keys(payload)) {
    if (key !== "resource_id" && key !== "global_auto" && key !== "calendar_auto_enabled" && key !== "matrix" && key !== "correlation_id") {
      throw new Error(`Invalid agent settings write key: unknown key "${key}"`);
    }
  }
  const matrix = reqArray(payload.matrix, "agent settings write.matrix")
    .map((entry, index) => parseMatrixEntry(entry, `agent settings write.matrix[${index}]`));
  return {
    ...consoleBase(correlation, replayed),
    settings: {
      resource_id: reqStr(payload, "resource_id", "agent settings write.resource_id"),
      global_auto: reqBool(payload, "global_auto", "agent settings write.global_auto"),
      calendar_auto_enabled: reqBool(payload, "calendar_auto_enabled", "agent settings write.calendar_auto_enabled"),
      matrix,
    },
  };
}

export function parseAgentPromptWriteResponse(value: unknown): AgentPromptWriteResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "prompt", "agent prompt write");
  for (const key of Object.keys(payload)) {
    if (
      key !== "resource_id" && key !== "version" && key !== "tone" && key !== "instructions" &&
      key !== "composed_snapshot" && key !== "reverted_from" && key !== "correlation_id"
    ) {
      throw new Error(`Invalid agent prompt write key: unknown key "${key}"`);
    }
  }
  const reverted = payload.reverted_from;
  if (reverted !== null && reverted !== undefined && (typeof reverted !== "number" || !Number.isInteger(reverted))) {
    throw new Error("Invalid agent prompt write.reverted_from");
  }
  // instructions may be "" (seeded v1 and its reverted copies): valid here.
  const writeInstructions = payload.instructions;
  if (typeof writeInstructions !== "string") throw new Error("Invalid agent prompt write.instructions");
  return {
    ...consoleBase(correlation, replayed),
    prompt: {
      resource_id: reqStr(payload, "resource_id", "agent prompt write.resource_id"),
      version: reqNum(payload, "version", "agent prompt write.version"),
      tone: reqStr(payload, "tone", "agent prompt write.tone"),
      instructions: writeInstructions,
      composed_snapshot: reqStr(payload, "composed_snapshot", "agent prompt write.composed_snapshot"),
      reverted_from: (reverted ?? null) as number | null,
    },
  };
}

export function parseKbArticleWriteResponse(value: unknown): KbArticleWriteResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "article", "kb article write");
  for (const key of Object.keys(payload)) {
    if (key !== "resource_id" && key !== "category" && key !== "question" && key !== "correlation_id") {
      throw new Error(`Invalid kb article write key: unknown key "${key}"`);
    }
  }
  const category = optAbsentStr(payload, "category", "kb article write.category");
  if (category !== null && !KB_ARTICLE_CATEGORIES.includes(category)) {
    throw new Error(`Invalid kb article write.category: unknown category "${category}"`);
  }
  return {
    ...consoleBase(correlation, replayed),
    article: {
      resource_id: reqStr(payload, "resource_id", "kb article write.resource_id"),
      category,
      question: optAbsentStr(payload, "question", "kb article write.question"),
    },
  };
}

export function parseCalendarConfigWriteResponse(value: unknown): CalendarConfigWriteResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "calendar_config", "calendar config write");
  for (const key of Object.keys(payload)) {
    if (
      key !== "resource_id" && key !== "window_start" && key !== "window_end" &&
      key !== "slot_minutes" && key !== "working_days" && key !== "destination_label" &&
      key !== "timezone" && key !== "correlation_id"
    ) {
      throw new Error(`Invalid calendar config write key: unknown key "${key}"`);
    }
  }
  return {
    ...consoleBase(correlation, replayed),
    calendar_config: {
      resource_id: reqStr(payload, "resource_id", "calendar config write.resource_id"),
      window_start: reqWindow(payload.window_start, "calendar config write.window_start"),
      window_end: reqWindow(payload.window_end, "calendar config write.window_end"),
      slot_minutes: parseSlotMinutes(payload.slot_minutes, "calendar config write.slot_minutes"),
      working_days: parseWorkingDays(payload.working_days, "calendar config write.working_days"),
      destination_label: optAbsentStr(payload, "destination_label", "calendar config write.destination_label"),
      timezone: optTimezone(payload.timezone, "calendar config write.timezone"),
    },
  };
}

export function parseLeadFieldsWriteResponse(value: unknown): LeadFieldsWriteResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "allowlist", "lead fields write");
  for (const key of Object.keys(payload)) {
    if (key !== "resource_id" && key !== "allowed" && key !== "correlation_id") {
      throw new Error(`Invalid lead fields write key: unknown key "${key}"`);
    }
  }
  return {
    ...consoleBase(correlation, replayed),
    allowlist: {
      resource_id: reqStr(payload, "resource_id", "lead fields write.resource_id"),
      allowed: parseAllowedColumns(payload.allowed, "lead fields write.allowed"),
    },
  };
}

/**
 * Configuration WU17 — custom fields, lead custom values, and contact tags
 * (mirror api/routes/agent_wu14.py console routes). Every success envelope is
 * the same {contract_version, environment: dev-only-agent, accepted, replayed,
 * correlation_id, <resource>: payload}; scope is server-derived. Custom field
 * names are account-dynamic, so only the fixed CHECK type domain is enumerable
 * client-side.
 */
export const CUSTOM_FIELD_TYPES: readonly string[] = ["text", "number", "select", "date"];
export type CustomFieldType = "text" | "number" | "select" | "date";

export type CustomFieldDefinition = {
  id: string;
  name: string;
  type: CustomFieldType;
  choices: string[];
};

export type CustomFieldDefinitionsResponse = AgentConsoleBase & {
  custom_fields: { count: number; items: CustomFieldDefinition[] };
};

export type CustomFieldValue = {
  field_id: string;
  name: string;
  type: CustomFieldType;
  value: string;
};

export type CustomFieldValuesResponse = AgentConsoleBase & {
  custom_values: { lead_id: string; values: CustomFieldValue[] };
};

export type CustomFieldValuesWriteResponse = AgentConsoleBase & {
  custom_values: { lead_id: string; values: CustomFieldValue[] };
};

export type ContactTagList = {
  id: string;
  name: string;
  tags: string[];
};

export type ContactsByTagResponse = AgentConsoleBase & {
  contacts: { count: number; items: ContactTagList[] };
};

export type ContactTagsResponse = AgentConsoleBase & {
  tags: { contact_id: string; tags: string[] };
};

export type CustomFieldWriteResponse = AgentConsoleBase & {
  custom_field: {
    resource_id: string;
    name: string | null;
    type: CustomFieldType | null;
    choices: string[] | null;
  };
};

/** Write inputs — scope is server-derived; no account id appears here. */
export type CreateCustomFieldInput = {
  name: string;
  type: CustomFieldType;
  choices?: string[];
  idempotencyKey?: string;
};

export type UpdateCustomFieldInput = {
  name?: string;
  type?: CustomFieldType;
  choices?: string[];
  idempotencyKey?: string;
};

export type SetLeadCustomValuesInput = {
  values: Record<string, string | number>;
  idempotencyKey?: string;
};

export type ContactTagInput = {
  tag: string;
  idempotencyKey?: string;
};

function reqCustomFieldType(value: unknown, label: string): CustomFieldType {
  if (typeof value !== "string" || !CUSTOM_FIELD_TYPES.includes(value)) {
    throw new Error(`Invalid ${label}: must be text|number|select|date`);
  }
  return value as CustomFieldType;
}

function optCustomFieldType(value: unknown, label: string): CustomFieldType | null {
  if (value === undefined || value === null) return null;
  return reqCustomFieldType(value, label);
}

function optStringList(value: unknown, label: string): string[] | null {
  if (value === undefined || value === null) return null;
  return reqStringList(value, label);
}

function parseCustomFieldDefinition(value: unknown, label: string): CustomFieldDefinition {
  const record = reqRecord(value, label);
  for (const key of Object.keys(record)) {
    if (key !== "id" && key !== "name" && key !== "type" && key !== "choices") {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  return {
    id: reqStr(record, "id", `${label}.id`),
    name: reqStr(record, "name", `${label}.name`),
    type: reqCustomFieldType(record.type, `${label}.type`),
    choices: reqStringList(record.choices, `${label}.choices`),
  };
}

export function parseCustomFieldDefinitionsResponse(value: unknown): CustomFieldDefinitionsResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "custom_fields", "custom fields");
  for (const key of Object.keys(payload)) {
    if (key !== "count" && key !== "items") {
      throw new Error(`Invalid custom fields key: unknown key "${key}"`);
    }
  }
  const items = reqArray(payload.items, "custom fields.items")
    .map((entry, index) => parseCustomFieldDefinition(entry, `custom field[${index}]`));
  return {
    ...consoleBase(correlation, replayed),
    custom_fields: { count: reqNum(payload, "count", "custom fields.count"), items },
  };
}

function parseCustomFieldValue(value: unknown, label: string): CustomFieldValue {
  const record = reqRecord(value, label);
  for (const key of Object.keys(record)) {
    if (key !== "field_id" && key !== "name" && key !== "type" && key !== "value") {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  return {
    field_id: reqStr(record, "field_id", `${label}.field_id`),
    name: reqStr(record, "name", `${label}.name`),
    type: reqCustomFieldType(record.type, `${label}.type`),
    value: reqStr(record, "value", `${label}.value`),
  };
}

function parseCustomValuesPayload(payload: Record<string, unknown>, label: string): {
  lead_id: string;
  values: CustomFieldValue[];
} {
  for (const key of Object.keys(payload)) {
    if (key !== "lead_id" && key !== "values") {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  const values = reqArray(payload.values, `${label}.values`)
    .map((entry, index) => parseCustomFieldValue(entry, `${label}.values[${index}]`));
  return {
    lead_id: reqStr(payload, "lead_id", `${label}.lead_id`),
    values,
  };
}

export function parseCustomFieldValuesResponse(value: unknown): CustomFieldValuesResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "custom_values", "custom values");
  return {
    ...consoleBase(correlation, replayed),
    custom_values: parseCustomValuesPayload(payload, "custom values"),
  };
}

export function parseCustomFieldValuesWriteResponse(value: unknown): CustomFieldValuesWriteResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "custom_values", "custom values write");
  return {
    ...consoleBase(correlation, replayed),
    custom_values: parseCustomValuesPayload(payload, "custom values write"),
  };
}

function parseContactTagList(value: unknown, label: string): ContactTagList {
  const record = reqRecord(value, label);
  for (const key of Object.keys(record)) {
    if (key !== "id" && key !== "name" && key !== "tags") {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  return {
    id: reqStr(record, "id", `${label}.id`),
    name: reqStr(record, "name", `${label}.name`),
    tags: reqStringList(record.tags, `${label}.tags`),
  };
}

export function parseContactsByTagResponse(value: unknown): ContactsByTagResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "contacts", "contacts by tag");
  for (const key of Object.keys(payload)) {
    if (key !== "count" && key !== "items") {
      throw new Error(`Invalid contacts by tag key: unknown key "${key}"`);
    }
  }
  const items = reqArray(payload.items, "contacts by tag.items")
    .map((entry, index) => parseContactTagList(entry, `contact[${index}]`));
  return {
    ...consoleBase(correlation, replayed),
    contacts: { count: reqNum(payload, "count", "contacts by tag.count"), items },
  };
}

export function parseContactTagsResponse(value: unknown): ContactTagsResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "tags", "contact tags");
  for (const key of Object.keys(payload)) {
    if (key !== "contact_id" && key !== "tags") {
      throw new Error(`Invalid contact tags key: unknown key "${key}"`);
    }
  }
  return {
    ...consoleBase(correlation, replayed),
    tags: {
      contact_id: reqStr(payload, "contact_id", "contact tags.contact_id"),
      tags: reqStringList(payload.tags, "contact tags.tags"),
    },
  };
}

export function parseCustomFieldWriteResponse(value: unknown): CustomFieldWriteResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "custom_field", "custom field write");
  for (const key of Object.keys(payload)) {
    if (key !== "resource_id" && key !== "name" && key !== "type" && key !== "choices" && key !== "correlation_id") {
      throw new Error(`Invalid custom field write key: unknown key "${key}"`);
    }
  }
  return {
    ...consoleBase(correlation, replayed),
    custom_field: {
      resource_id: reqStr(payload, "resource_id", "custom field write.resource_id"),
      name: optAbsentStr(payload, "name", "custom field write.name"),
      type: optCustomFieldType(payload.type, "custom field write.type"),
      choices: optStringList(payload.choices, "custom field write.choices"),
    },
  };
}

/**
 * Agent Custom Field Writes WU18 — signal bindings ("Fuentes del agente",
 * mirror api/routes/agent_wu14.py signal-binding routes on build_agent_router:
 * GET /agent/signal-bindings, POST /agent/signal-bindings
 * {signal_key, field_definition_id}, DELETE /agent/signal-bindings/{signal_key}).
 * Same console envelope; scope is server-derived. The signal catalog is
 * closed: unknown signal keys fail closed (400 server-side, throw client-side).
 */
export const SIGNAL_KEYS: readonly string[] = [
  "phone",
  "email",
  "presupuesto_max",
  "barrio",
  "tipo_propiedad",
  "fecha_objetivo",
  "medio_de_contacto",
];

export type SignalBinding = {
  signal_key: string;
  field_definition_id: string;
  field_name: string;
  field_type: CustomFieldType;
};

export type SignalBindingsResponse = AgentConsoleBase & {
  signal_bindings: { count: number; items: SignalBinding[] };
};

export type SignalBindingWriteResponse = AgentConsoleBase & {
  signal_binding: SignalBinding;
};

export type SignalBindingDeleteResponse = AgentConsoleBase & {
  signal_binding: { signal_key: string; deleted: boolean };
};

/** Write input — scope is server-derived; no account id appears here. */
export type CreateSignalBindingInput = {
  signal_key: string;
  field_definition_id: string;
  idempotencyKey?: string;
};

function reqSignalKey(value: unknown, label: string): string {
  if (typeof value !== "string" || !SIGNAL_KEYS.includes(value)) {
    throw new Error(`Invalid ${label}: unknown signal "${String(value)}"`);
  }
  return value;
}

function parseSignalBinding(value: unknown, label: string): SignalBinding {
  const record = reqRecord(value, label);
  for (const key of Object.keys(record)) {
    if (
      key !== "signal_key" && key !== "field_definition_id" &&
      key !== "field_name" && key !== "field_type"
    ) {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  return {
    signal_key: reqSignalKey(record.signal_key, `${label}.signal_key`),
    field_definition_id: reqStr(record, "field_definition_id", `${label}.field_definition_id`),
    field_name: reqStr(record, "field_name", `${label}.field_name`),
    field_type: reqCustomFieldType(record.field_type, `${label}.field_type`),
  };
}

export function parseSignalBindingsResponse(value: unknown): SignalBindingsResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "signal_bindings", "signal bindings");
  for (const key of Object.keys(payload)) {
    if (key !== "count" && key !== "items") {
      throw new Error(`Invalid signal bindings key: unknown key "${key}"`);
    }
  }
  const items = reqArray(payload.items, "signal bindings.items")
    .map((entry, index) => parseSignalBinding(entry, `signal binding[${index}]`));
  return {
    ...consoleBase(correlation, replayed),
    signal_bindings: { count: reqNum(payload, "count", "signal bindings.count"), items },
  };
}

export function parseSignalBindingWriteResponse(value: unknown): SignalBindingWriteResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "signal_binding", "signal binding write");
  return {
    ...consoleBase(correlation, replayed),
    signal_binding: parseSignalBinding(payload, "signal binding write"),
  };
}

export function parseSignalBindingDeleteResponse(value: unknown): SignalBindingDeleteResponse {
  const { correlation, replayed, payload } = parseConsoleEnvelope(value, "signal_binding", "signal binding delete");
  for (const key of Object.keys(payload)) {
    if (key !== "signal_key" && key !== "deleted") {
      throw new Error(`Invalid signal binding delete key: unknown key "${key}"`);
    }
  }
  const deleted = payload.deleted;
  if (typeof deleted !== "boolean") throw new Error("Invalid signal binding delete.deleted");
  return {
    ...consoleBase(correlation, replayed),
    signal_binding: {
      signal_key: reqSignalKey(payload.signal_key, "signal binding delete.signal_key"),
      deleted,
    },
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

/**
 * Commercial Pipelines WU19 — advisor pipeline/stage write surface
 * (mirror api/routes/pipeline_wu19.py on build_pipeline_router:
 * POST /pipelines, PUT /pipelines/{pipeline_id}, POST
 * /pipelines/{pipeline_id}/stages, PUT /stages/{stage_id}, POST
 * /stages/{stage_id}/move with the strict up|down enum, DELETE
 * /stages/{stage_id}).
 *
 * Every success envelope is {contract_version, environment:
 * dev-only-commercial-pipelines, accepted, replayed, correlation_id,
 * result: payload}; scope is server-derived so no account id ever appears
 * in a request body. A stage delete blocked by leads denies fail-closed
 * with 409 {error: {code: stage_has_leads, ...}, lead_count}. The UI
 * re-reads GET /v1/demo/pipeline after every mutation; these parsers only
 * prove the write envelope round-trips with the same strictness.
 */
export const PIPELINE_WRITE_ENVIRONMENT = "dev-only-commercial-pipelines" as const;

/** Strict move direction domain (mirror _require_direction: up|down only). */
export const PIPELINE_MOVE_DIRECTIONS = ["up", "down"] as const;
export type PipelineMoveDirection = (typeof PIPELINE_MOVE_DIRECTIONS)[number];

type PipelineWriteBase = {
  contract_version: typeof RUNTIME_CONTRACT_VERSION;
  environment: typeof PIPELINE_WRITE_ENVIRONMENT;
  accepted: true;
  replayed: boolean;
  correlation_id: CorrelationId;
};

export type PipelineCreatedStage = {
  stage_id: string;
  name: string;
  position: number;
};

export type PipelineCreateResponse = PipelineWriteBase & {
  result: {
    resource_id: string;
    name: string;
    stages: PipelineCreatedStage[];
    correlation_id: string;
  };
};

export type PipelineRenameResponse = PipelineWriteBase & {
  result: {
    resource_id: string;
    name: string;
    /** Pipelines carry no agent-known stage names: always false, kept so the rename envelope stays uniform. */
    agent_name_warning: boolean;
    correlation_id: string;
  };
};

export type PipelineStageCreateResponse = PipelineWriteBase & {
  result: {
    resource_id: string;
    pipeline_id: string;
    name: string;
    position: number;
    correlation_id: string;
  };
};

export type PipelineStageRenameResponse = PipelineWriteBase & {
  result: {
    resource_id: string;
    name: string;
    previous_name: string;
    /** Warn-only agent-name guard: true when either side touches a known agent stage name. */
    agent_name_warning: boolean;
    correlation_id: string;
  };
};

export type PipelineStageMoveResponse = PipelineWriteBase & {
  result: {
    resource_id: string;
    position: number;
    /** False on the already-first/already-last edge no-op (still success). */
    moved: boolean;
    correlation_id: string;
  };
};

export type PipelineStageDeleteResponse = PipelineWriteBase & {
  result: {
    resource_id: string;
    deleted: true;
    /** True when the deleted stage carried an agent-known name. */
    agent_name_warning: boolean;
    correlation_id: string;
  };
};

/** Fail-closed 409 when leads still reference the stage. */
export type PipelineStageDeleteDenial = {
  error: {
    code: "stage_has_leads";
    message: string;
    correlation_id: CorrelationId;
  };
  lead_count: number | null;
};

/** Write inputs — scope is server-derived; no account id appears here. */
export type CreatePipelineInput = {
  name: string;
  stages?: string[];
  idempotencyKey?: string;
};

export type RenamePipelineInput = {
  name: string;
  idempotencyKey?: string;
};

export type CreateStageInput = {
  name: string;
  idempotencyKey?: string;
};

export type RenameStageInput = {
  name: string;
  idempotencyKey?: string;
};

export type MoveStageInput = {
  direction: PipelineMoveDirection;
  idempotencyKey?: string;
};

export type DeleteStageInput = {
  idempotencyKey?: string;
};

function parsePipelineWriteEnvelope(value: unknown, label: string): {
  correlation: CorrelationId;
  replayed: boolean;
  payload: Record<string, unknown>;
} {
  if (!isRecord(value)) throw new Error(`Invalid ${label} response`);
  for (const key of Object.keys(value)) {
    if (
      key !== "contract_version" && key !== "environment" && key !== "accepted" &&
      key !== "replayed" && key !== "correlation_id" && key !== "result"
    ) {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  if (value.contract_version !== RUNTIME_CONTRACT_VERSION) throw new Error("Unsupported contract version");
  if (value.environment !== PIPELINE_WRITE_ENVIRONMENT) throw new Error(`Invalid ${label} environment`);
  if (value.accepted !== true) throw new Error(`Invalid ${label} accepted flag`);
  if (typeof value.replayed !== "boolean") throw new Error(`Invalid ${label} replayed flag`);
  const correlation = reqStr(value, "correlation_id", `${label}.correlation_id`);
  correlationId(correlation);
  return {
    correlation: correlation as CorrelationId,
    replayed: value.replayed,
    payload: reqRecord(value.result, `${label}.result`),
  };
}

function pipelineWriteBase(correlation: CorrelationId, replayed: boolean): PipelineWriteBase {
  return {
    contract_version: RUNTIME_CONTRACT_VERSION,
    environment: PIPELINE_WRITE_ENVIRONMENT,
    accepted: true,
    replayed,
    correlation_id: correlation,
  };
}

function parsePipelineCreatedStage(value: unknown, label: string): PipelineCreatedStage {
  const record = reqRecord(value, label);
  for (const key of Object.keys(record)) {
    if (key !== "stage_id" && key !== "name" && key !== "position") {
      throw new Error(`Invalid ${label} key: unknown key "${key}"`);
    }
  }
  return {
    stage_id: reqStr(record, "stage_id", `${label}.stage_id`),
    name: reqStr(record, "name", `${label}.name`),
    position: reqNum(record, "position", `${label}.position`),
  };
}

export function parsePipelineCreateResponse(value: unknown): PipelineCreateResponse {
  const { correlation, replayed, payload } = parsePipelineWriteEnvelope(value, "pipeline create");
  for (const key of Object.keys(payload)) {
    if (key !== "resource_id" && key !== "name" && key !== "stages" && key !== "correlation_id") {
      throw new Error(`Invalid pipeline create key: unknown key "${key}"`);
    }
  }
  const stages = reqArray(payload.stages, "pipeline create.stages")
    .map((entry, index) => parsePipelineCreatedStage(entry, `pipeline create.stages[${index}]`));
  return {
    ...pipelineWriteBase(correlation, replayed),
    result: {
      resource_id: reqStr(payload, "resource_id", "pipeline create.resource_id"),
      name: reqStr(payload, "name", "pipeline create.name"),
      stages,
      correlation_id: reqStr(payload, "correlation_id", "pipeline create.correlation_id"),
    },
  };
}

export function parsePipelineRenameResponse(value: unknown): PipelineRenameResponse {
  const { correlation, replayed, payload } = parsePipelineWriteEnvelope(value, "pipeline rename");
  for (const key of Object.keys(payload)) {
    if (key !== "resource_id" && key !== "name" && key !== "agent_name_warning" && key !== "correlation_id") {
      throw new Error(`Invalid pipeline rename key: unknown key "${key}"`);
    }
  }
  return {
    ...pipelineWriteBase(correlation, replayed),
    result: {
      resource_id: reqStr(payload, "resource_id", "pipeline rename.resource_id"),
      name: reqStr(payload, "name", "pipeline rename.name"),
      agent_name_warning: reqBool(payload, "agent_name_warning", "pipeline rename.agent_name_warning"),
      correlation_id: reqStr(payload, "correlation_id", "pipeline rename.correlation_id"),
    },
  };
}

export function parsePipelineStageCreateResponse(value: unknown): PipelineStageCreateResponse {
  const { correlation, replayed, payload } = parsePipelineWriteEnvelope(value, "pipeline stage create");
  for (const key of Object.keys(payload)) {
    if (
      key !== "resource_id" && key !== "pipeline_id" && key !== "name" &&
      key !== "position" && key !== "correlation_id"
    ) {
      throw new Error(`Invalid pipeline stage create key: unknown key "${key}"`);
    }
  }
  return {
    ...pipelineWriteBase(correlation, replayed),
    result: {
      resource_id: reqStr(payload, "resource_id", "pipeline stage create.resource_id"),
      pipeline_id: reqStr(payload, "pipeline_id", "pipeline stage create.pipeline_id"),
      name: reqStr(payload, "name", "pipeline stage create.name"),
      position: reqNum(payload, "position", "pipeline stage create.position"),
      correlation_id: reqStr(payload, "correlation_id", "pipeline stage create.correlation_id"),
    },
  };
}

export function parsePipelineStageRenameResponse(value: unknown): PipelineStageRenameResponse {
  const { correlation, replayed, payload } = parsePipelineWriteEnvelope(value, "pipeline stage rename");
  for (const key of Object.keys(payload)) {
    if (
      key !== "resource_id" && key !== "name" && key !== "previous_name" &&
      key !== "agent_name_warning" && key !== "correlation_id"
    ) {
      throw new Error(`Invalid pipeline stage rename key: unknown key "${key}"`);
    }
  }
  return {
    ...pipelineWriteBase(correlation, replayed),
    result: {
      resource_id: reqStr(payload, "resource_id", "pipeline stage rename.resource_id"),
      name: reqStr(payload, "name", "pipeline stage rename.name"),
      previous_name: reqStr(payload, "previous_name", "pipeline stage rename.previous_name"),
      agent_name_warning: reqBool(payload, "agent_name_warning", "pipeline stage rename.agent_name_warning"),
      correlation_id: reqStr(payload, "correlation_id", "pipeline stage rename.correlation_id"),
    },
  };
}

export function parsePipelineStageMoveResponse(value: unknown): PipelineStageMoveResponse {
  const { correlation, replayed, payload } = parsePipelineWriteEnvelope(value, "pipeline stage move");
  for (const key of Object.keys(payload)) {
    if (key !== "resource_id" && key !== "position" && key !== "moved" && key !== "correlation_id") {
      throw new Error(`Invalid pipeline stage move key: unknown key "${key}"`);
    }
  }
  return {
    ...pipelineWriteBase(correlation, replayed),
    result: {
      resource_id: reqStr(payload, "resource_id", "pipeline stage move.resource_id"),
      position: reqNum(payload, "position", "pipeline stage move.position"),
      moved: reqBool(payload, "moved", "pipeline stage move.moved"),
      correlation_id: reqStr(payload, "correlation_id", "pipeline stage move.correlation_id"),
    },
  };
}

export function parsePipelineStageDeleteResponse(value: unknown): PipelineStageDeleteResponse {
  const { correlation, replayed, payload } = parsePipelineWriteEnvelope(value, "pipeline stage delete");
  for (const key of Object.keys(payload)) {
    if (
      key !== "resource_id" && key !== "deleted" &&
      key !== "agent_name_warning" && key !== "correlation_id"
    ) {
      throw new Error(`Invalid pipeline stage delete key: unknown key "${key}"`);
    }
  }
  if (payload.deleted !== true) throw new Error("Invalid pipeline stage delete.deleted");
  return {
    ...pipelineWriteBase(correlation, replayed),
    result: {
      resource_id: reqStr(payload, "resource_id", "pipeline stage delete.resource_id"),
      deleted: true,
      agent_name_warning: reqBool(payload, "agent_name_warning", "pipeline stage delete.agent_name_warning"),
      correlation_id: reqStr(payload, "correlation_id", "pipeline stage delete.correlation_id"),
    },
  };
}

export function parsePipelineStageDeleteDenial(value: unknown): PipelineStageDeleteDenial {
  const record = reqRecord(value, "pipeline stage delete denial");
  for (const key of Object.keys(record)) {
    if (key !== "error" && key !== "lead_count") {
      throw new Error(`Invalid pipeline stage delete denial key: unknown key "${key}"`);
    }
  }
  const error = reqRecord(record.error, "pipeline stage delete denial.error");
  for (const key of Object.keys(error)) {
    if (key !== "code" && key !== "message" && key !== "correlation_id") {
      throw new Error(`Invalid pipeline stage delete denial error key: unknown key "${key}"`);
    }
  }
  if (error.code !== "stage_has_leads") {
    throw new Error('Invalid pipeline stage delete denial code: must be "stage_has_leads"');
  }
  const correlation = reqStr(error, "correlation_id", "pipeline stage delete denial.correlation_id");
  correlationId(correlation);
  const leadCount = record.lead_count;
  if (
    leadCount !== undefined && leadCount !== null &&
    (typeof leadCount !== "number" || !Number.isFinite(leadCount))
  ) {
    throw new Error("Invalid pipeline stage delete denial.lead_count");
  }
  return {
    error: {
      code: "stage_has_leads",
      message: reqStr(error, "message", "pipeline stage delete denial.message"),
      correlation_id: correlation as CorrelationId,
    },
    lead_count: (leadCount ?? null) as number | null,
  };
}

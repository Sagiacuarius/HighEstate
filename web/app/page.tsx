"use client";

/**
 * MVP Commercial Demo WU8 — real-data dashboard.
 *
 * Replaces the runtime health page. Every number, row and badge on this page
 * comes from the versioned WU6 read APIs through the selector-governed demo
 * session (WU7/WU8 composition). No fixture metrics, no hard-coded counts:
 * loading states render skeletons and errors render the envelope code.
 */
import React from "react";
import {
  createDemoApi,
  resolveDemoApiBaseUrl,
  describeSimulateResult,
  formatDayUtc,
  formatMoney,
  formatTimeUtc,
  initials,
  isResetBlockedError,
  unreadTotal,
  INTENT_LABELS,
  ACTION_LABELS,
  describeCriteria,
  describeExecution,
  agentModeLabel,
  policyDecisionLabel,
  type AuditFilterState,
  type DemoApi,
} from "./demo-api";
import { RuntimeHttpError } from "../runtime-client";
import {
  AGENT_CONSOLE_ACTION_TYPES,
  CUSTOM_FIELD_TYPES,
  KB_ARTICLE_CATEGORIES,
  LEAD_FIELD_COLUMNS,
} from "../../contracts/runtime-v1";
import type {
  AgentCalendarConfig,
  AgentDraft,
  AgentKbArticle,
  AgentPromptPreview,
  AgentPromptVersion,
  AgentSettings,
  AgentStateResponse,
  ContactTagList,
  CrmInboxItem,
  CustomFieldDefinition,
  CustomFieldType,
  DemoContextResponse,
  KbSelfTestProbe,
} from "../../contracts/runtime-v1";

export const dynamic = "force-dynamic";

type ViewKey = "resumen" | "inbox" | "leads" | "properties" | "pipeline" | "agenda" | "activity" | "auditoria" | "calendario" | "agente";

type ViewData = {
  dashboard?: import("../../contracts/runtime-v1").CrmDashboardResponse;
  inbox?: import("../../contracts/runtime-v1").CrmInboxResponse;
  leads?: import("../../contracts/runtime-v1").CrmLeadsResponse;
  properties?: import("../../contracts/runtime-v1").CrmPropertiesResponse;
  pipeline?: import("../../contracts/runtime-v1").CrmPipelineResponse;
  agenda?: import("../../contracts/runtime-v1").CrmAgendaResponse;
  activity?: import("../../contracts/runtime-v1").CrmActivityResponse;
};

const DEMO_ACCOUNTS = [
  { id: "20000000-0000-0000-0000-000000000001", label: "Aurea Inmobiliaria Norte" },
  { id: "20000000-0000-0000-0000-000000000002", label: "Litoral Inmobiliaria Sur" },
] as const;

const AVATAR_TONES = ["gold", "sand", "olive", "rose", "blue", "violet"] as const;

function toneFor(name: string): string {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return AVATAR_TONES[hash % AVATAR_TONES.length];
}

function pillFor(status: string): string {
  const s = status.toLowerCase();
  if (s === "qualified" || s === "won" || s === "completed" || s === "available" || s === "success") return "status-pill ok";
  if (s === "lost" || s === "cancelled" || s === "denied") return "status-pill bad";
  return "status-pill hot";
}

// Traducción de los valores técnicos que vienen de la base (enums y actions del audit).
const STATUS_LABELS: Record<string, string> = {
  new: "Nuevo",
  qualified: "Calificado",
  won: "Ganado",
  lost: "Perdido",
  available: "Disponible",
  reserved: "Reservado",
  unavailable: "No disponible",
  open: "Abierta",
  closed: "Cerrada",
  archived: "Archivada",
  pending: "Pendiente",
  completed: "Completada",
  cancelled: "Cancelada",
  inbound: "entrada",
  outbound: "salida",
  success: "éxito",
  denied: "denegado",
  failure: "fallo",
  // WU11 (property-catalog): estados del catálogo y acciones del audit.
  capturing: "En captura",
  sold: "Vendido",
  rented: "Alquilado",
  paused: "Pausado",
  "crm.property.create": "Propiedad creada",
  "crm.property.update": "Propiedad actualizada",
  "crm.property.status_change": "Cambio de estado de propiedad",
  "crm.property.link_contact": "Contacto vinculado a propiedad",
  "crm.property.register_interest": "Interés registrado",
  "crm.inbound.process": "Mensaje entrante procesado",
  "crm.lead.transition": "Prospecto avanzado de etapa",
  "crm.follow_up.create": "Seguimiento creado",
  "crm.follow_up.complete": "Seguimiento completado",
  authorization_denied: "Acceso denegado",
  // WU13 (calendar-agenda): estados de eventos y acciones del audit.
  scheduled: "Programado",
  "crm.calendar.create": "Evento creado",
  "crm.calendar.update": "Evento actualizado",
  "crm.calendar.cancel": "Evento cancelado",
  // WU14 (conversational agent): estados de borrador y acciones del audit.
  // WU15 (agent autonomy): el estado auto de un borrador ejecutado por la política.
  proposed: "Propuesto",
  approved: "Aprobado",
  rejected: "Rechazado",
  executed: "Ejecutado",
  auto: "Automático",
  "crm.agent.analyze": "Análisis del agente",
  "crm.agent.decide": "Decisión del agente",
  "crm.agent.execute": "Borrador ejecutado",
};

function statusLabel(value: string): string {
  return STATUS_LABELS[value.toLowerCase()] ?? value;
}

const VIEW_LABELS: Record<ViewKey, string> = {
  resumen: "Resumen",
  inbox: "Bandeja",
  leads: "Prospectos",
  properties: "Propiedades",
  pipeline: "Embudo",
  agenda: "Agenda",
  activity: "Actividad",
  auditoria: "Auditoría",
  calendario: "Calendario",
  agente: "Agente",
};

function consoleFailure(error: unknown): string {
  const correlation = error instanceof RuntimeHttpError
    ? ` · ${error.envelope.error.correlation_id}` : "";
  return `${errorText(error)}${correlation}`;
}

// WU16: etiquetas en español neutro para la consola del agente (el servidor
// sigue siendo neutral al idioma). Días = Python weekday(): lunes=0.
const WEEKDAY_LABELS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"] as const;

const LEAD_FIELD_LABELS: Record<string, string> = {
  stage_id: "Etapa",
  status: "Estado",
  selected_property_id: "Propiedad elegida",
};

const CONSOLE_ACTION_LABELS: Record<string, string> = {
  ...ACTION_LABELS,
  handoff_to_human: "Derivar a un humano",
};

// WU17: etiquetas en español neutro para los tipos de campo personalizado.
const CUSTOM_FIELD_TYPE_LABELS: Record<string, string> = {
  text: "Texto",
  number: "Número",
  select: "Selección",
  date: "Fecha",
};

function errorText(error: unknown): string {
  if (error instanceof RuntimeHttpError) return `${error.status} · ${error.envelope.error.code}`;
  if (error instanceof Error) return error.message;
  return "unknown error";
}

export default function DashboardPage(): React.ReactElement {
  const [api] = React.useState<DemoApi | null>(() => {
    try {
      return createDemoApi(resolveDemoApiBaseUrl(process.env.NEXT_PUBLIC_DEMO_API_BASE_URL));
    } catch {
      return null;
    }
  });
  const [account, setAccount] = React.useState<DemoContextResponse | null>(null);
  const [view, setView] = React.useState<ViewKey>("resumen");
  const [data, setData] = React.useState<ViewData>({});
  const [viewErrors, setViewErrors] = React.useState<Partial<Record<ViewKey | "cuenta", string>>>({});
  const [loading, setLoading] = React.useState(true);
  const [switching, setSwitching] = React.useState(false);
  const [selectedConversation, setSelectedConversation] = React.useState<string | null>(null);
  const [thread, setThread] = React.useState<import("../../contracts/runtime-v1").CrmMessagesResponse | null>(null);
  const [threadError, setThreadError] = React.useState<string | null>(null);
  const [controlsNote, setControlsNote] = React.useState<string | null>(null);
  const [simulating, setSimulating] = React.useState(false);
  const [simulateNote, setSimulateNote] = React.useState<string | null>(null);
  const [resetBlocked, setResetBlocked] = React.useState(false);
  // WU12 audit screen: own state (filters + keyset paging), never fixture data.
  const [audit, setAudit] = React.useState<import("../../contracts/runtime-v1").CrmAuditResponse | null>(null);
  const [auditError, setAuditError] = React.useState<string | null>(null);
  const [auditFilters, setAuditFilters] = React.useState<AuditFilterState>({});
  const [auditLoading, setAuditLoading] = React.useState(false);
  // WU14 conversational agent: draft cards live inline in the conversation
  // thread; decisions re-run the account reads because they mutate CRM data.
  const [drafts, setDrafts] = React.useState<AgentDraft[]>([]);
  const [draftsError, setDraftsError] = React.useState<string | null>(null);
  const [draftsLoading, setDraftsLoading] = React.useState(false);
  const [agentBody, setAgentBody] = React.useState("");
  const [agentNote, setAgentNote] = React.useState<string | null>(null);
  const [analyzing, setAnalyzing] = React.useState(false);
  const [decisionBusy, setDecisionBusy] = React.useState<string | null>(null);
  // WU15 (agent autonomy): dev-only agent state per conversation (mode switch
  // + handoff resume); null while unloaded or when the read fails closed.
  const [agentState, setAgentState] = React.useState<AgentStateResponse | null>(null);
  const [modeBusy, setModeBusy] = React.useState(false);
  // WU16 Agent Console: cinco secciones sobre /v1/demo/agent/* (alcance
  // derivado por el servidor; ningún payload lleva ids de cuenta). Cada
  // mutación refresca su lectura; los fallos muestran el envelope + correlación.
  const [consoleSettings, setConsoleSettings] = React.useState<AgentSettings | null>(null);
  const [consoleError, setConsoleError] = React.useState<string | null>(null);
  const [consoleLoading, setConsoleLoading] = React.useState(false);
  const [consoleNote, setConsoleNote] = React.useState<string | null>(null);
  const [consoleBusy, setConsoleBusy] = React.useState(false);
  const [promptList, setPromptList] = React.useState<AgentPromptVersion[]>([]);
  const [promptPreview, setPromptPreview] = React.useState<AgentPromptPreview | null>(null);
  const [kbArticles, setKbArticles] = React.useState<AgentKbArticle[]>([]);
  const [kbProbe, setKbProbe] = React.useState<KbSelfTestProbe | null>(null);
  const [editingKbId, setEditingKbId] = React.useState<string | null>(null);
  const [calendarConfig, setCalendarConfig] = React.useState<AgentCalendarConfig | null>(null);
  const [leadFields, setLeadFields] = React.useState<string[]>([]);
  // WU17: campos personalizados + etiquetas de contactos (alcance derivado por
  // el servidor). Carga y error independientes del resto de la consola.
  const [customFields, setCustomFields] = React.useState<CustomFieldDefinition[]>([]);
  const [customFieldsError, setCustomFieldsError] = React.useState<string | null>(null);
  const [customFieldsLoading, setCustomFieldsLoading] = React.useState(false);
  const [editingFieldId, setEditingFieldId] = React.useState<string | null>(null);
  const [contacts, setContacts] = React.useState<ContactTagList[]>([]);
  const [tagFilter, setTagFilter] = React.useState<string>("");
  const [tagsNote, setTagsNote] = React.useState<string | null>(null);
  const [tagsError, setTagsError] = React.useState<string | null>(null);

  const loadConsole = React.useCallback(async (client: DemoApi): Promise<void> => {
    setConsoleLoading(true);
    setConsoleError(null);
    try {
      const [settings, prompts, preview, articles, calCfg, fields] = await Promise.all([
        client.getConsoleSettings(),
        client.listPrompts(),
        client.previewPrompt(),
        client.listKbArticles(),
        client.getCalendarConfig(),
        client.getLeadFields(),
      ]);
      setConsoleSettings(settings.settings);
      setPromptList(prompts.prompts.items);
      setPromptPreview(preview.preview);
      setKbArticles(articles.articles.items);
      setCalendarConfig(calCfg.calendar_config);
      setLeadFields(fields.allowlist.allowed);
    } catch (error) {
      setConsoleError(consoleFailure(error));
    } finally {
      setConsoleLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (!api || view !== "agente") return;
    void loadConsole(api);
  }, [api, view, account?.client_account_id, loadConsole]);

  const loadCustomFields = React.useCallback(async (client: DemoApi): Promise<void> => {
    setCustomFieldsLoading(true);
    setCustomFieldsError(null);
    try {
      const result = await client.listCustomFields();
      setCustomFields(result.custom_fields.items);
    } catch (error) {
      setCustomFieldsError(consoleFailure(error));
    } finally {
      setCustomFieldsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (!api || view !== "agente") return;
    void loadCustomFields(api);
  }, [api, view, account?.client_account_id, loadCustomFields]);

  const submitConsoleSettings = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const form = new FormData(event.currentTarget);
    const matrix = AGENT_CONSOLE_ACTION_TYPES.map((action) => ({
      action_type: action,
      verdict: (String(form.get(`verdict-${action}`) ?? "escalate") === "auto" ? "auto" : "escalate") as "auto" | "escalate",
    }));
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.updateConsoleSettings({
        global_auto: form.get("global_auto") === "on",
        calendar_auto_enabled: form.get("calendar_auto_enabled") === "on",
        matrix,
      });
      const fresh = await api.getConsoleSettings();
      setConsoleSettings(fresh.settings);
      setConsoleNote("Ajustes guardados");
    } catch (error) {
      setConsoleNote(`Guardar ajustes falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const submitCreatePrompt = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const form = new FormData(event.currentTarget);
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.createPrompt({
        tone: String(form.get("tone") ?? ""),
        instructions: String(form.get("instructions") ?? ""),
      });
      event.currentTarget.reset();
      const [prompts, preview] = await Promise.all([api.listPrompts(), api.previewPrompt()]);
      setPromptList(prompts.prompts.items);
      setPromptPreview(preview.preview);
      setConsoleNote("Versión del prompt creada");
    } catch (error) {
      setConsoleNote(`Crear versión falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const runRevertPrompt = async (version: number): Promise<void> => {
    if (!api || consoleBusy) return;
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.revertPrompt(version);
      const [prompts, preview] = await Promise.all([api.listPrompts(), api.previewPrompt()]);
      setPromptList(prompts.prompts.items);
      setPromptPreview(preview.preview);
      setConsoleNote(`Reversión a la versión ${version} creada como versión nueva`);
    } catch (error) {
      setConsoleNote(`Revertir falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const submitCreateKb = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const form = new FormData(event.currentTarget);
    const keywords = String(form.get("keywords") ?? "")
      .split(",").map((k) => k.trim()).filter(Boolean);
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.createKbArticle({
        category: String(form.get("category") ?? ""),
        question: String(form.get("question") ?? ""),
        keywords,
        answer: String(form.get("answer") ?? ""),
      });
      event.currentTarget.reset();
      const articles = await api.listKbArticles();
      setKbArticles(articles.articles.items);
      setConsoleNote("Artículo creado");
    } catch (error) {
      setConsoleNote(`Crear artículo falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const submitEditKb = async (event: React.FormEvent<HTMLFormElement>, articleId: string): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const form = new FormData(event.currentTarget);
    const keywordsRaw = String(form.get("keywords") ?? "").trim();
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.updateKbArticle(articleId, {
        question: String(form.get("question") ?? ""),
        keywords: keywordsRaw ? keywordsRaw.split(",").map((k) => k.trim()).filter(Boolean) : undefined,
        answer: String(form.get("answer") ?? ""),
      });
      setEditingKbId(null);
      const articles = await api.listKbArticles();
      setKbArticles(articles.articles.items);
      setConsoleNote("Artículo actualizado");
    } catch (error) {
      setConsoleNote(`Actualizar artículo falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const runDeactivateKb = async (articleId: string): Promise<void> => {
    if (!api || consoleBusy) return;
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.deleteKbArticle(articleId);
      const articles = await api.listKbArticles();
      setKbArticles(articles.articles.items);
      setConsoleNote("Artículo desactivado");
    } catch (error) {
      setConsoleNote(`Desactivar falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const runSelfTest = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const form = new FormData(event.currentTarget);
    const question = String(form.get("probe") ?? "").trim();
    if (!question) {
      setKbProbe(null);
      setConsoleNote("Escribí una pregunta antes de probar.");
      return;
    }
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      const result = await api.kbSelfTest(question);
      setKbProbe(result.probe);
    } catch (error) {
      setKbProbe(null);
      setConsoleNote(`Probar falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const submitCalendarConfig = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const workingDays = [0, 1, 2, 3, 4, 5, 6].filter((day) => form.get(`day-${day}`) === "on");
    const labelRaw = String(form.get("destination_label") ?? "").trim();
    const timezoneRaw = String(form.get("timezone") ?? "").trim();
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.updateCalendarConfig({
        window_start: String(form.get("window_start") ?? ""),
        window_end: String(form.get("window_end") ?? ""),
        slot_minutes: Number(String(form.get("slot_minutes") ?? "")),
        working_days: workingDays,
        destination_label: labelRaw ? labelRaw : null,
        ...(timezoneRaw ? { timezone: timezoneRaw } : {}),
      });
      const fresh = await api.getCalendarConfig();
      setCalendarConfig(fresh.calendar_config);
      setConsoleNote("Calendario guardado");
    } catch (error) {
      setConsoleNote(`Guardar calendario falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const submitLeadFields = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const form = new FormData(event.currentTarget);
    const allowed = LEAD_FIELD_COLUMNS.filter((column) => form.get(`field-${column}`) === "on");
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.updateLeadFields({ allowed: [...allowed] });
      const fresh = await api.getLeadFields();
      setLeadFields(fresh.allowlist.allowed);
      setConsoleNote("Campos guardados");
    } catch (error) {
      setConsoleNote(`Guardar campos falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const submitCreateCustomField = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const type = String(form.get("type") ?? "text") as CustomFieldType;
    const choicesRaw = String(form.get("choices") ?? "").trim();
    const choices = choicesRaw ? choicesRaw.split(",").map((c) => c.trim()).filter(Boolean) : undefined;
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.createCustomField({
        name: String(form.get("name") ?? ""),
        type,
        ...(type === "select" && choices ? { choices } : {}),
      });
      formEl.reset();
      await loadCustomFields(api);
      setConsoleNote("Campo personalizado creado");
    } catch (error) {
      setConsoleNote(`Crear campo falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const submitEditCustomField = async (event: React.FormEvent<HTMLFormElement>, fieldId: string): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const form = new FormData(event.currentTarget);
    const nameRaw = String(form.get("name") ?? "").trim();
    const typeRaw = String(form.get("type") ?? "").trim();
    const choicesRaw = String(form.get("choices") ?? "").trim();
    const body: import("../../contracts/runtime-v1").UpdateCustomFieldInput = {};
    if (nameRaw) body.name = nameRaw;
    if (typeRaw) body.type = typeRaw as CustomFieldType;
    const choices = choicesRaw ? choicesRaw.split(",").map((c) => c.trim()).filter(Boolean) : undefined;
    if (choices) body.choices = choices;
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.updateCustomField(fieldId, body);
      setEditingFieldId(null);
      await loadCustomFields(api);
      setConsoleNote("Campo actualizado");
    } catch (error) {
      setConsoleNote(`Actualizar campo falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const runDeleteCustomField = async (fieldId: string): Promise<void> => {
    if (!api || consoleBusy) return;
    setConsoleBusy(true);
    setConsoleNote(null);
    try {
      await api.deleteCustomField(fieldId);
      await loadCustomFields(api);
      setConsoleNote("Campo eliminado");
    } catch (error) {
      setConsoleNote(`Eliminar campo falló: ${consoleFailure(error)}`);
    } finally {
      setConsoleBusy(false);
    }
  };

  const submitTagFilter = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const form = new FormData(event.currentTarget);
    const tag = String(form.get("tag") ?? "").trim();
    if (!tag) {
      setContacts([]);
      setTagFilter("");
      setTagsNote("Escribí una etiqueta para filtrar.");
      return;
    }
    setConsoleBusy(true);
    setTagsNote(null);
    setTagsError(null);
    try {
      const result = await api.listContactsByTag(tag);
      setContacts(result.contacts.items);
      setTagFilter(tag);
      setTagsNote(`${result.contacts.count} contacto(s) con la etiqueta «${tag}»`);
    } catch (error) {
      setContacts([]);
      setTagsError(consoleFailure(error));
    } finally {
      setConsoleBusy(false);
    }
  };

  const submitAddTag = async (event: React.FormEvent<HTMLFormElement>, contactId: string): Promise<void> => {
    event.preventDefault();
    if (!api || consoleBusy) return;
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const tag = String(form.get("tag") ?? "").trim();
    if (!tag) return;
    setConsoleBusy(true);
    setTagsNote(null);
    setTagsError(null);
    try {
      await api.addContactTag(contactId, { tag });
      formEl.reset();
      const fresh = await api.listContactsByTag(tagFilter);
      setContacts(fresh.contacts.items);
      setTagsNote("Etiqueta agregada");
    } catch (error) {
      setTagsError(consoleFailure(error));
    } finally {
      setConsoleBusy(false);
    }
  };

  const runRemoveTag = async (contactId: string, tag: string): Promise<void> => {
    if (!api || consoleBusy) return;
    setConsoleBusy(true);
    setTagsNote(null);
    setTagsError(null);
    try {
      await api.removeContactTag(contactId, { tag });
      const fresh = await api.listContactsByTag(tagFilter);
      setContacts(fresh.contacts.items);
      setTagsNote("Etiqueta quitada");
    } catch (error) {
      setTagsError(consoleFailure(error));
    } finally {
      setConsoleBusy(false);
    }
  };

  const reload = React.useCallback(
    async (client: DemoApi) => {
      setLoading(true);
      const settled = await Promise.allSettled([
        client.dashboard(),
        client.inbox(),
        client.leads(),
        client.properties(),
        client.pipeline(),
        client.agenda(),
        client.activity(),
      ]);
      const keys: ViewKey[] = ["resumen", "inbox", "leads", "properties", "pipeline", "agenda", "activity"];
      const nextData: ViewData = {};
      const nextErrors: Partial<Record<ViewKey, string>> = {};
      const dataKeys = ["dashboard", "inbox", "leads", "properties", "pipeline", "agenda", "activity"] as const;
      settled.forEach((result, index) => {
        if (result.status === "fulfilled") {
          (nextData as Record<string, unknown>)[dataKeys[index]] = result.value;
        } else {
          nextErrors[keys[index]] = errorText(result.reason);
        }
      });
      setData(nextData);
      setViewErrors(nextErrors);
      setLoading(false);
    },
    [],
  );

  React.useEffect(() => {
    if (!api) {
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const current = await api.current();
        setAccount(current);
        await reload(api);
      } catch (error) {
        setViewErrors({ cuenta: errorText(error) });
        setLoading(false);
      }
    })();
  }, [api, reload]);

  // WU12: the audit screen loads on demand (never cached server-side, own
  // filter/cursor state). append=true continues the keyset via next_cursor.
  const loadAudit = React.useCallback(
    async (client: DemoApi, filters: AuditFilterState, append: boolean): Promise<void> => {
      setAuditLoading(true);
      setAuditError(null);
      try {
        const page = await client.audit(filters);
        setAudit((previous) => (append && previous
          ? { ...page, items: [...previous.items, ...page.items], count: previous.count + page.count }
          : page));
      } catch (error) {
        // Envelope discipline: the operator must see the correlation id to
        // cross-check against the X-Correlation-Id header (acceptance gate).
        const correlation = error instanceof RuntimeHttpError
          ? ` · ${error.envelope.error.correlation_id}` : "";
        setAuditError(`${errorText(error)}${correlation}`);
        if (!append) setAudit(null);
      } finally {
        setAuditLoading(false);
      }
    },
    [],
  );

  React.useEffect(() => {
    if (!api) return;
    setAudit(null);
    void loadAudit(api, {}, false);
  }, [api, account?.client_account_id, loadAudit]);

  const applyAuditFilters = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!api) return;
    const form = new FormData(event.currentTarget);
    const next: AuditFilterState = {};
    for (const key of ["contact_id", "property_id", "user_id", "action"] as const) {
      const value = String(form.get(key) ?? "").trim();
      if (value) next[key] = value;
    }
    // datetime-local yields "YYYY-MM-DDTHH:MM" (no offset); the server demands
    // an ISO-8601 value with timezone. Treat the control's naive time as UTC.
    const fromRaw = String(form.get("from") ?? "").trim();
    if (fromRaw) {
      next.from = /^\d{4}-\d{2}-\d{2}$/.test(fromRaw) ? `${fromRaw}T00:00:00Z`
        : /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(fromRaw) ? `${fromRaw}:00Z` : fromRaw;
    }
    const toRaw = String(form.get("to") ?? "").trim();
    if (toRaw) {
      next.to = /^\d{4}-\d{2}-\d{2}$/.test(toRaw) ? `${toRaw}T23:59:59Z`
        : /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(toRaw) ? `${toRaw}:00Z` : toRaw;
    }
    setAuditFilters(next);
    void loadAudit(api, next, false);
  };

  // WU13: the calendar renders a month grid (never cached server-side). Click a
  // date cell "+" to create inline (title + times, date fixed to the cell);
  // click an event chip to edit (full form below, for cross-date moves);
  // click chip "×" to cancel. editingEventId null = no edit form.
  const [calendar, setCalendar] = React.useState<import("../../contracts/runtime-v1").CalendarEventsResponse | null>(null);
  const [calendarError, setCalendarError] = React.useState<string | null>(null);
  const [calendarLoading, setCalendarLoading] = React.useState(false);
  const [calendarMonthOffset, setCalendarMonthOffset] = React.useState(0);
  const [creatingDate, setCreatingDate] = React.useState<string | null>(null);
  const [editingEventId, setEditingEventId] = React.useState<string | null>(null);
  const [calendarNote, setCalendarNote] = React.useState<string | null>(null);

  const calendarMonthRange = React.useCallback((offsetMonths: number): { from: string; to: string; year: number; month: number } => {
    const now = new Date();
    const first = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + offsetMonths, 1, 0, 0, 0, 0));
    const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0, 23, 59, 59, 0));
    return { from: first.toISOString(), to: last.toISOString(), year: first.getUTCFullYear(), month: first.getUTCMonth() };
  }, []);

  const loadCalendar = React.useCallback(
    async (client: DemoApi, offsetMonths: number): Promise<void> => {
      setCalendarLoading(true);
      setCalendarError(null);
      try {
        const { from, to } = calendarMonthRange(offsetMonths);
        const page = await client.calendarEvents(from, to, false);
        setCalendar(page);
      } catch (error) {
        const correlation = error instanceof RuntimeHttpError
          ? ` · ${error.envelope.error.correlation_id}` : "";
        setCalendarError(`${errorText(error)}${correlation}`);
        setCalendar(null);
      } finally {
        setCalendarLoading(false);
      }
    },
    [calendarMonthRange],
  );

  React.useEffect(() => {
    if (!api) return;
    setCalendar(null);
    void loadCalendar(api, calendarMonthOffset);
  }, [api, account?.client_account_id, calendarMonthOffset, loadCalendar]);

  // Cells for the month grid: leading Monday through trailing Sunday (UTC).
  const calendarCells = React.useMemo((): Array<{ dateKey: string; dayNum: number; inMonth: boolean; isToday: boolean }> => {
    const { year, month } = calendarMonthRange(calendarMonthOffset);
    const first = new Date(Date.UTC(year, month, 1));
    const lead = (first.getUTCDay() + 6) % 7; // Monday=0
    const start = new Date(first);
    start.setUTCDate(start.getUTCDate() - lead);
    const todayKey = new Date().toISOString().slice(0, 10);
    const cells: Array<{ dateKey: string; dayNum: number; inMonth: boolean; isToday: boolean }> = [];
    for (let i = 0; i < 42; i++) {
      const d = new Date(start);
      d.setUTCDate(d.getUTCDate() + i);
      const key = d.toISOString().slice(0, 10);
      cells.push({ dateKey: key, dayNum: d.getUTCDate(), inMonth: d.getUTCMonth() === month, isToday: key === todayKey });
      if (i >= 27 && d.getUTCDay() === 0 && d.getUTCMonth() !== month) break;
    }
    return cells;
  }, [calendarMonthOffset, calendarMonthRange]);

  const eventsForDate = React.useCallback((dateKey: string) => {
    if (!calendar) return [];
    return calendar.items.filter((e) => e.starts_at.slice(0, 10) === dateKey);
  }, [calendar]);

  const submitInlineCreate = async (event: React.FormEvent<HTMLFormElement>, dateKey: string): Promise<void> => {
    event.preventDefault();
    if (!api) return;
    const form = new FormData(event.currentTarget);
    const title = String(form.get("title") ?? "").trim();
    const startTime = String(form.get("start_time") ?? "09:00").trim();
    const endTime = String(form.get("end_time") ?? "10:00").trim();
    setCalendarNote(null);
    try {
      const result = await api.createCalendarEvent({
        title,
        startsAt: `${dateKey}T${startTime}:00Z`,
        endsAt: `${dateKey}T${endTime}:00Z`,
        idempotencyKey: `ui-calendar-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      });
      setCalendarNote(result.replayed ? "Evento creado (repetición)" : "Evento creado");
      setCreatingDate(null);
      await loadCalendar(api, calendarMonthOffset);
    } catch (error) {
      const correlation = error instanceof RuntimeHttpError
        ? ` · ${error.envelope.error.correlation_id}` : "";
      setCalendarError(`${errorText(error)}${correlation}`);
    }
  };

  const submitCalendarForm = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!api || !editingEventId) return;
    const form = new FormData(event.currentTarget);
    const text = (name: string): string | undefined => {
      const v = String(form.get(name) ?? "").trim();
      return v ? v : undefined;
    };
    const toUtcIso = (raw: string | undefined): string | undefined => {
      if (!raw) return undefined;
      return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(raw) ? `${raw}:00Z` : raw;
    };
    setCalendarNote(null);
    try {
      const result = await api.updateCalendarEvent(editingEventId, {
        title: text("title"),
        startsAt: toUtcIso(text("starts_at")),
        endsAt: toUtcIso(text("ends_at")),
        description: text("description"),
        contactId: text("contact_id"),
        leadId: text("lead_id"),
        propertyId: text("property_id"),
        notes: text("notes"),
        idempotencyKey: `ui-calendar-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      });
      setCalendarNote(result.replayed ? "Evento actualizado (repetición)" : "Evento actualizado");
      setEditingEventId(null);
      event.currentTarget.reset();
      await loadCalendar(api, calendarMonthOffset);
    } catch (error) {
      const correlation = error instanceof RuntimeHttpError
        ? ` · ${error.envelope.error.correlation_id}` : "";
      setCalendarError(`${errorText(error)}${correlation}`);
    }
  };

  const cancelCalendarEvent = async (eventId: string): Promise<void> => {
    if (!api) return;
    setCalendarNote(null);
    try {
      const result = await api.cancelCalendarEvent(eventId, `ui-calendar-cancel-${Date.now()}`);
      setCalendarNote(result.replayed ? "Evento cancelado (repetición)" : "Evento cancelado");
      await loadCalendar(api, calendarMonthOffset);
    } catch (error) {
      const correlation = error instanceof RuntimeHttpError
        ? ` · ${error.envelope.error.correlation_id}` : "";
      setCalendarError(`${errorText(error)}${correlation}`);
    }
  };

  const switchAccount = async (requestedAccountId: string): Promise<void> => {
    if (!api || switching) return;
    setSwitching(true);
    setControlsNote(null);
    try {
      const resolved = await api.selectAccount(requestedAccountId);
      setAccount(resolved);
      setSelectedConversation(null);
      setThread(null);
      setDrafts([]);
      setAgentNote(null);
      setAgentState(null);
      await reload(api);
      await loadDrafts(api, null);
    } catch (error) {
      setViewErrors((prev) => ({ ...prev, cuenta: errorText(error) }));
    } finally {
      setSwitching(false);
    }
  };

  const openConversation = async (item: CrmInboxItem): Promise<void> => {
    if (!api) return;
    setSelectedConversation(item.conversation_id);
    setThreadError(null);
    try {
      setThread(await api.messages(item.conversation_id));
    } catch (error) {
      setThreadError(errorText(error));
      setThread(null);
    }
    await loadDrafts(api, item.conversation_id);
    await loadAgentState(api, item.conversation_id);
  };

  // WU15: the handoff flag + paused indicator come from the dev-only agent
  // state read (the accepted WU6 inbox response stays byte-identical). A
  // failed read fails closed to null — never a stale mode.
  const loadAgentState = React.useCallback(async (client: DemoApi, conversationId: string | null): Promise<void> => {
    try {
      setAgentState(await client.getAgentState(conversationId ?? undefined));
    } catch {
      setAgentState(null);
    }
  }, []);

  const loadDrafts = React.useCallback(async (client: DemoApi, conversationId: string | null): Promise<void> => {
    setDraftsLoading(true);
    setDraftsError(null);
    try {
      const page = await client.agentDrafts(conversationId ?? undefined);
      setDrafts(page.items);
    } catch (error) {
      const correlation = error instanceof RuntimeHttpError
        ? ` · ${error.envelope.error.correlation_id}` : "";
      setDraftsError(`${errorText(error)}${correlation}`);
      setDrafts([]);
    } finally {
      setDraftsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (!api) return;
    void loadDrafts(api, null);
  }, [api, account?.client_account_id, loadDrafts]);

  const runAnalyze = async (): Promise<void> => {
    if (!api || analyzing) return;
    const lastInbound = thread
      ? [...thread.items].reverse().find((m) => m.direction === "inbound")?.body ?? ""
      : "";
    const body = (agentBody.trim() || lastInbound).trim();
    if (!body) {
      setAgentNote("Escribí un mensaje del lead (o simulá uno) antes de analizar.");
      return;
    }
    setAnalyzing(true);
    setAgentNote(null);
    try {
      const result = await api.analyzeMessage(body);
      setAgentNote(result.auto_executed
        ? "Ejecutado por la política — sin revisión manual"
        : result.replayed
          ? "Borrador ya existía para este mensaje (repetición)"
          : "Borrador propuesto — revisalo y aprobalo o rechazalo");
      setAgentBody("");
      await loadDrafts(api, selectedConversation);
      // The auto path may pause the conversation (handoff escalation):
      // refresh the state read so the flag + switch stay truthful.
      await loadAgentState(api, selectedConversation);
    } catch (error) {
      const correlation = error instanceof RuntimeHttpError
        ? ` · ${error.envelope.error.correlation_id}` : "";
      setAgentNote(`Análisis falló: ${errorText(error)}${correlation}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const runDecide = async (draft: AgentDraft, approve: boolean): Promise<void> => {
    if (!api || decisionBusy) return;
    setDecisionBusy(draft.draft_id);
    setAgentNote(null);
    try {
      const result = approve
        ? await api.approveDraft(draft.draft_id)
        : await api.rejectDraft(draft.draft_id);
      setAgentNote(approve
        ? `Borrador ejecutado · ${describeExecution(result.draft.execution_result) ?? "sin acciones"}`
        : "Borrador rechazado — nada se aplicó");
      await loadDrafts(api, selectedConversation);
      // The approved actions mutate CRM data (follow-ups, events, interests):
      // refresh the account reads so the badges stay DB-derived.
      await reload(api);
      await loadAgentState(api, selectedConversation);
    } catch (error) {
      const correlation = error instanceof RuntimeHttpError
        ? ` · ${error.envelope.error.correlation_id}` : "";
      setAgentNote(`Decisión falló: ${errorText(error)}${correlation}`);
    } finally {
      setDecisionBusy(null);
    }
  };

  // WU15 dev-only Auto/Manual switch + handoff resume. The server derives the
  // scope (never a client account id); every mutation refreshes drafts + state.
  const runAgentMode = async (mode: "manual" | "auto"): Promise<void> => {
    if (!api || modeBusy) return;
    setModeBusy(true);
    setAgentNote(null);
    try {
      const state = await api.setAgentMode(mode);
      setAgentState(state);
      setAgentNote(mode === "auto"
        ? "Modo automático activado — el agente puede ejecutar la política"
        : "Modo manual activado — los borradores requieren tu aprobación");
      await loadDrafts(api, selectedConversation);
    } catch (error) {
      const correlation = error instanceof RuntimeHttpError
        ? ` · ${error.envelope.error.correlation_id}` : "";
      setAgentNote(`Cambio de modo falló: ${errorText(error)}${correlation}`);
    } finally {
      setModeBusy(false);
    }
  };

  const runResumeHandoff = async (): Promise<void> => {
    if (!api || modeBusy) return;
    setModeBusy(true);
    setAgentNote(null);
    try {
      const state = await api.resumeHandoff();
      setAgentState(state);
      setAgentNote("Conversación retomada — el agente vuelve a modo automático");
      await loadDrafts(api, selectedConversation);
      await reload(api);
    } catch (error) {
      const correlation = error instanceof RuntimeHttpError
        ? ` · ${error.envelope.error.correlation_id}` : "";
      setAgentNote(`Retomar falló: ${errorText(error)}${correlation}`);
    } finally {
      setModeBusy(false);
    }
  };

  const runControl = async (kind: "seed" | "reset", forceEffects = false): Promise<void> => {
    if (!api) return;
    setControlsNote(`${kind === "seed" ? "Recargar demo" : "Reinicio"} en curso…`);
    try {
      const result = kind === "seed" ? await api.seed() : await api.reset({}, forceEffects);
      const cleared = typeof result.cleared_effects === "number" ? ` · ${result.cleared_effects} efectos limpiados` : "";
      setControlsNote(`${kind === "seed" ? "Recarga" : "Reinicio"} ok · cuenta ${result.account_id.slice(0, 8)}…${cleared}`);
      setResetBlocked(false);
      await reload(api);
    } catch (error) {
      if (kind === "reset" && isResetBlockedError(error)) {
        // WU9: reset fail-closed by pending simulator effects; surface the
        // explicit force option instead of hiding the block.
        setResetBlocked(true);
        setControlsNote(`Reinicio bloqueado por ${errorText(error)}`);
        return;
      }
      setControlsNote(`Control falló: ${errorText(error)}`);
    }
  };

  const runSimulate = async (): Promise<void> => {
    if (!api || simulating) return;
    setSimulating(true);
    setSimulateNote(null);
    try {
      const result = await api.simulate({});
      setSimulateNote(describeSimulateResult(result));
      await reload(api);
    } catch (error) {
      setSimulateNote(`Simulación falló: ${errorText(error)}`);
    } finally {
      setSimulating(false);
    }
  };

  if (!api) {
    return (
      <main className="content">
        <section className="error-panel">
          <h2>Falta configuración</h2>
          <p>
            Definí <code>NEXT_PUBLIC_DEMO_API_BASE_URL</code> con la URL base del demo API
            (p. ej. http://127.0.0.1:8001) y recargá.
          </p>
        </section>
      </main>
    );
  }

  const inbox = data.inbox;
  const unread = inbox ? unreadTotal(inbox) : 0;
  const pending = data.agenda?.counts.pending ?? 0;
  const counts = data.dashboard?.counts;
  const asOf = data.dashboard?.as_of;

  const navItem = (key: ViewKey, icon: string, label: string, badge?: number): React.ReactElement => (
    <a key={key} href={`#${key}`} className={view === key ? "active" : ""} onClick={(e) => { e.preventDefault(); setView(key); }}>
      <span>{icon}</span> {label}
      {badge !== undefined && badge > 0 ? <b className="soft">{badge}</b> : null}
    </a>
  );

  const viewError = (key: ViewKey): React.ReactElement | null =>
    viewErrors[key] ? (
      <section className="error-panel">
        <h2>No se pudo cargar {VIEW_LABELS[key]}</h2>
        <p><code>{viewErrors[key]}</code> · los datos persistidos no cambiaron.</p>
      </section>
    ) : null;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><img src="/logo.png" alt="HighEstate" width={32} height={32} className="brand-logo" /><span>HighEstate</span></div>
        <div className="account-switcher" aria-label="Selector de cuenta demo">
          {DEMO_ACCOUNTS.map((option) => (
            <button
              key={option.id}
              className={`account-option${account?.client_account_id === option.id ? " selected" : ""}`}
              disabled={switching}
              onClick={() => void switchAccount(option.id)}
            >
              <span className={`avatar ${toneFor(option.label)}`}>{initials(option.label)}</span>
              <div><strong>{option.label}</strong><small>Cuenta demo · controlada por servidor</small></div>
              <span className="dev-badge">demo</span>
            </button>
          ))}
        </div>
        {viewErrors.cuenta ? (
          <p className="empty-note"><code>{viewErrors.cuenta}</code></p>
        ) : null}
        <nav className="main-nav">
          {navItem("resumen", "◈", "Resumen")}
          {navItem("inbox", "◌", "Bandeja", unread)}
          {navItem("leads", "♢", "Prospectos", counts?.leads_total)}
          {navItem("properties", "▱", "Propiedades", counts?.properties_total)}
          {navItem("pipeline", "▥", "Embudo")}
          {navItem("agenda", "◷", "Agenda", pending)}
          {navItem("activity", "↗", "Actividad")}
          {navItem("auditoria", "⌕", "Auditoría")}
          {navItem("calendario", "▦", "Calendario")}
          {navItem("agente", "◍", "Agente")}
        </nav>
        <div className="sidebar-bottom">
          <div className="profile">
            <span className="avatar violet">DU</span>
            <div><strong>usuario demo</strong><small>Identidad solo demo · {account ? account.client_account_name : "…"}</small></div>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button className="ghost-btn" onClick={() => void runControl("seed")}>Recargar demo</button>
            <button className="ghost-btn" onClick={() => void runControl("reset", false)}>Reiniciar</button>
            {resetBlocked ? (
              <button className="ghost-btn danger" onClick={() => void runControl("reset", true)}>
                Forzar reinicio
              </button>
            ) : null}
          </div>
          {controlsNote ? <p className="empty-note">{controlsNote}</p> : null}
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="crumb"><span>Agencia</span><i>/</i><strong>{account ? account.client_account_name : "…"}</strong></div>
          <div className="top-actions"><span className="status-dot"></span><span className="live-label">Datos reales · PostgreSQL</span></div>
        </header>
        <section className="content">
          <div className="hero">
            <div>
              <p className="eyebrow">{asOf ? `${formatDayUtc(asOf)} · datos al ${formatTimeUtc(asOf)} UTC` : "Cargando…"}</p>
              <h1>{account ? account.client_account_name : "…"}<span className="gold-text">.</span></h1>
              <p className="hero-copy">El pulso de tus oportunidades, leído de la base en cada carga.</p>
            </div>
          </div>

          {loading ? <p className="empty-note">Cargando vistas…</p> : null}

          <section className={`view-section${view === "resumen" ? " is-visible" : ""}`}>
            {viewError("resumen")}
            {counts ? (
              <>
                <div className="metric-grid">
                  <article className="metric-card"><div className="metric-top"><span>Conversaciones abiertas</span><span className="metric-icon">◌</span></div><strong>{counts.conversations_open}</strong><div className="metric-foot">{counts.unread_messages}<span> mensajes sin leer</span></div></article>
                  <article className="metric-card"><div className="metric-top"><span>Prospectos totales</span><span className="metric-icon">♢</span></div><strong>{counts.leads_total}</strong><div className="metric-foot">{Object.entries(counts.leads_by_status).map(([s, n]) => `${statusLabel(s)}: ${n}`).join(" · ") || "—"}</div></article>
                  <article className="metric-card"><div className="metric-top"><span>Propiedades</span><span className="metric-icon">▱</span></div><strong>{counts.properties_total}</strong><div className="metric-foot">{Object.entries(counts.properties_by_availability).map(([s, n]) => `${statusLabel(s)}: ${n}`).join(" · ") || "—"}</div></article>
                  <article className="metric-card accent-card"><div className="metric-top"><span>Seguimientos pendientes</span><span className="metric-icon">◷</span></div><strong>{counts.follow_ups_pending}</strong><div className="metric-foot"><span>{counts.follow_ups_overdue} vencidos</span></div></article>
                </div>
                <div className="dashboard-grid">
                  <section className="panel conversations">
                    <div className="panel-heading"><div><p className="eyebrow">Atención</p><h2>Conversaciones recientes</h2></div></div>
                    {inbox && inbox.items.length > 0 ? inbox.items.slice(0, 4).map((item) => (
                      <div className="conversation" key={item.conversation_id}>
                        <span className={`avatar ${toneFor(item.contact_name)}`}>{initials(item.contact_name)}</span>
                        <div className="conversation-body">
                          <div className="row"><strong>{item.contact_name}</strong><time>{item.last_message ? formatTimeUtc(item.last_message.created_at) : "—"}</time></div>
                          <p>{item.last_message ? item.last_message.body : "Sin mensajes"}</p>
                        </div>
                        {item.unread_count > 0 ? <span className="unread"></span> : null}
                      </div>
                    )) : <p className="empty-note">Sin conversaciones.</p>}
                  </section>
                  <section className="panel">
                    <div className="panel-heading"><div><p className="eyebrow">Evidencia</p><h2>Actividad reciente</h2></div></div>
                    {data.activity && data.activity.items.length > 0 ? data.activity.items.slice(0, 5).map((event) => (
                      <div className="flow" key={event.activity_id}>
                        <span className="flow-icon">↗</span>
                        <div><strong>{event.action}</strong><small>{event.outcome}{event.reason ? ` · ${event.reason}` : ""} · {formatTimeUtc(event.created_at)}</small></div>
                      </div>
                    )) : <p className="empty-note">Sin actividad registrada.</p>}
                  </section>
                </div>
              </>
            ) : null}
          </section>

          <section className={`view-section${view === "inbox" ? " is-visible" : ""}`}>
            {viewError("inbox")}
            <section className="panel simulator-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Simulador · solo demo (no Kapso, no WhatsApp)</p>
                  <h2>Simular mensaje entrante</h2>
                </div>
                <button className="ghost-btn simulate-btn" onClick={() => void runSimulate()}
                        disabled={simulating || loading}>
                  {simulating ? "Simulando…" : "Simular mensaje entrante"}
                </button>
              </div>
              {simulateNote ? <p className="empty-note">{simulateNote}</p> : null}
            </section>
            {inbox ? (
              <div className="detail-grid">
                <section className="panel conversations">
                  <div className="panel-heading"><div><p className="eyebrow">Atención</p><h2>Bandeja · {inbox.count}</h2></div></div>
                  {inbox.items.map((item) => (
                    <div
                      className={`conversation${selectedConversation === item.conversation_id ? " selected" : ""}`}
                      key={item.conversation_id}
                      onClick={() => void openConversation(item)}
                      role="button"
                      tabIndex={0}
                    >
                      <span className={`avatar ${toneFor(item.contact_name)}`}>{initials(item.contact_name)}</span>
                      <div className="conversation-body">
                        <div className="row"><strong>{item.contact_name}</strong><time>{item.last_message ? formatTimeUtc(item.last_message.created_at) : "—"}</time></div>
                        <p>{item.last_message ? item.last_message.body : "Sin mensajes"}</p>
                        <div><span className="tag follow">{statusLabel(item.status)}</span><span className="channel">{item.unread_count} sin leer</span></div>
                        {agentState && agentState.handoff_open && agentState.conversation_id === item.conversation_id ? (
                          <div><span className="tag hot">Derivado a humano</span><span className="channel">Pausado</span></div>
                        ) : null}
                      </div>
                      {item.unread_count > 0 ? <span className="unread"></span> : null}
                    </div>
                  ))}
                </section>
                <section className="panel">
                  <div className="panel-heading"><div><p className="eyebrow">Hilo</p><h2>Mensajes</h2></div></div>
                  {threadError ? <p className="empty-note"><code>{threadError}</code></p> : null}
                  {!thread && !threadError ? <p className="empty-note">Elegí una conversación para ver el hilo.</p> : null}
                  {thread ? (
                    <div className="thread">
                      {thread.items.map((message) => (
                        <div className={`message ${message.direction}`} key={message.message_id}>
                          {message.body}
                          <small>{statusLabel(message.direction)} · {message.created_at ? formatTimeUtc(message.created_at) : "—"}</small>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div className="agent-block">
                    <div className="panel-heading"><div><p className="eyebrow">Agente · borradores a confirmar</p><h2>Asistente de {account ? account.client_account_name : "…"}</h2></div></div>
                    <p className="empty-note">El agente lee el mensaje, busca propiedades y sugiere una franja libre. Nada se aplica hasta que apruebes el borrador.</p>
                    {agentState && selectedConversation && agentState.conversation_id === selectedConversation ? (
                      <div className="agent-chips">
                        <span className="soft">Modo: {agentModeLabel(agentState.agent_mode)}</span>
                        {agentState.handoff_open ? <span className="status-pill hot">Derivado a humano</span> : null}
                      </div>
                    ) : null}
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                      <button className="ghost-btn" disabled={modeBusy || !selectedConversation} onClick={() => void runAgentMode("auto")}>Automático</button>
                      <button className="ghost-btn" disabled={modeBusy || !selectedConversation} onClick={() => void runAgentMode("manual")}>Manual</button>
                      {agentState && agentState.handoff_open && selectedConversation && agentState.conversation_id === selectedConversation ? (
                        <button className="ghost-btn danger" disabled={modeBusy} onClick={() => void runResumeHandoff()}>Retomar conversación</button>
                      ) : null}
                    </div>
                    <div className="agent-analyze">
                      <textarea
                        value={agentBody}
                        onChange={(e) => setAgentBody(e.target.value)}
                        rows={2}
                        placeholder="Escribí el mensaje del lead (o dejá vacío y usá el último entrante del hilo), luego Analizar."
                      />
                      <button className="ghost-btn" onClick={() => void runAnalyze()} disabled={analyzing || draftsLoading}>
                        {analyzing ? "Analizando…" : "Analizar con el agente"}
                      </button>
                    </div>
                    {draftsError ? <p className="empty-note"><code>{draftsError}</code></p> : null}
                    {agentNote ? <p className="empty-note">{agentNote}</p> : null}
                    {draftsLoading ? <p className="empty-note">Cargando borradores…</p> : null}
                    {!draftsLoading && drafts.length === 0 ? <p className="empty-note">Todavía no hay borradores en esta conversación.</p> : null}
                    {drafts.map((draft) => {
                      const chips = describeCriteria(draft.criteria);
                      const summary = describeExecution(draft.execution_result);
                      return (
                        <div className="agent-draft-card" key={draft.draft_id}>
                          <div className="agent-draft-head">
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <span className="avatar violet">AI</span>
                              <div><strong>{INTENT_LABELS[draft.intent] ?? draft.intent}</strong>
                                <div className="mono" style={{ fontSize: 13 }}>{draft.model_provider} · {draft.model_name}</div>
                              </div>
                            </div>
                            <span className={pillFor(draft.status)}>{statusLabel(draft.status)}</span>
                          </div>
                          {draft.execution_mode === "agent_policy" ? (
                            <div className="agent-chips">
                              <span className="status-pill ok">Auto · {policyDecisionLabel(draft.policy_decision)}</span>
                            </div>
                          ) : null}
                          {draft.intent === "handoff" ? (
                            <div className="agent-chips">
                              <span className="status-pill hot">Derivado a humano</span>
                            </div>
                          ) : null}
                          {chips.length > 0 ? <div className="agent-chips">{chips.map((c) => <span className="soft" key={c}>{c}</span>)}</div> : null}
                          {draft.matched_property_ids.length > 0 ? (
                            <div className="agent-chips">
                              {draft.matched_property_ids.map((pid) => <span className="mono" key={pid}>propiedad {pid.slice(0, 8)}…</span>)}
                            </div>
                          ) : null}
                          <ul className="agent-actions">
                            {draft.proposed_actions.map((action, i) => (
                              <li key={`${draft.draft_id}-${i}`}>
                                {ACTION_LABELS[action.type] ?? action.type}
                                {typeof action.params.title === "string" ? <> · {action.params.title}</> : null}
                                {typeof action.params.starts_at === "string" ? <> · {formatDayUtc(action.params.starts_at)} {formatTimeUtc(action.params.starts_at)} UTC</> : null}
                              </li>
                            ))}
                          </ul>
                          {draft.reply_text ? <p className="agent-reply">“{draft.reply_text}”</p> : null}
                          {summary ? <p className="empty-note">Ejecución: {summary}</p> : null}
                          {draft.status === "proposed" ? (
                            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                              <button className="ghost-btn" disabled={decisionBusy !== null} onClick={() => void runDecide(draft, true)}>Aprobar</button>
                              <button className="ghost-btn danger" disabled={decisionBusy !== null} onClick={() => void runDecide(draft, false)}>Rechazar</button>
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                </section>
              </div>
            ) : null}
          </section>

          <section className={`view-section${view === "leads" ? " is-visible" : ""}`}>
            {viewError("leads")}
            {data.leads ? (
              <section className="panel">
                <div className="panel-heading"><div><p className="eyebrow">Embudo</p><h2>Prospectos · {data.leads.count}</h2></div></div>
                <table className="data-table">
                  <thead><tr><th>Contacto</th><th>Etapa</th><th>Estado</th><th>Origen</th></tr></thead>
                  <tbody>
                    {data.leads.items.map((lead) => (
                      <tr key={lead.lead_id}>
                        <td><strong>{lead.contact_name}</strong><br /><span className="mono">{lead.pipeline_name}</span></td>
                        <td>{lead.stage_name}</td>
                        <td><span className={pillFor(lead.status)}>{statusLabel(lead.status)}</span></td>
                        <td>{statusLabel(lead.source)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            ) : null}
          </section>

          <section className={`view-section${view === "properties" ? " is-visible" : ""}`}>
            {viewError("properties")}
            {data.properties ? (
              <section className="panel">
                <div className="panel-heading"><div><p className="eyebrow">Catálogo</p><h2>Propiedades · {data.properties.count}</h2></div></div>
                {data.properties.items.map((property, index) => (
                  <div className="property" key={property.property_id}>
                    <div className={`property-image ${index % 2 === 0 ? "image-one" : "image-two"}`}></div>
                    <div>
                      <span className="property-type">{property.operation_type === "sale" ? "VENTA" : "ALQUILER"} · {property.location}</span>
                      <strong>{property.title}</strong>
                      <p>{property.bedrooms} dormitorios · <span className={pillFor(property.availability)}>{statusLabel(property.availability)}</span></p>
                    </div>
                    <b>{formatMoney(property.price, property.currency)}</b>
                  </div>
                ))}
              </section>
            ) : null}
          </section>

          <section className={`view-section${view === "pipeline" ? " is-visible" : ""}`}>
            {viewError("pipeline")}
            {data.pipeline && data.leads ? (
              <section className="panel pipeline-panel">
                <div className="panel-heading"><div><p className="eyebrow">Embudo comercial</p><h2>Embudo</h2></div></div>
                {data.pipeline.items.map((pipelineView) => (
                  <div key={pipelineView.pipeline_id}>
                    <p className="eyebrow">{pipelineView.name}</p>
                    <div className="pipeline">
                      {pipelineView.stages.map((stage) => (
                        <div className="pipeline-col" key={stage.stage_id}>
                          <div className="pipeline-col-head"><span>{stage.name}</span><b>{stage.lead_count}</b></div>
                          {data.leads?.items.filter((lead) => lead.stage_id === stage.stage_id).map((lead) => (
                            <div className="lead-card" key={lead.lead_id}>
                              <strong>{lead.contact_name}</strong>
                              <small>{statusLabel(lead.status)} · {statusLabel(lead.source)}</small>
                            </div>
                          ))}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </section>
            ) : null}
          </section>

          <section className={`view-section${view === "agenda" ? " is-visible" : ""}`}>
            {viewError("agenda")}
            {data.agenda ? (
              <section className="panel">
                <div className="panel-heading"><div><p className="eyebrow">Próximos eventos</p><h2>Agenda · {data.agenda.counts.pending} pendientes</h2></div></div>
                {data.agenda.items.map((item) => (
                  <div className="calendar-event" key={item.follow_up_id}>
                    <span className="event-time">{formatTimeUtc(item.due_at)}</span>
                    <span className="event-line"></span>
                    <div><strong>{item.title}</strong><small>{item.contact_name} · {formatDayUtc(item.due_at)}</small></div>
                    {item.overdue ? <span className="status-pill bad">vencido</span> : <span className={pillFor(item.status)}>{statusLabel(item.status)}</span>}
                  </div>
                ))}
              </section>
            ) : null}
          </section>

          <section className={`view-section${view === "activity" ? " is-visible" : ""}`}>
            {viewError("activity")}
            {data.activity ? (
              <section className="panel">
                <div className="panel-heading"><div><p className="eyebrow">Evidencia</p><h2>Actividad · {data.activity.total}</h2></div></div>
                <table className="data-table">
                  <thead><tr><th>Acción</th><th>Resultado</th><th>Correlación</th><th>Hora</th></tr></thead>
                  <tbody>
                    {data.activity.items.map((event) => (
                      <tr key={event.activity_id}>
                        <td><strong>{statusLabel(event.action)}</strong>{event.reason ? <><br /><span className="mono">{event.reason}</span></> : null}</td>
                        <td><span className={pillFor(event.outcome)}>{statusLabel(event.outcome)}</span></td>
                        <td><span className="mono">{event.correlation_id}</span></td>
                        <td>{formatTimeUtc(event.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            ) : null}
          </section>
          <section className={`view-section${view === "auditoria" ? " is-visible" : ""}`}>
            <section className="panel">
              <div className="panel-heading">
                <div><p className="eyebrow">Evidencia · solo lectura</p><h2>Auditoría · {audit ? audit.total : "…"}</h2></div>
                <p className="empty-note">Ruta leída <code>GET /v1/demo/audit</code>; sin filtros por cuenta: el alcance lo deriva el servidor.</p>
              </div>
              <form className="audit-filters" onSubmit={applyAuditFilters} aria-label="Filtros de auditoría">
                <label>Contacto<input name="contact_id" defaultValue={auditFilters.contact_id ?? ""} placeholder="uuid" /></label>
                <label>Propiedad<input name="property_id" defaultValue={auditFilters.property_id ?? ""} placeholder="uuid" /></label>
                <label>Usuario<input name="user_id" defaultValue={auditFilters.user_id ?? ""} placeholder="uuid (actor)" /></label>
                <label>Acción<input name="action" defaultValue={auditFilters.action ?? ""} placeholder="crm.property.create" /></label>
                <label>Desde<input name="from" type="datetime-local" defaultValue={auditFilters.from ?? ""} /></label>
                <label>Hasta<input name="to" type="datetime-local" defaultValue={auditFilters.to ?? ""} /></label>
                <button className="ghost-btn" type="submit" disabled={auditLoading}>Filtrar</button>
              </form>
              {auditError ? (
                <p className="empty-note"><code>{auditError}</code></p>
              ) : null}
              {audit && audit.items.length === 0 && !auditLoading ? (
                <p className="empty-note">Sin registros de auditoría para este filtro.</p>
              ) : null}
              {audit && audit.items.length > 0 ? (
                <>
                  <table className="data-table">
                    <thead><tr><th>Acción</th><th>Resultado</th><th>Recurso</th><th>Actor</th><th>Hora</th><th>Correlación</th></tr></thead>
                    <tbody>
                      {audit.items.map((row) => (
                        <tr key={row.audit_id}>
                          <td><strong>{statusLabel(row.action)}</strong>{row.reason ? <><br /><span className="mono">{row.reason}</span></> : null}</td>
                          <td><span className={pillFor(row.outcome)}>{statusLabel(row.outcome)}</span></td>
                          <td><span className="mono">{row.resource_type}{row.resource_id ? ` · ${row.resource_id.slice(0, 8)}…` : ""}</span></td>
                          <td><span className="mono">{row.actor_id ? row.actor_id.slice(0, 8) + "…" : "—"}</span></td>
                          <td>{formatDayUtc(row.created_at)} {formatTimeUtc(row.created_at)}</td>
                          <td><span className="mono">{row.correlation_id}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                    {audit.has_more && audit.next_cursor ? (
                      <button className="ghost-btn" disabled={auditLoading}
                              onClick={() => void loadAudit(api!, { ...auditFilters, cursor: audit.next_cursor }, true)}>
                        {auditLoading ? "Cargando…" : "Cargar más"}
                      </button>
                    ) : null}
                  </div>
                </>
              ) : null}
              {audit && !auditError ? (
                <p className="empty-note">Página {audit.count} de {audit.total} · límite {audit.limit} · datos al {formatTimeUtc(audit.as_of)} UTC</p>
              ) : null}
            </section>
          </section>
          <section className={`view-section${view === "calendario" ? " is-visible" : ""}`}>
            <section className="panel">
              <div className="panel-heading">
                <div><p className="eyebrow">Visitas · tocá una fecha para crear, × para cancelar</p><h2>Calendario · {(() => {
                  const { year, month } = calendarMonthRange(calendarMonthOffset);
                  const months = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
                  return `${months[month]} ${year}`;
                })()} · {calendar ? `${calendar.count} eventos` : "…"}</h2></div>
                <p className="empty-note">Rutas <code>GET /v1/demo/calendar/events</code> y <code>/availability</code>; el alcance lo deriva el servidor.</p>
              </div>
              <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <button className="ghost-btn" disabled={calendarLoading}
                        onClick={() => setCalendarMonthOffset((o) => o - 1)}>← Mes anterior</button>
                <button className="ghost-btn" disabled={calendarLoading || calendarMonthOffset === 0}
                        onClick={() => setCalendarMonthOffset(0)}>Hoy</button>
                <button className="ghost-btn" disabled={calendarLoading}
                        onClick={() => setCalendarMonthOffset((o) => o + 1)}>Mes siguiente →</button>
              </div>
              {calendarError ? (
                <p className="empty-note"><code>{calendarError}</code></p>
              ) : null}
              {calendarNote ? (
                <p className="empty-note">{calendarNote}</p>
              ) : null}
              <div className="calendar-grid" aria-label="Calendario mensual">
                {["lun", "mar", "mié", "jue", "vie", "sáb", "dom"].map((d) => (
                  <div className="calendar-dow" key={d}>{d}</div>
                ))}
                {calendarCells.map((cell) => (
                  <div key={cell.dateKey}
                       className={`calendar-cell${cell.inMonth ? "" : " is-other-month"}${cell.isToday ? " is-today" : ""}`}>
                    <div className="calendar-day-row">
                      <span className="calendar-day-num">{cell.dayNum}</span>
                      <button className="calendar-add" title={`Crear evento el ${cell.dateKey}`}
                              disabled={calendarLoading}
                              onClick={() => { setCreatingDate(cell.dateKey); setEditingEventId(null); }}>+</button>
                    </div>
                    {eventsForDate(cell.dateKey).map((row) => (
                      <button key={row.event_id}
                              className={`calendar-chip${row.status !== "scheduled" ? " is-cancelled" : ""}`}
                              title={`${row.title} · ${formatTimeUtc(row.starts_at)}–${formatTimeUtc(row.ends_at)} UTC · clic para editar`}
                              disabled={calendarLoading || row.status !== "scheduled"}
                              onClick={() => setEditingEventId(row.event_id)}>
                        <span className="calendar-chip-title">{row.title}</span>
                        <span className="calendar-chip-time">{formatTimeUtc(row.starts_at)}</span>
                        <span className="calendar-del" role="button" aria-label={`Cancelar ${row.title}`}
                              onClick={(e) => { e.stopPropagation(); void cancelCalendarEvent(row.event_id); }}>×</span>
                      </button>
                    ))}
                    {creatingDate === cell.dateKey ? (
                      <form className="calendar-inline-form"
                            onSubmit={(e) => void submitInlineCreate(e, cell.dateKey)}
                            aria-label={`Crear evento el ${cell.dateKey}`}>
                        <input name="title" required maxLength={200} placeholder="Título · ej. Visita Palermo" autoFocus />
                        <div className="calendar-inline-row">
                          <input name="start_time" type="time" defaultValue="09:00" required aria-label="Hora de inicio" />
                          <input name="end_time" type="time" defaultValue="10:00" required aria-label="Hora de fin" />
                        </div>
                        <div className="calendar-inline-row">
                          <button className="ghost-btn" type="submit" disabled={calendarLoading}>Crear</button>
                          <button className="ghost-btn" type="button" disabled={calendarLoading}
                                  onClick={() => setCreatingDate(null)}>✕</button>
                        </div>
                      </form>
                    ) : null}
                  </div>
                ))}
              </div>
              {editingEventId ? (
                <>
                  <form className="audit-filters" onSubmit={(e) => void submitCalendarForm(e)} aria-label="Editar evento (mover de fecha)">
                    <label>Título<input name="title" maxLength={200} placeholder="(sin cambios)" /></label>
                    <label>Inicio<input name="starts_at" type="datetime-local" /></label>
                    <label>Fin<input name="ends_at" type="datetime-local" /></label>
                    <label>Contacto<input name="contact_id" placeholder="uuid (opcional)" /></label>
                    <label>Propiedad<input name="property_id" placeholder="uuid (opcional)" /></label>
                    <label>Notas<input name="notes" placeholder="opcional" /></label>
                    <button className="ghost-btn" type="submit" disabled={calendarLoading}>Guardar cambios</button>
                    <button className="ghost-btn" type="button" disabled={calendarLoading}
                            onClick={() => setEditingEventId(null)}>Dejar de editar</button>
                  </form>
                  <p className="empty-note">Editando <code>{editingEventId.slice(0, 8)}…</code> — para mover de fecha usá Inicio/Fin; los vacíos no cambian.</p>
                </>
              ) : null}
            </section>
          </section>
          <section className={`view-section${view === "agente" ? " is-visible" : ""}`}>
            <section className="panel">
              <div className="panel-heading">
                <div><p className="eyebrow">Consola · solo demo</p><h2>Agente · consola de autonomía</h2></div>
                <button className="ghost-btn" disabled={consoleLoading} onClick={() => api && void loadConsole(api)}>Recargar</button>
              </div>
              <p className="empty-note">Rutas <code>GET/PUT /v1/demo/agent/settings</code>, <code>/prompts</code>, <code>/kb/articles</code>, <code>/calendar-config</code> y <code>/lead-fields</code>; el alcance lo deriva el servidor.</p>
              {consoleError ? <p className="empty-note"><code>{consoleError}</code></p> : null}
              {consoleNote ? <p className="empty-note">{consoleNote}</p> : null}
              {consoleLoading ? <p className="empty-note">Cargando consola…</p> : null}
              <h3>Ajustes</h3>
              {consoleSettings ? (
                <form className="audit-filters" onSubmit={(e) => void submitConsoleSettings(e)} aria-label="Ajustes del agente">
                  <label>Automatización global<input type="checkbox" name="global_auto" defaultChecked={consoleSettings.global_auto} aria-label="Automatización global" /></label>
                  <label>Creación automática de eventos<input type="checkbox" name="calendar_auto_enabled" defaultChecked={consoleSettings.calendar_auto_enabled} aria-label="Creación automática de eventos" /></label>
                  <table className="data-table">
                    <thead><tr><th>Acción</th><th>Veredicto</th></tr></thead>
                    <tbody>
                      {consoleSettings.matrix.map((entry) => (
                        <tr key={entry.action_type}>
                          <td><strong>{CONSOLE_ACTION_LABELS[entry.action_type] ?? entry.action_type}</strong><br /><span className="mono">{entry.action_type}</span></td>
                          <td>
                            <select name={`verdict-${entry.action_type}`} defaultValue={entry.verdict} aria-label={`Veredicto ${entry.action_type}`}>
                              <option value="auto">Automático</option>
                              <option value="escalate">Derivar</option>
                            </select>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <button className="ghost-btn" type="submit" disabled={consoleBusy}>Guardar ajustes</button>
                </form>
              ) : null}
              <h3>Prompt</h3>
              {promptPreview ? (
                <div className="result-box" aria-label="Vista previa compuesta">
                  <p><b>Vista previa compuesta · v{promptPreview.version}</b></p>
                  <p>{promptPreview.composed_text}</p>
                </div>
              ) : null}
              <form className="audit-filters" onSubmit={(e) => void submitCreatePrompt(e)} aria-label="Crear versión del prompt">
                <label>Tono<input name="tone" aria-label="Tono" placeholder="cercano y directo" /></label>
                <label>Instrucciones<input name="instructions" aria-label="Instrucciones" placeholder="Priorizar visitas los sábados" /></label>
                <button className="ghost-btn" type="submit" disabled={consoleBusy}>Crear versión</button>
              </form>
              {promptList.length > 0 ? (
                <table className="data-table">
                  <thead><tr><th>Versión</th><th>Tono</th><th>Estado</th><th>Acción</th></tr></thead>
                  <tbody>
                    {promptList.map((row) => (
                      <tr key={row.id}>
                        <td><strong>v{row.version}</strong></td>
                        <td>{row.tone}</td>
                        <td>{row.is_active ? <span className="status-pill ok">Activa</span> : <span className="status-pill">Histórica</span>}</td>
                        <td>
                          {!row.is_active ? (
                            <button className="ghost-btn" disabled={consoleBusy} onClick={() => void runRevertPrompt(row.version)}>
                              Revertir a la versión {row.version}
                            </button>
                          ) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
              <h3>Base de conocimiento</h3>
              <form className="audit-filters" onSubmit={(e) => void submitCreateKb(e)} aria-label="Crear artículo">
                <label>Categoría
                  <select name="category" aria-label="Categoría" defaultValue="visitas">
                    {KB_ARTICLE_CATEGORIES.map((category) => <option key={category} value={category}>{category}</option>)}
                  </select>
                </label>
                <label>Pregunta<input name="question" aria-label="Pregunta" placeholder="¿Puedo visitar el sábado?" /></label>
                <label>Palabras clave<input name="keywords" aria-label="Palabras clave" placeholder="visita, sábado (separadas por coma)" /></label>
                <label>Respuesta<input name="answer" aria-label="Respuesta" placeholder="Coordinamos la visita." /></label>
                <button className="ghost-btn" type="submit" disabled={consoleBusy}>Crear artículo</button>
              </form>
              {kbArticles.length > 0 ? (
                <table className="data-table">
                  <thead><tr><th>Pregunta</th><th>Categoría</th><th>Estado</th><th>Acción</th></tr></thead>
                  <tbody>
                    {kbArticles.map((article) => (
                      <tr key={article.id}>
                        <td><strong>{article.question}</strong><br /><span className="mono">{article.id.slice(0, 8)}…</span></td>
                        <td>{article.category}</td>
                        <td>{article.deactivated_at ? <span className="status-pill bad">Desactivada</span> : <span className="status-pill ok">Activa</span>}</td>
                        <td>
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                            {!article.deactivated_at ? (
                              <button className="ghost-btn danger" disabled={consoleBusy} onClick={() => void runDeactivateKb(article.id)}>Desactivar</button>
                            ) : null}
                            <button className="ghost-btn" disabled={consoleBusy} onClick={() => setEditingKbId(editingKbId === article.id ? null : article.id)}>Editar</button>
                          </div>
                          {editingKbId === article.id ? (
                            <form className="audit-filters" onSubmit={(e) => void submitEditKb(e, article.id)} aria-label={`Editar artículo ${article.id.slice(0, 8)}`}>
                              <label>Pregunta<input name="question" aria-label="Pregunta en edición" defaultValue={article.question} /></label>
                              <label>Palabras clave<input name="keywords" aria-label="Palabras clave en edición" defaultValue={article.keywords.join(", ")} /></label>
                              <label>Respuesta<input name="answer" aria-label="Respuesta en edición" defaultValue={article.answer} /></label>
                              <button className="ghost-btn" type="submit" disabled={consoleBusy}>Guardar artículo</button>
                            </form>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
              <form className="audit-filters" onSubmit={(e) => void runSelfTest(e)} aria-label="Probar base de conocimiento">
                <label>Pregunta de prueba<input name="probe" aria-label="Pregunta de prueba" placeholder="¿Puedo visitar el sábado?" /></label>
                <button className="ghost-btn" type="submit" disabled={consoleBusy}>Probar</button>
              </form>
              {kbProbe ? (
                kbProbe.match ? (
                  <div className="result-box" aria-label="Resultado de la prueba">
                    <p><b>Coincidencia:</b> {kbProbe.article.question} <span className="mono">· {kbProbe.article.id.slice(0, 8)}… · {kbProbe.article.category} · {kbProbe.hits} aciertos</span></p>
                  </div>
                ) : (
                  <p className="empty-note" aria-label="Resultado de la prueba">Sin coincidencia — ningún artículo responde a esa pregunta.</p>
                )
              ) : null}
              <h3>Calendario</h3>
              {calendarConfig ? (
                <form className="audit-filters" onSubmit={(e) => void submitCalendarConfig(e)} aria-label="Configuración del calendario">
                  <label>Inicio de la ventana<input name="window_start" aria-label="Inicio de la ventana" defaultValue={calendarConfig.window_start} placeholder="09:00" /></label>
                  <label>Fin de la ventana<input name="window_end" aria-label="Fin de la ventana" defaultValue={calendarConfig.window_end} placeholder="18:00" /></label>
                  <label>Duración del turno (min)<input name="slot_minutes" aria-label="Duración del turno" type="number" defaultValue={calendarConfig.slot_minutes} /></label>
                  <label>Etiqueta de destino<input name="destination_label" aria-label="Etiqueta de destino" defaultValue={calendarConfig.destination_label ?? ""} placeholder="Oficina central" /></label>
                  <label>Zona horaria<input name="timezone" aria-label="Zona horaria" defaultValue={calendarConfig.timezone ?? ""} placeholder="America/Argentina/Buenos_Aires" /></label>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }} role="group" aria-label="Días hábiles">
                    {[0, 1, 2, 3, 4, 5, 6].map((day) => (
                      <label key={day} style={{ display: "flex", gap: 4, alignItems: "center" }}>
                        <input type="checkbox" name={`day-${day}`} aria-label={`Día ${WEEKDAY_LABELS[day]}`} defaultChecked={calendarConfig.working_days.includes(day)} />
                        {WEEKDAY_LABELS[day]}
                      </label>
                    ))}
                  </div>
                  <button className="ghost-btn" type="submit" disabled={consoleBusy}>Guardar calendario</button>
                </form>
              ) : null}
              <h3>Campos del prospecto</h3>
              <form className="audit-filters" onSubmit={(e) => void submitLeadFields(e)} aria-label="Campos del prospecto">
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }} role="group" aria-label="Columnas permitidas">
                  {LEAD_FIELD_COLUMNS.map((column) => (
                    <label key={column} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <input type="checkbox" name={`field-${column}`} aria-label={`Campo ${column}`} defaultChecked={leadFields.includes(column)} />
                      {LEAD_FIELD_LABELS[column] ?? column} <span className="mono">{column}</span>
                    </label>
                  ))}
                </div>
                <button className="ghost-btn" type="submit" disabled={consoleBusy}>Guardar campos</button>
              </form>
              <h3>Campos personalizados</h3>
              {customFieldsError ? <p className="empty-note"><code>{customFieldsError}</code></p> : null}
              {customFieldsLoading ? <p className="empty-note">Cargando campos personalizados…</p> : null}
              <form className="audit-filters" onSubmit={(e) => void submitCreateCustomField(e)} aria-label="Crear campo personalizado">
                <label>Nombre<input name="name" aria-label="Nombre del campo" placeholder="barrio" /></label>
                <label>Tipo
                  <select name="type" aria-label="Tipo de campo" defaultValue="text">
                    {CUSTOM_FIELD_TYPES.map((type) => <option key={type} value={type}>{CUSTOM_FIELD_TYPE_LABELS[type] ?? type}</option>)}
                  </select>
                </label>
                <label>Opciones (tipo selección)<input name="choices" aria-label="Opciones" placeholder="Palermo, Belgrano (separadas por coma)" /></label>
                <button className="ghost-btn" type="submit" disabled={consoleBusy}>Crear campo</button>
              </form>
              {customFields.length > 0 ? (
                <table className="data-table">
                  <thead><tr><th>Nombre</th><th>Tipo</th><th>Opciones</th><th>Acción</th></tr></thead>
                  <tbody>
                    {customFields.map((field) => (
                      <tr key={field.id}>
                        <td><strong>{field.name}</strong><br /><span className="mono">{field.id.slice(0, 8)}…</span></td>
                        <td>{CUSTOM_FIELD_TYPE_LABELS[field.type] ?? field.type}</td>
                        <td>{field.choices.length > 0 ? field.choices.join(", ") : "—"}</td>
                        <td>
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                            <button className="ghost-btn" disabled={consoleBusy} onClick={() => setEditingFieldId(editingFieldId === field.id ? null : field.id)}>Editar</button>
                            <button className="ghost-btn danger" disabled={consoleBusy} onClick={() => void runDeleteCustomField(field.id)}>Eliminar</button>
                          </div>
                          {editingFieldId === field.id ? (
                            <form className="audit-filters" onSubmit={(e) => void submitEditCustomField(e, field.id)} aria-label={`Editar campo ${field.name}`}>
                              <label>Nombre<input name="name" aria-label="Nombre en edición" defaultValue={field.name} /></label>
                              <label>Tipo
                                <select name="type" aria-label="Tipo en edición" defaultValue={field.type}>
                                  {CUSTOM_FIELD_TYPES.map((type) => <option key={type} value={type}>{CUSTOM_FIELD_TYPE_LABELS[type] ?? type}</option>)}
                                </select>
                              </label>
                              <label>Opciones<input name="choices" aria-label="Opciones en edición" defaultValue={field.choices.join(", ")} /></label>
                              <button className="ghost-btn" type="submit" disabled={consoleBusy}>Guardar campo</button>
                            </form>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
              <h3>Etiquetas</h3>
              <form className="audit-filters" onSubmit={(e) => void submitTagFilter(e)} aria-label="Filtrar contactos por etiqueta">
                <label>Etiqueta<input name="tag" aria-label="Etiqueta para filtrar" placeholder="vip" defaultValue={tagFilter} /></label>
                <button className="ghost-btn" type="submit" disabled={consoleBusy}>Filtrar etiquetas</button>
              </form>
              {tagsError ? <p className="empty-note"><code>{tagsError}</code></p> : null}
              {tagsNote ? <p className="empty-note">{tagsNote}</p> : null}
              {contacts.length > 0 ? (
                <table className="data-table">
                  <thead><tr><th>Contacto</th><th>Etiquetas</th><th>Acción</th></tr></thead>
                  <tbody>
                    {contacts.map((contact) => (
                      <tr key={contact.id}>
                        <td><strong>{contact.name}</strong><br /><span className="mono">{contact.id.slice(0, 8)}…</span></td>
                        <td>
                          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                            {contact.tags.map((tag) => (
                              <span key={tag} className="status-pill">
                                {tag}
                                <button type="button" className="calendar-del" aria-label={`Quitar etiqueta ${tag}`} disabled={consoleBusy} onClick={() => void runRemoveTag(contact.id, tag)}>×</button>
                              </span>
                            ))}
                            {contact.tags.length === 0 ? <span className="mono">sin etiquetas</span> : null}
                          </div>
                        </td>
                        <td>
                          <form className="audit-filters" onSubmit={(e) => void submitAddTag(e, contact.id)} aria-label={`Agregar etiqueta a ${contact.name}`}>
                            <label>Nueva etiqueta<input name="tag" aria-label="Nueva etiqueta" placeholder="vip" /></label>
                            <button className="ghost-btn" type="submit" disabled={consoleBusy}>Agregar</button>
                          </form>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </section>
          </section>
        </section>
      </main>
    </div>
  );
}

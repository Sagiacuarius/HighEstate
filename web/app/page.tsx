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
  type DemoApi,
} from "./demo-api";
import { RuntimeHttpError } from "../runtime-client";
import type {
  CrmInboxItem,
  DemoContextResponse,
} from "../../contracts/runtime-v1";

export const dynamic = "force-dynamic";

type ViewKey = "resumen" | "inbox" | "leads" | "properties" | "pipeline" | "agenda" | "activity";

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
  "crm.inbound.process": "Mensaje entrante procesado",
  "crm.lead.transition": "Prospecto avanzado de etapa",
  "crm.follow_up.complete": "Seguimiento completado",
  authorization_denied: "Acceso denegado",
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

  const switchAccount = async (requestedAccountId: string): Promise<void> => {
    if (!api || switching) return;
    setSwitching(true);
    setControlsNote(null);
    try {
      const resolved = await api.selectAccount(requestedAccountId);
      setAccount(resolved);
      setSelectedConversation(null);
      setThread(null);
      await reload(api);
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
        <div className="brand"><span className="brand-mark">✦</span><span>HighEstate</span></div>
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
        </section>
      </main>
    </div>
  );
}

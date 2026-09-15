import type {
  AgencyId,
  ClientAccountId,
  CorrelationId,
  TenantContext,
  UserId,
} from "../domain/tenant";
import { ClientAccountId as makeClientAccountId, AgencyId as makeAgencyId, CorrelationId as makeCorrelationId, MembershipId as makeMembershipId, UserId as makeUserId } from "../domain/tenant";
import type { CurrentIdentity } from "./ports/tenant-ports";

export class CommercialContractError extends Error {
  readonly name = "CommercialContractError";
}

export type ContactId = string & { readonly __brand: "ContactId" };
export type LeadId = string & { readonly __brand: "LeadId" };
export type PropertyId = string & { readonly __brand: "PropertyId" };
export type PipelineId = string & { readonly __brand: "PipelineId" };
export type StageId = string & { readonly __brand: "StageId" };
export type ConversationId = string & { readonly __brand: "ConversationId" };
export type MessageId = string & { readonly __brand: "MessageId" };
export type FollowUpId = string & { readonly __brand: "FollowUpId" };
export type EventId = string & { readonly __brand: "EventId" };

type CommercialIdBrand = ContactId | LeadId | PropertyId | PipelineId | StageId | ConversationId | MessageId | FollowUpId | EventId;

function commercialId<T extends CommercialIdBrand>(value: unknown, label: string): T {
  if (typeof value !== "string") throw new CommercialContractError(`${label} is invalid`);
  const normalized = value.trim();
  if (normalized !== value || normalized.length === 0 || normalized.length > 256 || !/^[A-Za-z0-9][A-Za-z0-9:_-]*$/.test(normalized)) {
    throw new CommercialContractError(`${label} is invalid`);
  }
  return normalized as T;
}

export const ContactId = (value: unknown): ContactId => commercialId<ContactId>(value, "ContactId");
export const LeadId = (value: unknown): LeadId => commercialId<LeadId>(value, "LeadId");
export const PropertyId = (value: unknown): PropertyId => commercialId<PropertyId>(value, "PropertyId");
export const PipelineId = (value: unknown): PipelineId => commercialId<PipelineId>(value, "PipelineId");
export const StageId = (value: unknown): StageId => commercialId<StageId>(value, "StageId");
export const ConversationId = (value: unknown): ConversationId => commercialId<ConversationId>(value, "ConversationId");
export const MessageId = (value: unknown): MessageId => commercialId<MessageId>(value, "MessageId");
export const FollowUpId = (value: unknown): FollowUpId => commercialId<FollowUpId>(value, "FollowUpId");
export const EventId = (value: unknown): EventId => commercialId<EventId>(value, "EventId");

export type ContactStatus = "active" | "archived";
export type LeadStatus = "new" | "qualified" | "won" | "lost";
export type OperationType = "sale" | "rent";
export type PropertyAvailability = "available" | "reserved" | "unavailable";
export type FollowUpStatus = "pending" | "completed" | "cancelled";
export type MessageDirection = "inbound" | "outbound";

export type Contact = {
  id: ContactId;
  clientAccountId: ClientAccountId;
  name: string;
  email?: string;
  phone?: string;
  tags: readonly string[];
  status: ContactStatus;
};

export type Lead = {
  id: LeadId;
  clientAccountId: ClientAccountId;
  contactId: ContactId;
  status: LeadStatus;
  stageId: StageId;
  selectedPropertyId?: PropertyId;
  source?: string;
};

export type PropertyLocation = {
  city: string;
  neighborhood?: string;
  address?: string;
};

export type Property = {
  id: PropertyId;
  clientAccountId: ClientAccountId;
  operationType: OperationType;
  location: PropertyLocation;
  bedrooms: number;
  price: number;
  currency: string;
  availability: PropertyAvailability;
};

export type Pipeline = {
  id: PipelineId;
  clientAccountId: ClientAccountId;
  name: string;
  stages: readonly Stage[];
};

export type Stage = {
  id: StageId;
  pipelineId: PipelineId;
  clientAccountId: ClientAccountId;
  name: string;
  position: number;
};

export type LeadTransitionIntent = {
  leadId: LeadId;
  toStageId: StageId;
};

export type LeadTransitionResult = {
  lead: Lead;
  previousStageId: StageId;
  currentStageId: StageId;
};

export type Conversation = {
  id: ConversationId;
  clientAccountId: ClientAccountId;
  contactId: ContactId;
  unreadCount: number;
  lastMessageAt?: string;
};

export type Message = {
  id: MessageId;
  conversationId: ConversationId;
  clientAccountId: ClientAccountId;
  direction: MessageDirection;
  body: string;
  receivedAt: string;
};

export type FollowUp = {
  id: FollowUpId;
  clientAccountId: ClientAccountId;
  leadId: LeadId;
  contactId: ContactId;
  dueAt: string;
  status: FollowUpStatus;
  note?: string;
};

export type DemoUserIntent = {
  requestedClientAccount?: RequestedClientAccountIntent;
};

export type RequestedClientAccountIntent = {
  clientAccountId: ClientAccountId;
  source: "client" | "server";
};

export type DemoContextResolver = {
  /** Resolves from trusted identity and membership; intent is only a request. */
  resolve(currentContext: TenantContext, intent: RequestedClientAccountIntent, identity: CurrentIdentity): Promise<TenantContext>;
};

export async function resolveDemoUserIntent(
  resolver: DemoContextResolver,
  intent: DemoUserIntent,
  identity?: CurrentIdentity,
  currentContext?: TenantContext,
): Promise<TenantContext> {
  if (!intent.requestedClientAccount) throw new CommercialContractError("requested account is required");
  if (!identity) throw new CommercialContractError("server identity is required");
  if (!currentContext) throw new CommercialContractError("previous TenantContext is required");
  const authenticatedUser = await identity.get();
  if (!authenticatedUser || !authenticatedUser.id) throw new CommercialContractError("authenticated user is required");
  const resolved = await resolver.resolve(currentContext, intent.requestedClientAccount, identity);
  if (!resolved || resolved.user.id !== authenticatedUser.id) {
    throw new CommercialContractError("resolved context does not match server identity");
  }
  return resolved;
}

export function requireTenantScope(context: TenantContext | undefined, requestedAccountId: ClientAccountId): ClientAccountId {
  if (!context) throw new CommercialContractError("TenantContext is required for commercial operations");
  if (context.clientAccount.id !== requestedAccountId) throw new CommercialContractError("tenant scope does not match TenantContext");
  if (context.clientAccount.agencyId !== context.agency.id || context.membership.clientAccountId !== context.clientAccount.id || context.membership.agencyId !== context.agency.id) {
    throw new CommercialContractError("TenantContext scope is inconsistent");
  }
  return context.clientAccount.id;
}

export type CommercialRelationships = {
  lead?: Lead;
  contact?: Contact;
  property?: Property;
  pipeline?: Pipeline;
  stage?: Stage;
  conversation?: Conversation;
  message?: Message;
  followUp?: FollowUp;
};

export function requireRelationshipScope(
  context: TenantContext | undefined,
  relationships: CommercialRelationships,
): ClientAccountId {
  if (!context) throw new CommercialContractError("TenantContext is required for commercial relationships");
  validateTenantContextIds(context);
  const accountId = requireTenantScope(context, context.clientAccount.id);
  validateEntityIds(relationships);
  const entities = Object.values(relationships).filter(Boolean) as ReadonlyArray<{ readonly clientAccountId: ClientAccountId }>;
  if (entities.some((entity) => entity.clientAccountId !== accountId)) {
    throw new CommercialContractError("commercial relationship crosses client-account scope");
  }
  const { lead, contact, property, pipeline, stage, conversation, message } = relationships;
  if (lead && contact && lead.contactId !== contact.id) throw new CommercialContractError("Lead does not belong to Contact");
  if (lead?.selectedPropertyId && property && lead.selectedPropertyId !== property.id) {
    throw new CommercialContractError("Lead selected property relationship is invalid");
  }
  if (stage && pipeline && stage.pipelineId !== pipeline.id) throw new CommercialContractError("Stage does not belong to Pipeline");
  if (lead && stage && lead.stageId !== stage.id) throw new CommercialContractError("Lead stage does not match Stage");
  if (conversation && contact && conversation.contactId !== contact.id) throw new CommercialContractError("Conversation does not belong to Contact");
  if (message && conversation && message.conversationId !== conversation.id) throw new CommercialContractError("Message does not belong to Conversation");
  const followUp = relationships.followUp;
  if (followUp?.leadId && lead && followUp.leadId !== lead.id) throw new CommercialContractError("FollowUp does not belong to Lead");
  if (followUp?.contactId && contact && followUp.contactId !== contact.id) throw new CommercialContractError("FollowUp does not belong to Contact");
  if (pipeline?.stages.some((pipelineStage) => pipelineStage.pipelineId !== pipeline.id)) {
    throw new CommercialContractError("Pipeline contains a Stage from another Pipeline");
  }
  if (pipeline?.stages.some((pipelineStage) => pipelineStage.clientAccountId !== accountId)) {
    throw new CommercialContractError("Pipeline contains a Stage from another ClientAccount");
  }
  return accountId;
}

function validateTenantContextIds(context: TenantContext): void {
  try {
    validateBaselineId(context.agency.id, "AgencyId", makeAgencyId);
    validateBaselineId(context.clientAccount.id, "ClientAccountId", makeClientAccountId);
    validateBaselineId(context.membership.id, "MembershipId", makeMembershipId);
    validateBaselineId(context.user.id, "UserId", makeUserId);
    validateBaselineId(context.correlationId, "CorrelationId", makeCorrelationId);
  } catch {
    throw new CommercialContractError("TenantContext contains an invalid baseline identifier");
  }
}

function validateBaselineId(value: unknown, label: string, constructor: (value: string) => unknown): void {
  if (typeof value !== "string" || value.trim() !== value || value.length === 0 || value.length > 256 || !/^[A-Za-z0-9][A-Za-z0-9:_-]*$/.test(value)) {
    throw new CommercialContractError(`${label} is invalid`);
  }
  constructor(value);
}

function validateEntityIds(relationships: CommercialRelationships): void {
  const { lead, contact, property, pipeline, stage, conversation, message, followUp } = relationships;
  if (contact) { ContactId(contact.id); validatedClientAccountId(contact.clientAccountId); }
  if (lead) { LeadId(lead.id); ContactId(lead.contactId); StageId(lead.stageId); validatedClientAccountId(lead.clientAccountId); if (lead.selectedPropertyId) PropertyId(lead.selectedPropertyId); }
  if (property) { PropertyId(property.id); validatedClientAccountId(property.clientAccountId); }
  if (pipeline) {
    PipelineId(pipeline.id); validatedClientAccountId(pipeline.clientAccountId);
    for (const pipelineStage of pipeline.stages) { StageId(pipelineStage.id); PipelineId(pipelineStage.pipelineId); validatedClientAccountId(pipelineStage.clientAccountId); }
  }
  if (stage) { StageId(stage.id); PipelineId(stage.pipelineId); validatedClientAccountId(stage.clientAccountId); }
  if (conversation) { ConversationId(conversation.id); ContactId(conversation.contactId); validatedClientAccountId(conversation.clientAccountId); }
  if (message) { MessageId(message.id); ConversationId(message.conversationId); validatedClientAccountId(message.clientAccountId); }
  if (followUp) { FollowUpId(followUp.id); LeadId(followUp.leadId); ContactId(followUp.contactId); validatedClientAccountId(followUp.clientAccountId); }
}

export type ContactLeadRepository = {
  listContacts(context: TenantContext): Promise<readonly Contact[]>;
  getContact(context: TenantContext, id: ContactId): Promise<Contact | null>;
  saveContact(context: TenantContext, contact: Contact): Promise<Contact>;
  getLead(context: TenantContext, id: LeadId): Promise<Lead | null>;
  saveLead(context: TenantContext, lead: Lead): Promise<Lead>;
};

export type PropertyRepository = {
  list(context: TenantContext): Promise<readonly Property[]>;
  get(context: TenantContext, id: PropertyId): Promise<Property | null>;
  save(context: TenantContext, property: Property): Promise<Property>;
};

export type PipelineRepository = {
  get(context: TenantContext, id: PipelineId): Promise<Pipeline | null>;
  transitionLead(context: TenantContext, intent: LeadTransitionIntent): Promise<LeadTransitionResult>;
};

export type ConversationRepository = {
  list(context: TenantContext): Promise<readonly Conversation[]>;
  getMessages(context: TenantContext, conversationId: ConversationId): Promise<readonly Message[]>;
  markRead(context: TenantContext, conversationId: ConversationId): Promise<void>;
};

export type FollowUpRepository = {
  listDue(context: TenantContext, at: string): Promise<readonly FollowUp[]>;
  save(context: TenantContext, followUp: FollowUp): Promise<FollowUp>;
  complete(context: TenantContext, id: FollowUpId): Promise<FollowUp>;
};

export type InboundMessageHandler = {
  handle(context: TenantContext, event: InboundMessageEvent): Promise<void>;
};

export type InboundMessageEvent = {
  eventId: EventId;
  clientAccountId: ClientAccountId;
  conversationId: ConversationId;
  message: Message;
};

export type TenantCommercialPorts = {
  contacts: ContactLeadRepository;
  properties: PropertyRepository;
  pipelines: PipelineRepository;
  conversations: ConversationRepository;
  followUps: FollowUpRepository;
  inboundMessages: InboundMessageHandler;
  demoContext: DemoContextResolver;
};

export function parseInboundMessageEvent(input: { route: string; payload: unknown }): InboundMessageEvent {
  if (input.route !== "/inbound-message") throw new CommercialContractError("unknown CRM route");
  if (!isRecord(input.payload) || !isNonEmptyString(input.payload.eventId) || !isNonEmptyString(input.payload.clientAccountId) || !isNonEmptyString(input.payload.conversationId) || !isRecord(input.payload.message) || !isNonEmptyString(input.payload.message.id) || !isNonEmptyString(input.payload.message.clientAccountId) || !isNonEmptyString(input.payload.message.conversationId) || input.payload.message.direction !== "inbound" || !isNonEmptyString(input.payload.message.body) || !isNonEmptyString(input.payload.message.receivedAt)) {
    throw new CommercialContractError("malformed inbound message payload");
  }
  const eventId = EventId(validatedString(input.payload.eventId, "eventId"));
  const clientAccountId = validatedClientAccountId(input.payload.clientAccountId);
  const conversationId = ConversationId(validatedString(input.payload.conversationId, "conversationId"));
  const message = input.payload.message;
  const messageClientAccountId = validatedString(message.clientAccountId, "message.clientAccountId");
  const messageConversationId = validatedString(message.conversationId, "message.conversationId");
  if (messageClientAccountId !== clientAccountId || messageConversationId !== conversationId) {
    throw new CommercialContractError("inbound message scope does not match event scope");
  }
  const timestamp = normalizeTimestamp(validatedString(message.receivedAt, "receivedAt"));
  const normalizedMessage: Message = {
    id: MessageId(validatedString(message.id, "message.id")),
    conversationId,
    clientAccountId,
    direction: "inbound",
    body: validatedString(message.body, "message.body"),
    receivedAt: timestamp,
  };
  return { eventId, clientAccountId, conversationId, message: normalizedMessage };
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function validatedString(value: unknown, field: string): string {
  if (!isNonEmptyString(value)) throw new CommercialContractError(`malformed inbound message ${field}`);
  return value.trim();
}

function normalizeTimestamp(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) throw new CommercialContractError("inbound message timestamp is invalid");
  return new Date(timestamp).toISOString();
}

function validatedClientAccountId(value: unknown): ClientAccountId {
  const normalized = validatedString(value, "clientAccountId");
  if (normalized.length > 256 || !/^[A-Za-z0-9][A-Za-z0-9:_-]*$/.test(normalized)) {
    throw new CommercialContractError("clientAccountId is invalid");
  }
  try {
    return makeClientAccountId(normalized);
  } catch {
    throw new CommercialContractError("clientAccountId is invalid");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export type ProductScope = {
  agencyId: AgencyId;
  clientAccountId: ClientAccountId;
  userId: UserId;
};

import { describe, expect, it } from "vitest";
import {
  CommercialContractError,
  ContactId,
  ConversationId,
  EventId,
  FollowUpId,
  LeadId,
  MessageId,
  PipelineId,
  PropertyId,
  StageId,
  parseInboundMessageEvent,
  requireRelationshipScope,
  requireTenantScope,
  resolveDemoUserIntent,
  type Contact,
  type ContactLeadRepository,
  type Conversation,
  type ConversationRepository,
  type DemoContextResolver,
  type DemoUserIntent,
  type FollowUp,
  type FollowUpRepository,
  type InboundMessageEvent,
  type Lead,
  type LeadTransitionIntent,
  type Message,
  type InboundMessageHandler,
  type Pipeline,
  type PipelineRepository,
  type Property,
  type PropertyRepository,
  type Stage,
  type TenantCommercialPorts,
} from "./commercial-demo";
import { Agency, AgencyId, ClientAccount, ClientAccountId, CorrelationId, Membership, MembershipId, TenantContext, User, UserId } from "../domain/tenant";

const accountA = ClientAccountId("account-a");
const accountB = ClientAccountId("account-b");
const agency = new Agency(AgencyId("agency-a"), "Agency A");
const user = new User(UserId("user-a"), "User A", "a@example.com");
const membership = new Membership(MembershipId("membership-a"), agency.id, accountA, user.id, ["viewer"], ["client_account:read"]);
const context = new TenantContext(agency, new ClientAccount(accountA, agency.id, "Account A"), user, membership, CorrelationId("corr-1"));
const identity = { get: async () => user };

const contact: Contact = { id: ContactId("contact-a"), clientAccountId: accountA, name: "Ana", tags: ["buyer"], status: "active" };
const property: Property = { id: PropertyId("property-a"), clientAccountId: accountA, operationType: "sale", location: { city: "Rosario" }, bedrooms: 2, price: 100, currency: "USD", availability: "available" };
const pipeline: Pipeline = { id: PipelineId("pipeline-a"), clientAccountId: accountA, name: "Ventas", stages: [] };
const stage: Stage = { id: StageId("stage-a"), pipelineId: pipeline.id, clientAccountId: accountA, name: "Nuevo", position: 0 };
const lead: Lead = { id: LeadId("lead-a"), clientAccountId: accountA, contactId: contact.id, status: "new", stageId: stage.id, selectedPropertyId: property.id };
const conversation: Conversation = { id: ConversationId("conversation-a"), clientAccountId: accountA, contactId: contact.id, unreadCount: 1 };
const message: Message = { id: MessageId("message-a"), conversationId: conversation.id, clientAccountId: accountA, direction: "inbound", body: "hola", receivedAt: "2026-01-01T12:00:00.000Z" };

function validInbound(overrides: Record<string, unknown> = {}): unknown {
  return { eventId: "event-a", clientAccountId: accountA, conversationId: conversation.id, message: { ...message, ...overrides } };
}

class TenantPortDoubleBase {
  readonly calls: TenantContext[] = [];
  protected receive(context: TenantContext | undefined): void {
    if (!context) throw new CommercialContractError("TenantContext is required");
    requireTenantScope(context, context.clientAccount.id);
    this.calls.push(context);
  }
}

class ContactLeadRepositoryTestDouble extends TenantPortDoubleBase implements ContactLeadRepository {
  async listContacts(context: TenantContext) { this.receive(context); return [contact]; }
  async getContact(context: TenantContext, _id: typeof contact.id) { this.receive(context); return contact; }
  async saveContact(context: TenantContext, value: Contact) { requireRelationshipScope(context, { contact: value }); this.receive(context); return value; }
  async getLead(context: TenantContext, _id: typeof lead.id) { this.receive(context); return lead; }
  async saveLead(context: TenantContext, value: Lead) { requireRelationshipScope(context, { lead: value, contact, property }); this.receive(context); return value; }
}

class PropertyRepositoryTestDouble extends TenantPortDoubleBase implements PropertyRepository {
  async list(context: TenantContext) { this.receive(context); return [property]; }
  async get(context: TenantContext, _id: typeof property.id) { this.receive(context); return property; }
  async save(context: TenantContext, value: Property) { requireRelationshipScope(context, { property: value }); this.receive(context); return value; }
}

class PipelineRepositoryTestDouble extends TenantPortDoubleBase implements PipelineRepository {
  async get(context: TenantContext, _id: typeof pipeline.id) { this.receive(context); return pipeline; }
  async transitionLead(context: TenantContext, _intent: LeadTransitionIntent): Promise<never> { this.receive(context); throw new Error("test double"); }
}

class ConversationRepositoryTestDouble extends TenantPortDoubleBase implements ConversationRepository {
  async list(context: TenantContext) { this.receive(context); return [conversation]; }
  async getMessages(context: TenantContext, _id: typeof conversation.id) { this.receive(context); return [message]; }
  async markRead(context: TenantContext, _id: typeof conversation.id) { this.receive(context); }
}

class FollowUpRepositoryTestDouble extends TenantPortDoubleBase implements FollowUpRepository {
  async listDue(context: TenantContext, _at: string) { this.receive(context); return []; }
  async save(context: TenantContext, value: FollowUp) { requireRelationshipScope(context, { followUp: value, lead, contact, property }); this.receive(context); return value; }
  async complete(context: TenantContext, _id: typeof followUpId): Promise<FollowUp> { this.receive(context); throw new Error("test double"); }
}

const followUpId = FollowUpId("follow-up-a");
class InboundMessageHandlerTestDouble extends TenantPortDoubleBase implements InboundMessageHandler {
  readonly handled: InboundMessageEvent[] = [];
  async handle(context: TenantContext, event: InboundMessageEvent) {
    if (event.clientAccountId !== event.message.clientAccountId || event.conversationId !== event.message.conversationId) {
      throw new CommercialContractError("inbound event/message scope is inconsistent");
    }
    requireRelationshipScope(context, { message: event.message });
    requireTenantScope(context, event.clientAccountId);
    this.receive(context);
    this.handled.push(event);
  }
}

class DemoContextResolverTestDouble implements DemoContextResolver {
  readonly calls: Array<{ context: TenantContext; requestedClientAccountId: string }> = [];
  async resolve(currentContext: TenantContext, intent: { clientAccountId: typeof accountA; source: "client" | "server" }, trustedIdentity: { get: () => Promise<User> }) {
    if (!currentContext) throw new CommercialContractError("TenantContext is required");
    requireTenantScope(currentContext, currentContext.clientAccount.id);
    const trustedUser = await trustedIdentity.get();
    if (!trustedUser || intent.clientAccountId !== currentContext.clientAccount.id) throw new CommercialContractError("demo account not authorized");
    this.calls.push({ context: currentContext, requestedClientAccountId: intent.clientAccountId });
    return currentContext;
  }
}

describe("commercial CRM contracts", () => {
  it("rejects unknown route and malformed/empty identifiers", () => {
    expect(() => parseInboundMessageEvent({ route: "/unknown", payload: {} })).toThrow(CommercialContractError);
    expect(() => parseInboundMessageEvent({ route: "/inbound-message", payload: { messageId: 42 } })).toThrow("malformed");
    for (const key of ["eventId", "clientAccountId", "conversationId"]) {
      expect(() => parseInboundMessageEvent({ route: "/inbound-message", payload: { ...(validInbound() as object), [key]: "   " } })).toThrow("malformed");
    }
  });

  it("rejects empty body, outbound direction and invalid message id", () => {
    expect(() => parseInboundMessageEvent({ route: "/inbound-message", payload: validInbound({ body: "" }) })).toThrow("malformed");
    expect(() => parseInboundMessageEvent({ route: "/inbound-message", payload: validInbound({ direction: "outbound" }) })).toThrow("malformed");
    expect(() => parseInboundMessageEvent({ route: "/inbound-message", payload: validInbound({ id: "   " }) })).toThrow("malformed");
    expect(() => parseInboundMessageEvent({ route: "/inbound-message", payload: validInbound({ id: "bad/id" }) })).toThrow("MessageId");
  });

  it("normalizes timestamp and creates a new validated event", () => {
    const original = validInbound({ receivedAt: "2026-01-01T09:00:00-03:00" });
    const normalized = parseInboundMessageEvent({ route: "/inbound-message", payload: original });
    expect(normalized).not.toBe(original);
    expect(normalized.eventId).toBe(EventId("event-a"));
    expect(normalized.message.receivedAt).toBe("2026-01-01T12:00:00.000Z");
  });

  it("rejects invalid timestamp and inconsistent conversation/account scope", () => {
    expect(() => parseInboundMessageEvent({ route: "/inbound-message", payload: validInbound({ receivedAt: "not-a-timestamp" }) })).toThrow("timestamp");
    expect(() => parseInboundMessageEvent({ route: "/inbound-message", payload: { ...(validInbound() as object), conversationId: "conversation-b" } })).toThrow("scope");
    expect(() => parseInboundMessageEvent({ route: "/inbound-message", payload: { ...(validInbound() as object), message: { ...message, conversationId: "conversation-b" } } })).toThrow("scope");
    expect(() => parseInboundMessageEvent({ route: "/inbound-message", payload: { ...(validInbound() as object), message: { ...message, clientAccountId: accountB } } })).toThrow("scope");
  });

  it("executes CurrentIdentity.get and rejects absent/invalid authenticated users", async () => {
    let calls = 0;
    const trusted = { get: async () => { calls += 1; return user; } };
    const resolver: DemoContextResolver = { resolve: async (_current, _intent, _identity) => context };
    await expect(resolveDemoUserIntent(resolver, { requestedClientAccount: { clientAccountId: accountB, source: "client" } }, trusted, context)).resolves.toBe(context);
    expect(calls).toBe(1);
    await expect(resolveDemoUserIntent(resolver, { requestedClientAccount: { clientAccountId: accountA, source: "client" } }, { get: async () => undefined as never }, context)).rejects.toThrow("authenticated user");
  });

  it("keeps selector as hint and rejects unauthorized/foreign/falsified accounts", async () => {
    const resolver: DemoContextResolver = { resolve: async (_current, { clientAccountId }) => clientAccountId === accountA ? context : Promise.reject(new CommercialContractError("not authorized")) };
    await expect(resolveDemoUserIntent(resolver, { requestedClientAccount: { clientAccountId: accountB, source: "client" } }, identity, context)).rejects.toThrow("not authorized");
    await expect(resolveDemoUserIntent(resolver, { requestedClientAccount: { clientAccountId: "forged-account" as never, source: "client" } }, identity, context)).rejects.toThrow("not authorized");
  });

  it("preserves prior context and rejects external scope", async () => {
    expect(() => requireTenantScope(context, accountB)).toThrow("scope");
    const resolver: DemoContextResolver = { resolve: async () => { throw new CommercialContractError("denied"); } };
    await expect(resolveDemoUserIntent(resolver, { requestedClientAccount: { clientAccountId: accountB, source: "client" } }, identity, context)).rejects.toThrow("denied");
    expect(context.clientAccount.id).toBe(accountA);
  });

  it("validates baseline TenantContext IDs at runtime", () => {
    const invalidContexts = [
      { ...context, agency: { ...context.agency, id: " " } },
      { ...context, clientAccount: { ...context.clientAccount, id: "invalid/id" } },
      { ...context, membership: { ...context.membership, id: "" } },
      { ...context, user: { ...context.user, id: "user with spaces" } },
      { ...context, correlationId: "x".repeat(257) },
    ];
    for (const invalidContext of invalidContexts) {
      expect(() => requireRelationshipScope(invalidContext as unknown as TenantContext, {})).toThrow("invalid baseline");
    }
  });

  it("validates seven explicit contract doubles and records TenantContext calls", async () => {
    const ports: TenantCommercialPorts = {
      contacts: new ContactLeadRepositoryTestDouble(),
      properties: new PropertyRepositoryTestDouble(),
      pipelines: new PipelineRepositoryTestDouble(),
      conversations: new ConversationRepositoryTestDouble(),
      followUps: new FollowUpRepositoryTestDouble(),
      inboundMessages: new InboundMessageHandlerTestDouble(),
      demoContext: new DemoContextResolverTestDouble(),
    };
    await ports.contacts.listContacts(context);
    await ports.properties.list(context);
    await ports.pipelines.get(context, pipeline.id);
    await ports.conversations.list(context);
    await ports.followUps.listDue(context, "2026-01-01T00:00:00.000Z");
    await ports.inboundMessages.handle(context, parseInboundMessageEvent({ route: "/inbound-message", payload: validInbound() }));
    await ports.demoContext.resolve(context, { clientAccountId: accountA, source: "client" }, identity);
    expect((ports.contacts as ContactLeadRepositoryTestDouble).calls).toHaveLength(1);
    expect((ports.properties as PropertyRepositoryTestDouble).calls).toHaveLength(1);
    expect((ports.pipelines as PipelineRepositoryTestDouble).calls).toHaveLength(1);
    expect((ports.conversations as ConversationRepositoryTestDouble).calls).toHaveLength(1);
    expect((ports.followUps as FollowUpRepositoryTestDouble).calls).toHaveLength(1);
    expect((ports.inboundMessages as InboundMessageHandlerTestDouble).calls).toHaveLength(1);
    expect((ports.inboundMessages as InboundMessageHandlerTestDouble).handled).toHaveLength(1);
    const demoDouble = ports.demoContext as DemoContextResolverTestDouble;
    expect(demoDouble.calls).toHaveLength(1);
    expect(demoDouble.calls[0].context).toBe(context);
    expect(demoDouble.calls[0].requestedClientAccountId).toBe(accountA);
    await expect(ports.contacts.listContacts(undefined as never)).rejects.toThrow("TenantContext");
    await expect(ports.contacts.saveContact(context, { ...contact, clientAccountId: accountB })).rejects.toThrow("crosses");
    await expect(demoDouble.resolve(undefined as never, { clientAccountId: accountA, source: "client" }, identity)).rejects.toThrow("TenantContext");
    await expect(demoDouble.resolve(context, { clientAccountId: accountB, source: "client" }, identity)).rejects.toThrow("not authorized");
    expect(demoDouble.calls).toHaveLength(1);
  });

  it("rejects FollowUp scope and related Lead/Contact mismatches", () => {
    const followUp: FollowUp = { id: FollowUpId("follow-up-a"), clientAccountId: accountA, leadId: lead.id, contactId: contact.id, dueAt: "2026-01-01T00:00:00.000Z", status: "pending" };
    expect(() => requireRelationshipScope(context, { followUp: { ...followUp, clientAccountId: accountB }, lead, contact, property })).toThrow("crosses");
    expect(() => requireRelationshipScope(context, { followUp: { ...followUp, leadId: "other-lead" as never }, lead, contact, property })).toThrow("FollowUp does not belong to Lead");
    expect(() => requireRelationshipScope(context, { followUp: { ...followUp, contactId: "other-contact" as never }, lead, contact, property })).toThrow("FollowUp does not belong to Contact");
  });

  it("rejects Lead/Property, Pipeline/Stage, Conversation/Contact and Message/Conversation crossings", () => {
    expect(() => requireRelationshipScope(context, { lead: { ...lead, contactId: "other-contact" as never }, contact })).toThrow("Lead does not belong");
    expect(() => requireRelationshipScope(context, { lead: { ...lead, selectedPropertyId: "other-property" as never }, contact, property })).toThrow("selected property");
    expect(() => requireRelationshipScope(context, { lead: { ...lead, stageId: StageId("other-stage") }, stage })).toThrow("Lead stage does not match Stage");
    expect(() => requireRelationshipScope(context, { pipeline: { ...pipeline, stages: [{ ...stage, pipelineId: "other-pipeline" as never }] } })).toThrow("another Pipeline");
    expect(() => requireRelationshipScope(context, { pipeline: { ...pipeline, stages: [{ ...stage, clientAccountId: accountB }] } })).toThrow("another ClientAccount");
    expect(() => requireRelationshipScope(context, { conversation: { ...conversation, contactId: "other-contact" as never }, contact })).toThrow("Conversation does not belong");
    expect(() => requireRelationshipScope(context, { message: { ...message, conversationId: "other-conversation" as never }, conversation, contact })).toThrow("Message does not belong");
  });

  it("executes the explicit InboundMessageHandler test double and rejects scope without mutation", async () => {
    const handler = new InboundMessageHandlerTestDouble();
    const event = parseInboundMessageEvent({ route: "/inbound-message", payload: validInbound() });
    await handler.handle(context, event);
    expect(handler.handled).toHaveLength(1);
    const foreign = { ...event, clientAccountId: accountB };
    await expect(handler.handle(context, foreign)).rejects.toThrow("inbound event/message scope");
    const inconsistentConversation = { ...event, conversationId: "conversation-b" as never };
    await expect(handler.handle(context, inconsistentConversation)).rejects.toThrow("inbound event/message scope");
    const inconsistentMessage = { ...event, message: { ...event.message, clientAccountId: accountB } };
    await expect(handler.handle(context, inconsistentMessage)).rejects.toThrow("inbound event/message scope");
    expect(handler.handled).toHaveLength(1);
    expect(handler.calls).toHaveLength(1);
  });
});

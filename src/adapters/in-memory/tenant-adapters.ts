import type {
  AuditEntry,
  CurrentIdentity,
  CurrentTenantContext,
  DomainEvent,
  DomainEventPublisher,
  MembershipRepository,
  TenantRepository,
  TenantResolution,
  TenantScopedRepository,
} from "../../application/ports";
import {
  Agency,
  AgencyId,
  ClientAccount,
  ClientAccountId,
  IdempotencyKey,
  Membership,
  TenantContext,
  User,
  UserId,
  type CorrelationId,
} from "../../domain/tenant";
import { assertTenantScope } from "../../application/ports";

export class InMemoryCurrentIdentity implements CurrentIdentity {
  constructor(private readonly authenticatedUser: User) {}
  async get(): Promise<User> { return this.authenticatedUser; }
}

export class InMemoryCurrentTenantContext implements CurrentTenantContext {
  constructor(private context: TenantContext) {}
  async get(): Promise<TenantContext> { return this.context; }
  set(context: TenantContext): void { this.context = context; }
}

export class InMemoryTenantRepository implements TenantRepository {
  private readonly agencies = new Map<string, Agency>();
  private readonly accounts = new Map<string, ClientAccount>();

  addAgency(agency: Agency): void { this.agencies.set(agency.id, agency); }
  addClientAccount(account: ClientAccount): void {
    if (!this.agencies.has(account.agencyId)) throw new Error("Agency must exist before ClientAccount");
    const existing = this.accounts.get(account.id);
    if (existing && existing.agencyId !== account.agencyId) throw new Error("ClientAccount cannot move to another Agency");
    this.accounts.set(account.id, account);
  }

  async findAgencyByClientAccount(input: { clientAccountId: ClientAccountId }): Promise<TenantResolution | null> {
    const account = this.accounts.get(input.clientAccountId);
    if (!account) return null;
    const agency = this.agencies.get(account.agencyId);
    return agency ? { agency, clientAccount: account } : null;
  }
}

export class InMemoryMembershipRepository implements MembershipRepository {
  private readonly memberships = new Map<string, Membership>();
  private readonly idempotency = new Map<string, { context: TenantContext; targetClientAccountId: ClientAccountId }>();

  add(membership: Membership): void { this.memberships.set(`${membership.userId}:${membership.clientAccountId}`, membership); }
  async findForUserAndClientAccount(input: { userId: UserId; clientAccountId: ClientAccountId }): Promise<Membership | null> {
    return this.memberships.get(`${input.userId}:${input.clientAccountId}`) ?? null;
  }
  async findByIdempotencyKey(input: { userId: UserId; idempotencyKey: IdempotencyKey }): Promise<{ context: TenantContext; targetClientAccountId: ClientAccountId } | null> {
    return this.idempotency.get(`${input.userId}:${input.idempotencyKey}`) ?? null;
  }
  async saveIdempotencyResult(input: { userId: UserId; idempotencyKey: IdempotencyKey; context: TenantContext; targetClientAccountId: ClientAccountId }): Promise<void> {
    this.idempotency.set(`${input.userId}:${input.idempotencyKey}`, input);
  }
}

export type InMemoryResource = { id: string; clientAccountId: ClientAccountId; value: string };

export class InMemoryTenantScopedRepository implements TenantScopedRepository<InMemoryResource> {
  private readonly records = new Map<string, InMemoryResource>();
  private writes = 0;

  seed(resource: InMemoryResource): void { this.records.set(resource.id, { ...resource }); }
  get writeCount(): number { return this.writes; }
  snapshot(): InMemoryResource[] { return [...this.records.values()].map((resource) => ({ ...resource })); }

  async read(tenantContext: TenantContext, id: string): Promise<InMemoryResource | null> {
    const resource = this.records.get(id);
    if (!resource) return null;
    assertTenantScope(tenantContext, resource.clientAccountId);
    return { ...resource };
  }
  async create(tenantContext: TenantContext, resource: InMemoryResource): Promise<InMemoryResource> {
    assertTenantScope(tenantContext, resource.clientAccountId);
    if (this.records.has(resource.id)) throw new Error("Resource already exists");
    this.records.set(resource.id, { ...resource }); this.writes += 1;
    return { ...resource };
  }
  async update(tenantContext: TenantContext, id: string, patch: Partial<InMemoryResource>): Promise<InMemoryResource> {
    const current = this.records.get(id);
    if (!current) throw new Error("Resource not found");
    assertTenantScope(tenantContext, current.clientAccountId);
    if (patch.clientAccountId && patch.clientAccountId !== current.clientAccountId) throw new Error("Cross-account update rejected");
    const next = { ...current, ...patch, clientAccountId: current.clientAccountId };
    this.records.set(id, next); this.writes += 1;
    return { ...next };
  }
  async delete(tenantContext: TenantContext, id: string): Promise<void> {
    const current = this.records.get(id);
    if (!current) return;
    assertTenantScope(tenantContext, current.clientAccountId);
    this.records.delete(id); this.writes += 1;
  }
}

export class InMemoryAuditSink {
  readonly entries: AuditEntry[] = [];
  async append(entry: AuditEntry): Promise<void> { this.entries.push(entry); }
  successEntries(): AuditEntry[] { return this.entries.filter((entry) => entry.action === "client_account_switched"); }
}

export class InMemoryDomainEventPublisher implements DomainEventPublisher {
  readonly events: DomainEvent[] = [];
  async publish(event: DomainEvent): Promise<void> { this.events.push(event); }
  successEvents(): DomainEvent[] { return this.events.filter((event) => event.type === "TenantContextSwitched"); }
}

export function buildInMemoryIdentity(userId: string, name = "Scenario user"): User {
  return new User(UserId(userId), name, `${userId}@example.test`);
}

export function buildInMemoryAgency(id: string, name = id): Agency {
  return new Agency(AgencyId(id), name);
}

export function buildInMemoryAccount(id: string, agencyId: AgencyId, name = id): ClientAccount {
  return new ClientAccount(ClientAccountId(id), agencyId, name);
}

export function buildInMemoryKey(value: string): IdempotencyKey { return IdempotencyKey(value); }
export function buildInMemoryCorrelation(value: string): CorrelationId { return value as CorrelationId; }

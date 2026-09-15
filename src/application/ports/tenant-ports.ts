import type {
  Agency,
  AgencyId,
  ClientAccount,
  ClientAccountId,
  CorrelationId,
  IdempotencyKey,
  Membership,
  Permission,
  TenantContext,
  User,
  UserId,
} from "../../domain/tenant";

export type CurrentIdentity = {
  /** Adapter responsibility: return the authenticated server-side user; never client claims. */
  get(): Promise<User>;
};

export type IdentityAdapter = CurrentIdentity;

export type CurrentTenantContext = {
  /** Adapter responsibility: return the already-resolved authenticated context. */
  get(): Promise<TenantContext>;
};

/**
 * The adapter implementing CurrentIdentity/CurrentTenantContext owns the
 * authentication integration. Callers cannot supply a user, agency or
 * current account as authorization authority.
 */

export type TenantResolution = {
  agency: Agency;
  clientAccount: ClientAccount;
};

export type MembershipRepository = {
  findForUserAndClientAccount(input: {
    userId: UserId;
    clientAccountId: ClientAccountId;
  }): Promise<Membership | null>;
  findByIdempotencyKey(input: {
    userId: UserId;
    idempotencyKey: IdempotencyKey;
  }): Promise<{ context: TenantContext; targetClientAccountId: ClientAccountId } | null>;
};

export type TenantRepository = {
  findAgencyByClientAccount(input: { clientAccountId: ClientAccountId }): Promise<TenantResolution | null>;
};

export type AuditEntry = {
  action: "tenant_context_resolved" | "client_account_switched" | "authorization_denied";
  agencyId?: AgencyId;
  clientAccountId?: ClientAccountId;
  userId: UserId;
  correlationId: CorrelationId;
  idempotencyKey?: IdempotencyKey;
  reason?: string;
};

export type AuditSink = { append(entry: AuditEntry): Promise<void> };

export type DomainEvent = {
  type: "TenantContextSwitched" | "AuthorizationDenied";
  agencyId: AgencyId;
  clientAccountId: ClientAccountId;
  userId: UserId;
  correlationId: CorrelationId;
  idempotencyKey?: IdempotencyKey;
};

export type DomainEventPublisher = { publish(event: DomainEvent): Promise<void> };

/**
 * Every adapter implementation MUST use tenantContext.clientAccountId as
 * the mandatory predicate for read/create/update/delete. It MUST NOT resolve
 * a tenant from an external input, request body, client claim, or resource
 * id. Missing, mismatched, or cross-account contexts must be rejected before
 * any storage operation. Durable enforcement belongs to a future adapter;
 * this port intentionally contains no infrastructure.
 */
export type TenantScopedRepository<TResource extends { clientAccountId: ClientAccountId }> = {
  read(tenantContext: TenantContext, id: string): Promise<TResource | null>;
  create(tenantContext: TenantContext, resource: TResource): Promise<TResource>;
  update(tenantContext: TenantContext, id: string, patch: Partial<TResource>): Promise<TResource>;
  delete(tenantContext: TenantContext, id: string): Promise<void>;
};

/**
 * Future adapters must apply this predicate to every operation and may not
 * infer the account from id, payload, user input, or an untrusted scope.
 */
export function requiredTenantPredicate(tenantContext: TenantContext): ClientAccountId {
  if (!tenantContext) throw new Error("TenantContext is required");
  return tenantContext.clientAccount.id;
}

export function assertTenantScope(context: TenantContext | undefined, resourceAccountId: ClientAccountId): void {
  if (!context) throw new Error("TenantContext is required");
  if (!context.allows("client_account:read") && !context.allows("client_account:write")) {
    throw new Error("TenantContext has no client-account permission");
  }
  if (resourceAccountId !== context.clientAccount.id) {
    throw new Error("Resource is outside the authorized TenantContext");
  }
}

export function assertDerivedPermissions(context: TenantContext, membership: Membership): void {
  if (context.permissions.some((permission) => !membership.permissions.includes(permission))) {
    throw new Error("TenantContext contains permissions not granted by Membership");
  }
}

export type TenantPermission = Permission;

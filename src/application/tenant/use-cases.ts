import {
  CrossAccountAccessDenied,
  IdempotencyConflict,
  InvalidTenantContext,
  MembershipNotFound,
  UnauthorizedClientAccountAccess,
  type AgencyId,
  type ClientAccountId,
  type CorrelationId,
  type IdempotencyKey,
  type UserId,
  TenantContext,
} from "../../domain/tenant";
import type {
  AuditSink,
  CurrentIdentity,
  DomainEventPublisher,
  MembershipRepository,
  TenantRepository,
} from "../ports";

export type ResolveAuthorizedTenantContextInput = {
  targetClientAccountId: ClientAccountId;
  correlationId: CorrelationId;
  idempotencyKey?: IdempotencyKey;
  requestedAgencyId?: string;
};

export class ResolveAuthorizedTenantContext {
  constructor(
    private readonly identity: CurrentIdentity,
    private readonly tenants: TenantRepository,
    private readonly memberships: MembershipRepository,
    private readonly audit: AuditSink,
    private readonly events: DomainEventPublisher,
  ) {}

  async execute(input: ResolveAuthorizedTenantContextInput): Promise<TenantContext> {
    const user = await this.identity.get();
    const resolution = await this.tenants.findAgencyByClientAccount({
      clientAccountId: input.targetClientAccountId,
    });
    if (!resolution || (input.requestedAgencyId && resolution.agency.id !== input.requestedAgencyId)) {
      await this.deny(user.id, input, "forged_or_unknown_scope");
      throw new UnauthorizedClientAccountAccess();
    }

    const membership = await this.memberships.findForUserAndClientAccount({
      userId: user.id,
      clientAccountId: input.targetClientAccountId,
    });
    if (!membership) {
      await this.deny(user.id, input, "explicit_membership_not_found", resolution.agency.id);
      throw new MembershipNotFound();
    }
    if (membership.agencyId !== resolution.agency.id || membership.clientAccountId !== resolution.clientAccount.id || membership.userId !== user.id) {
      await this.deny(user.id, input, "cross_account_membership", resolution.agency.id);
      throw new CrossAccountAccessDenied();
    }
    if (!membership.allows("client_account:read") && !membership.allows("client_account:write")) {
      await this.deny(user.id, input, "missing_client_account_permission", resolution.agency.id);
      throw new UnauthorizedClientAccountAccess();
    }

    const context = new TenantContext(
      resolution.agency,
      resolution.clientAccount,
      user,
      membership,
      input.correlationId,
      input.idempotencyKey,
      membership.permissions,
    );
    await this.audit.append({
      action: "tenant_context_resolved",
      agencyId: resolution.agency.id,
      clientAccountId: resolution.clientAccount.id,
      userId: user.id,
      correlationId: input.correlationId,
      idempotencyKey: input.idempotencyKey,
    });
    return context;
  }

  private async deny(userId: UserId, input: ResolveAuthorizedTenantContextInput, reason: string, agencyId?: AgencyId): Promise<void> {
    await this.audit.append({ action: "authorization_denied", agencyId, clientAccountId: input.targetClientAccountId, userId, correlationId: input.correlationId, idempotencyKey: input.idempotencyKey, reason });
    if (agencyId) await this.events.publish({ type: "AuthorizationDenied", agencyId, clientAccountId: input.targetClientAccountId, userId, correlationId: input.correlationId, idempotencyKey: input.idempotencyKey });
  }
}

export type SwitchClientAccountInput = {
  targetClientAccountId: ClientAccountId;
  correlationId: CorrelationId;
  idempotencyKey: IdempotencyKey;
};

export class SwitchClientAccount {
  constructor(
    private readonly identity: CurrentIdentity,
    private readonly currentContext: import("../ports").CurrentTenantContext,
    private readonly resolver: ResolveAuthorizedTenantContext,
    private readonly memberships: MembershipRepository,
    private readonly audit: AuditSink,
    private readonly events: DomainEventPublisher,
  ) {}

  async execute(input: SwitchClientAccountInput): Promise<TenantContext> {
    const user = await this.identity.get();
    const previousContext = await this.currentContext.get();
    if (previousContext.user.id !== user.id) throw new InvalidTenantContext("Current context does not match trusted identity");
    if (!previousContext.allows("client_account:switch")) throw new UnauthorizedClientAccountAccess();

    const previous = await this.memberships.findByIdempotencyKey({ userId: user.id, idempotencyKey: input.idempotencyKey });
    if (previous) {
      if (previous.targetClientAccountId !== input.targetClientAccountId) throw new IdempotencyConflict();
      return previous.context;
    }

    // targetClientAccountId is only a requested intention. The resolver
    // revalidates agency ownership, membership and permissions server-side.
    const next = await this.resolver.execute({ targetClientAccountId: input.targetClientAccountId, correlationId: input.correlationId, idempotencyKey: input.idempotencyKey });
    await this.audit.append({ action: "client_account_switched", agencyId: next.agency.id, clientAccountId: next.clientAccount.id, userId: user.id, correlationId: input.correlationId, idempotencyKey: input.idempotencyKey });
    await this.events.publish({ type: "TenantContextSwitched", agencyId: next.agency.id, clientAccountId: next.clientAccount.id, userId: user.id, correlationId: input.correlationId, idempotencyKey: input.idempotencyKey });
    return next;
  }
}

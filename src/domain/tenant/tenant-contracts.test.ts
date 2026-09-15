import { describe, expect, it } from "vitest";
import {
  Agency,
  AgencyId,
  ClientAccount,
  ClientAccountId,
  CorrelationId,
  CrossAccountAccessDenied,
  IdempotencyConflict,
  IdempotencyKey,
  InvalidTenantContext,
  Membership,
  MembershipId,
  MembershipNotFound,
  TenantContext,
  UnauthorizedClientAccountAccess,
  User,
  UserId,
} from "./index";
import { ResolveAuthorizedTenantContext, SwitchClientAccount } from "../../application/tenant";
import type { AuditEntry, CurrentIdentity, CurrentTenantContext, DomainEvent, MembershipRepository, TenantRepository, TenantScopedRepository } from "../../application/ports";
import { assertTenantScope } from "../../application/ports";

async function verifyTenantContracts(): Promise<void> {
  const agency = new Agency(AgencyId("agency-a"), "Agency A");
  const accountA = new ClientAccount(ClientAccountId("account-a"), agency.id, "Account A");
  const accountB = new ClientAccount(ClientAccountId("account-b"), agency.id, "Account B");
  const otherAgency = new Agency(AgencyId("agency-b"), "Agency B");
  const foreignAccount = new ClientAccount(ClientAccountId("account-foreign"), otherAgency.id, "Foreign");
  const user = new User(UserId("user-a"), "User A", "user@example.com");
  const membershipA = new Membership(MembershipId("membership-a"), agency.id, accountA.id, user.id, ["operator"], ["client_account:read", "client_account:switch"]);
  const membershipB = new Membership(MembershipId("membership-b"), agency.id, accountB.id, user.id, ["operator"], ["client_account:read", "client_account:switch"]);
  const contextA = new TenantContext(agency, accountA, user, membershipA, CorrelationId("correlation-a"), IdempotencyKey("idempotency-a"));

  if (!Object.isFrozen(contextA)) throw new Error("TenantContext must be immutable");
  let forgedContextRejected = false;
  try { new TenantContext(agency, accountB, user, membershipA, CorrelationId("forged")); } catch (error) { forgedContextRejected = error instanceof InvalidTenantContext; }
  if (!forgedContextRejected) throw new Error("forged scope must be rejected");

  const identity: CurrentIdentity = { get: async () => user };
  const audits: AuditEntry[] = [];
  const events: DomainEvent[] = [];
  const resolutions = new Map([[accountA.id, { agency, clientAccount: accountA }], [accountB.id, { agency, clientAccount: accountB }], [foreignAccount.id, { agency: otherAgency, clientAccount: foreignAccount }]]);
  const memberships = new Map([[accountA.id, membershipA], [accountB.id, membershipB]]);
  const replay = new Map<string, { context: TenantContext; targetClientAccountId: ClientAccountId }>();
  const tenantRepository: TenantRepository = { findAgencyByClientAccount: async ({ clientAccountId }) => resolutions.get(clientAccountId) ?? null };
  const membershipRepository: MembershipRepository = {
    findForUserAndClientAccount: async ({ userId, clientAccountId }) => userId === user.id ? memberships.get(clientAccountId) ?? null : null,
    findByIdempotencyKey: async ({ userId, idempotencyKey }) => userId === user.id ? replay.get(idempotencyKey) ?? null : null,
  };
  const audit = { append: async (entry: AuditEntry) => { audits.push(entry); } };
  const publisher = { publish: async (event: DomainEvent) => { events.push(event); } };
  const resolver = new ResolveAuthorizedTenantContext(identity, tenantRepository, membershipRepository, audit, publisher);

  const resolvedA = await resolver.execute({ targetClientAccountId: accountA.id, correlationId: CorrelationId("resolve-a") });
  if (resolvedA.clientAccount.id !== accountA.id || resolvedA.user.id !== user.id) throw new Error("authorized resolution failed");
  if (resolvedA.permissions.some((permission) => !membershipA.permissions.includes(permission))) throw new Error("permissions were not derived from membership");
  let forgedAgencyRejected = false;
  try { await resolver.execute({ targetClientAccountId: accountA.id, requestedAgencyId: otherAgency.id, correlationId: CorrelationId("forged-agency") }); } catch (error) { forgedAgencyRejected = error instanceof UnauthorizedClientAccountAccess; }
  if (!forgedAgencyRejected) throw new Error("forged agency scope must be rejected");

  const scopedRepository: TenantScopedRepository<{ clientAccountId: ClientAccountId; value: string }> = {
    read: async (context) => { assertTenantScope(context, accountA.id); return { clientAccountId: accountA.id, value: "read" }; },
    create: async (context, resource) => { assertTenantScope(context, resource.clientAccountId); return resource; },
    update: async (context) => { assertTenantScope(context, accountA.id); return { clientAccountId: accountA.id, value: "update" }; },
    delete: async (context) => { assertTenantScope(context, accountA.id); },
  };
  await scopedRepository.read(contextA, "resource-a");
  await scopedRepository.create(contextA, { clientAccountId: accountA.id, value: "create" });
  let crossReadRejected = false;
  try { await scopedRepository.read(new TenantContext(agency, accountB, user, membershipB, CorrelationId("context-b")), "resource-b"); } catch { crossReadRejected = true; }
  if (!crossReadRejected) throw new Error("cross-account read must be rejected");
  let crossWriteRejected = false;
  try { await scopedRepository.create(contextA, { clientAccountId: accountB.id, value: "cross-write" }); } catch { crossWriteRejected = true; }
  if (!crossWriteRejected) throw new Error("cross-account write must be rejected");

  const currentContext: CurrentTenantContext = { get: async () => contextA };
  const switcher = new SwitchClientAccount(identity, currentContext, resolver, membershipRepository, audit, publisher);
  const switched = await switcher.execute({ targetClientAccountId: accountB.id, correlationId: CorrelationId("switch-b"), idempotencyKey: IdempotencyKey("switch-key") });
  replay.set("switch-key", { context: switched, targetClientAccountId: accountB.id });
  if (switched.clientAccount.id !== accountB.id) throw new Error("authorized switch failed");
  const previousContext = contextA;
  let unauthorizedRejected = false;
  try { await switcher.execute({ targetClientAccountId: foreignAccount.id, correlationId: CorrelationId("switch-foreign"), idempotencyKey: IdempotencyKey("foreign-key") }); } catch (error) { unauthorizedRejected = error instanceof MembershipNotFound || error instanceof UnauthorizedClientAccountAccess || error instanceof CrossAccountAccessDenied; }
  if (!unauthorizedRejected || previousContext.clientAccount.id !== accountA.id) throw new Error("unauthorized switch must be fail-closed and preserve context");

  replay.set("reuse-key", { context: contextA, targetClientAccountId: accountA.id });
  let idempotencyRejected = false;
  try { await switcher.execute({ targetClientAccountId: accountB.id, correlationId: CorrelationId("idempotency-conflict"), idempotencyKey: IdempotencyKey("reuse-key") }); } catch (error) { idempotencyRejected = error instanceof IdempotencyConflict; }
  if (!idempotencyRejected) throw new Error("idempotency target conflict must be rejected");
  if (audits.length === 0 || events.length === 0) throw new Error("authorization outcomes must be auditable and publish events");
}

describe("Agency → ClientAccount contracts", () => {
  it("passes all pure authorization, isolation and idempotency scenarios", async () => {
    await expect(verifyTenantContracts()).resolves.toBeUndefined();
  });
});

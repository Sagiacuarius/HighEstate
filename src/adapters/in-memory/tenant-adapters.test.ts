import { describe, expect, it } from "vitest";
import { ResolveAuthorizedTenantContext, SwitchClientAccount } from "../../application/tenant";
import { AgencyId, ClientAccountId, CorrelationId, IdempotencyConflict, IdempotencyKey, Membership, MembershipId, TenantContext } from "../../domain/tenant";
import {
  InMemoryCurrentIdentity,
  InMemoryCurrentTenantContext,
  InMemoryDomainEventPublisher,
  InMemoryMembershipRepository,
  InMemoryTenantRepository,
  InMemoryTenantScopedRepository,
  InMemoryAuditSink,
  buildInMemoryAccount,
  buildInMemoryAgency,
  buildInMemoryIdentity,
} from "./tenant-adapters";

function setup() {
  const agency = buildInMemoryAgency("agency-a", "Agency A");
  const accountA = buildInMemoryAccount("account-a", agency.id, "Account A");
  const accountB = buildInMemoryAccount("account-b", agency.id, "Account B");
  const foreignAgency = buildInMemoryAgency("agency-b", "Agency B");
  const foreignAccount = buildInMemoryAccount("account-foreign", foreignAgency.id, "Foreign");
  const user = buildInMemoryIdentity("user-a");
  const otherUser = buildInMemoryIdentity("user-b");
  const membershipA = new Membership(MembershipId("membership-a"), agency.id, accountA.id, user.id, ["operator"], ["client_account:read", "client_account:write", "client_account:switch"]);
  const membershipB = new Membership(MembershipId("membership-b"), agency.id, accountB.id, user.id, ["operator"], ["client_account:read", "client_account:write", "client_account:switch"]);
  const tenantRepository = new InMemoryTenantRepository();
  tenantRepository.addAgency(agency); tenantRepository.addAgency(foreignAgency);
  tenantRepository.addClientAccount(accountA); tenantRepository.addClientAccount(accountB); tenantRepository.addClientAccount(foreignAccount);
  const membershipRepository = new InMemoryMembershipRepository();
  membershipRepository.add(membershipA); membershipRepository.add(membershipB);
  const identity = new InMemoryCurrentIdentity(user);
  const initialContext = new TenantContext(agency, accountA, user, membershipA, CorrelationId("initial"), IdempotencyKey("initial-key"));
  const currentContext = new InMemoryCurrentTenantContext(initialContext);
  const audit = new InMemoryAuditSink();
  const events = new InMemoryDomainEventPublisher();
  const resolver = new ResolveAuthorizedTenantContext(identity, tenantRepository, membershipRepository, audit, events);
  const switcher = new SwitchClientAccount(identity, currentContext, resolver, membershipRepository, audit, events);
  return { agency, accountA, accountB, foreignAccount, user, otherUser, membershipA, membershipB, tenantRepository, membershipRepository, identity, initialContext, currentContext, audit, events, resolver, switcher };
}

describe("Work Unit 3 in-memory tenant adapters", () => {
  it("allows an authorized administrator to switch A to B", async () => {
    const s = setup();
    const next = await s.switcher.execute({ targetClientAccountId: s.accountB.id, correlationId: CorrelationId("switch"), idempotencyKey: IdempotencyKey("switch-key") });
    expect(next.clientAccount.id).toBe(s.accountB.id);
    expect(s.audit.successEntries()).toHaveLength(1);
    expect(s.events.successEvents()).toHaveLength(1);
  });

  it("rejects a non-member and preserves the prior context", async () => {
    const s = setup();
    const identity = new InMemoryCurrentIdentity(s.otherUser);
    const resolver = new ResolveAuthorizedTenantContext(identity, s.tenantRepository, s.membershipRepository, s.audit, s.events);
    const switcher = new SwitchClientAccount(identity, s.currentContext, resolver, s.membershipRepository, s.audit, s.events);
    await expect(switcher.execute({ targetClientAccountId: s.accountB.id, correlationId: CorrelationId("denied"), idempotencyKey: IdempotencyKey("denied-key") })).rejects.toThrow();
    expect((await s.currentContext.get()).clientAccount.id).toBe(s.accountA.id);
    expect(s.audit.successEntries()).toHaveLength(0);
    expect(s.events.successEvents()).toHaveLength(0);
  });

  it("rejects falsified agency and account scopes", async () => {
    const s = setup();
    await expect(s.resolver.execute({ targetClientAccountId: s.accountA.id, requestedAgencyId: "agency-forged", correlationId: CorrelationId("forged-agency") })).rejects.toThrow();
    await expect(s.resolver.execute({ targetClientAccountId: s.foreignAccount.id, correlationId: CorrelationId("foreign-account") })).rejects.toThrow();
    expect(s.audit.entries.filter((entry) => entry.action === "authorization_denied")).toHaveLength(2);
    expect(s.events.successEvents()).toHaveLength(0);
  });

  it("rejects cross-account read, create, update and delete without modifying records", async () => {
    const s = setup();
    const repository = new InMemoryTenantScopedRepository();
    repository.seed({ id: "a-1", clientAccountId: s.accountA.id, value: "A" });
    repository.seed({ id: "b-1", clientAccountId: s.accountB.id, value: "B" });
    const before = repository.snapshot();
    const contextA = s.initialContext;
    await expect(repository.read(contextA, "b-1")).rejects.toThrow();
    await expect(repository.create(contextA, { id: "b-2", clientAccountId: s.accountB.id, value: "forged" })).rejects.toThrow();
    await expect(repository.update(contextA, "b-1", { value: "changed" })).rejects.toThrow();
    const contextB = new TenantContext(s.agency, s.accountB, s.user, s.membershipB, CorrelationId("context-b"));
    await expect(repository.delete(contextA, "b-1")).rejects.toThrow();
    await repository.read(contextB, "b-1");
    expect(repository.snapshot()).toEqual(before);
    expect(repository.writeCount).toBe(0);
  });

  it("rejects missing context and enforces membership-derived permissions", async () => {
    const s = setup();
    const repository = new InMemoryTenantScopedRepository();
    repository.seed({ id: "a-1", clientAccountId: s.accountA.id, value: "A" });
    await expect(repository.read(undefined as never, "a-1")).rejects.toThrow();
    expect(() => new TenantContext(s.agency, s.accountA, s.user, s.membershipA, CorrelationId("extra"), undefined, ["agency:admin"])).toThrow();
    expect(s.initialContext.permissions).toEqual(s.membershipA.permissions);
  });

  it("rejects idempotency key reuse for another target", async () => {
    const s = setup();
    await s.membershipRepository.saveIdempotencyResult({ userId: s.user.id, idempotencyKey: IdempotencyKey("reuse"), context: s.initialContext, targetClientAccountId: s.accountA.id });
    await expect(s.switcher.execute({ targetClientAccountId: s.accountB.id, correlationId: CorrelationId("conflict"), idempotencyKey: IdempotencyKey("reuse") })).rejects.toBeInstanceOf(IdempotencyConflict);
    expect(s.audit.successEntries()).toHaveLength(0);
    expect(s.events.successEvents()).toHaveLength(0);
  });
});

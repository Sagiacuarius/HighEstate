import {
  type AgencyId,
  type ClientAccountId,
  type CorrelationId,
  type IdempotencyKey,
  type MembershipId,
  type Permission,
  type Role,
  type UserId,
  permissionsOf,
  rolesOf,
} from "./identifiers";
import { InvalidTenantContext } from "./errors";

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} cannot be empty`);
  return normalized;
}

export class Agency {
  readonly kind = "Agency" as const;
  constructor(
    readonly id: AgencyId,
    readonly name: string,
    readonly createdAt: Date = new Date(),
  ) {
    required(name, "Agency name");
  }
}

export class ClientAccount {
  readonly kind = "ClientAccount" as const;
  constructor(
    readonly id: ClientAccountId,
    readonly agencyId: AgencyId,
    readonly name: string,
    readonly createdAt: Date = new Date(),
  ) {
    required(name, "Client account name");
  }
}

export class User {
  readonly kind = "User" as const;
  constructor(
    readonly id: UserId,
    readonly displayName: string,
    readonly email: string,
  ) {
    required(displayName, "User display name");
    required(email, "User email");
  }
}

export class Membership {
  readonly kind = "Membership" as const;
  readonly roles: readonly Role[];
  readonly permissions: readonly Permission[];

  constructor(
    readonly id: MembershipId,
    readonly agencyId: AgencyId,
    readonly clientAccountId: ClientAccountId,
    readonly userId: UserId,
    roles: readonly Role[],
    permissions: readonly Permission[],
  ) {
    if (roles.length === 0) throw new Error("Membership requires at least one role");
    this.roles = rolesOf(roles);
    this.permissions = permissionsOf(permissions);
  }

  allows(permission: Permission): boolean {
    return this.permissions.includes(permission);
  }
}

export class TenantContext {
  readonly kind = "TenantContext" as const;
  readonly permissions: readonly Permission[];
  readonly resolvedAt: string;

  constructor(
    readonly agency: Agency,
    readonly clientAccount: ClientAccount,
    readonly user: User,
    readonly membership: Membership,
    readonly correlationId: CorrelationId,
    readonly idempotencyKey?: IdempotencyKey,
    permissions: readonly Permission[] = membership.permissions,
    resolvedAt: string | Date = new Date().toISOString(),
  ) {
    if (clientAccount.agencyId !== agency.id || membership.agencyId !== agency.id) {
      throw new InvalidTenantContext("Agency ownership does not match tenant context");
    }
    if (membership.clientAccountId !== clientAccount.id || membership.userId !== user.id) {
      throw new InvalidTenantContext("Membership does not match tenant context");
    }
    if (permissions.some((permission) => !membership.permissions.includes(permission))) {
      throw new InvalidTenantContext("TenantContext permissions must be derived from Membership");
    }
    this.permissions = permissionsOf(permissions);
    const resolvedTimestamp = typeof resolvedAt === "string" ? resolvedAt : resolvedAt.toISOString();
    if (Number.isNaN(Date.parse(resolvedTimestamp))) throw new InvalidTenantContext("Resolved timestamp is invalid");
    this.resolvedAt = resolvedTimestamp;
    Object.freeze(this.permissions);
    Object.freeze(this);
  }

  allows(permission: Permission): boolean {
    return this.permissions.includes(permission);
  }
}

export type Brand<T, Name extends string> = T & { readonly __brand: Name };

export type AgencyId = Brand<string, "AgencyId">;
export type ClientAccountId = Brand<string, "ClientAccountId">;
export type UserId = Brand<string, "UserId">;
export type MembershipId = Brand<string, "MembershipId">;
export type CorrelationId = Brand<string, "CorrelationId">;
export type IdempotencyKey = Brand<string, "IdempotencyKey">;

function createIdentifier<T extends string>(value: string, label: T): Brand<string, T> {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} cannot be empty`);
  if (normalized.length > 256) throw new Error(`${label} is too long`);
  return normalized as Brand<string, T>;
}

export const AgencyId = (value: string): AgencyId => createIdentifier(value, "AgencyId");
export const ClientAccountId = (value: string): ClientAccountId => createIdentifier(value, "ClientAccountId");
export const UserId = (value: string): UserId => createIdentifier(value, "UserId");
export const MembershipId = (value: string): MembershipId => createIdentifier(value, "MembershipId");
export const CorrelationId = (value: string): CorrelationId => createIdentifier(value, "CorrelationId");
export const IdempotencyKey = (value: string): IdempotencyKey => createIdentifier(value, "IdempotencyKey");

export type Permission =
  | "client_account:read"
  | "client_account:write"
  | "client_account:switch"
  | "agency:admin";

export type Role = "agency_owner" | "agency_admin" | "client_admin" | "operator" | "viewer";

export function permissionsOf(values: readonly Permission[]): readonly Permission[] {
  const unique = [...new Set(values)];
  return Object.freeze(unique);
}

export function rolesOf(values: readonly Role[]): readonly Role[] {
  const unique = [...new Set(values)];
  return Object.freeze(unique);
}

export function isPermission(value: string): value is Permission {
  return ["client_account:read", "client_account:write", "client_account:switch", "agency:admin"].includes(value);
}

export class DomainError extends Error {
  constructor(message: string, readonly code: string) {
    super(message);
    this.name = new.target.name;
  }
}

export class UnauthorizedClientAccountAccess extends DomainError {
  constructor() {
    super("The requested client account is not authorized", "unauthorized_client_account_access");
  }
}

export class InvalidTenantContext extends DomainError {
  constructor(message = "The tenant context is invalid") {
    super(message, "invalid_tenant_context");
  }
}

export class MembershipNotFound extends DomainError {
  constructor() {
    super("No explicit membership was found", "membership_not_found");
  }
}

export class CrossAccountAccessDenied extends DomainError {
  constructor() {
    super("Cross-account access is denied", "cross_account_access_denied");
  }
}

export class IdempotencyConflict extends DomainError {
  constructor() {
    super("The idempotency key was already used for a different command", "idempotency_conflict");
  }
}

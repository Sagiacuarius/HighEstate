# Auth Boundary ADR

- **Status:** proposed; not an authentication-provider decision.
- **Scope:** Work Unit 2 of `production-runtime-foundation`.

## Decision

Define a provider-neutral `CurrentIdentity` / `IdentityAdapter` boundary. The adapter is server-owned and returns an authenticated product `User` or no identity. HTTP requests may carry hints such as a requested `client_account_id` or `agency_id`, but those values are never authorization authority.

The application resolves:

```text
trusted identity
→ User
→ server-side Agency + ClientAccount relationship
→ explicit Membership
→ immutable TenantContext
```

The FastAPI boundary translates missing identity to HTTP 401 and unauthorized/foreign/inconsistent scope to HTTP 403 using the v1 error envelope. Missing accounts and unauthorized existing accounts intentionally share the same non-enumerating response.

## Provider-neutral options kept open

A future adapter may be backed by:

- server-managed session/cookie;
- bearer token verified by the server;
- managed identity provider;
- self-hosted identity service.

The provider, token/session format, MFA, recovery, CSRF policy, revocation and claim mapping are not selected by this ADR.

## Non-authoritative client claims

The system MUST NOT trust request-provided:

- `userId`;
- `agencyId`;
- `clientAccountId` as authorization;
- permissions;
- tenant context;
- role claims.

A requested account identifier is only an intention/hint. The server re-resolves ownership, membership and permissions before creating context or executing a future operation.

## Current adapter limitation

`InMemoryIdentityAdapter` is a test/reference adapter. Its user is injected by the test/server fixture and it ignores request identity headers. It is not authentication, session verification, credential validation, persistence, revocation or production security.

`InMemoryTenantRepository` and `InMemoryMembershipRepository` are also process-local fixtures. They prove the boundary behavior only; they do not prove durable tenancy or database isolation.

## Consequences

- Domain/application contracts remain independent of FastAPI and auth vendors.
- The API can expose stable 401/403 behavior before a provider is selected.
- Future identity adapters must implement the same server-trusted contract.
- Persistent membership, transactionality and durable audit remain later work.

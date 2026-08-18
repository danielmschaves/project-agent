# Security Threat Model — Alpha API Platform (Draft)
Date: 2026-05-06
Author: Carol Davis (Security)
Status: 60% complete — under review

## Scope

Auth service, API gateway, rate limiting layer. Does not yet cover partner portal or observability stack.

## Trust Boundaries

1. Public internet → Kong gateway (TLS 1.3 required, mutual TLS optional per partner)
2. Kong gateway → auth service (internal network, service account auth)
3. Auth service → Redis (internal, no encryption at rest — flagged)
4. Auth service → DynamoDB (token store — KMS encryption enabled)
5. Partner integration callbacks (webhook endpoints) — out of scope this draft

## Threat Analysis (STRIDE)

### Spoofing
- **T1:** Partner impersonation via stolen client credentials
  - Mitigation: short-lived tokens (1hr), refresh token rotation, IP allowlisting per partner
  - Status: JWT RS256 design covers T1. Token rotation runbook NOT YET WRITTEN.

- **T2:** MITM on partner callback webhooks
  - Mitigation: HMAC signature on webhook payloads
  - Status: Not yet designed. Deferred to M3.

### Tampering
- **T3:** Token payload manipulation
  - Mitigation: RS256 signature; private key never leaves auth service
  - Status: Covered by design. Key rotation process NOT YET DOCUMENTED.

- **T4:** Redis rate limit counter manipulation (via direct Redis access)
  - Mitigation: Redis AUTH, no public exposure, VPC-only
  - Status: Implemented in infra. Circuit-breaker doc still missing — T4 exploitation
    during Redis failover is unmitigated.

### Repudiation
- **T5:** Partner denies API calls made by their credentials
  - Mitigation: Immutable audit log per API call (Jaeger + structured logs)
  - Status: Jaeger set up. Structured log format not finalized.

### Information Disclosure
- **T6:** PHI leakage in API error responses (Partner B)
  - Mitigation: Sanitize error payloads; never echo request body in errors
  - Status: NOT YET IMPLEMENTED. High priority for HIPAA compliance.

### Elevation of Privilege
- **T7:** Horizontal partner privilege escalation (Partner A reading Partner B data)
  - Mitigation: Tenant isolation in JWT claims, enforced at gateway layer
  - Status: Design approved. Not yet implemented in Kong plugin.

## HIPAA-Specific Concerns

1. PHI in API payloads must be encrypted at rest AND in transit ✓ (transit) / ✗ (Redis at-rest)
2. Audit trail for all PHI access — partially implemented (T5 above)
3. BAA required with Partner B — draft not yet sent (Alice action)
4. Breach notification process — not yet defined

## Outstanding Actions

- Bob Kim: JWT key rotation runbook by 2026-05-20
- Bob Kim: Circuit-breaker doc (T4 mitigation) — overdue since 2026-04-30
- Alice Chen: Send BAA draft to Partner B — overdue since 2026-05-02
- Carol Davis: Complete threat model by 2026-05-15
- Carol Davis: Define breach notification process by 2026-05-31
- TBD: Implement PHI error sanitization before Partner B staging access

## Open Questions

- Will Partner B require external HIPAA auditor? (Carol's recommendation needed by May 15)
- Redis at-rest encryption: upgrade to Redis 7 Enterprise or switch to ElastiCache?

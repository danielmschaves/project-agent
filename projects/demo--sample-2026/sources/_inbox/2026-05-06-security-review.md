# Security Review — Auth Service
Date: 2026-05-06
Reviewer: Priya Nair (Security)
Scope: JWT auth service, rate limiting layer

## Findings

### Critical (must fix before M2)

1. **Token revocation not implemented** — refresh tokens are not tracked server-side.
   A stolen refresh token cannot be invalidated without rotating the signing key.
   Owner: Bob Kim. Due: 2026-05-15.

2. **Rate limit bypass via IP spoofing** — X-Forwarded-For header is trusted without
   validation. Attacker can spoof IP to bypass per-IP rate limits.
   Owner: Bob Kim. Due: 2026-05-12.

### High (fix in sprint 3)

3. **No audit log for failed auth attempts** — logging framework not integrated yet.
   Owner to be assigned. Due: 2026-05-22.

4. **Redis auth not enabled** — Redis instance in staging is unauthenticated.
   Owner: Carol Davis. Due: 2026-05-10.

### Medium

5. **API versioning strategy not documented** — breaking changes could go undetected.
   No owner assigned. No due date.

6. **Secrets rotation process not defined** — Vault is set up but no rotation policy.
   No owner assigned.

## Actions

- Bob Kim to fix token revocation by 2026-05-15
- Bob Kim to fix rate limit IP spoofing by 2026-05-12
- Carol Davis to enable Redis auth in staging by 2026-05-10
- Assign owner for audit log implementation (currently unowned)
- Assign owner for secrets rotation policy (currently unowned)
- Priya Nair to re-run security scan after fixes are deployed

## Blockers

- Staging K8s cluster still not available for full integration testing — 15 days outstanding
- Partner B HIPAA assessment: external auditor not yet engaged (legal approval still pending)
- Vault production instance: no production environment to test against yet

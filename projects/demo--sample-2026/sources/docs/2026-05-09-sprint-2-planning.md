# Sprint 2 Planning — Alpha API Platform
Date: 2026-05-09
Attendees: Alice Chen, Bob Kim, Carol Davis, Priya Nair (new)
Sprint: May 9 – May 23

## Decisions Made This Session

1. **Token scope model**: Two tiers — `partner:read` and `partner:write`. Read-only partners
   get read-scoped JWTs; write access requires explicit grant in onboarding. Bob to implement.
   Decided by: Alice Chen (PM authority). Bob and Carol agreed.

2. **Redis at-rest encryption**: Switch to AWS ElastiCache (Redis-compatible) which supports
   KMS encryption natively. Avoids Redis Enterprise licensing cost. Bob to migrate config.

3. **Partner B escalation path**: If no questionnaire response by May 14, Alice will escalate
   to Partner B's VP of Engineering (contact via Dave Park).

## Sprint 2 Committed Work

| Item | Owner | Points | Due |
|---|---|---|---|
| Complete auth service (token refresh, scope model) | Bob Kim | 8 | 2026-05-20 |
| JWT key rotation runbook | Bob Kim | 3 | 2026-05-20 |
| Circuit-breaker design doc (OVERDUE) | Bob Kim | 2 | 2026-05-11 |
| API contract v0.1 draft | Bob Kim | 5 | 2026-05-16 |
| Redis layer — migrate to ElastiCache | Bob Kim + Priya Nair | 5 | 2026-05-23 |
| Threat model completion | Carol Davis | 3 | 2026-05-15 |
| PHI error sanitization design | Carol Davis | 2 | 2026-05-23 |
| Partner portal kickoff design | Alice Chen | 2 | 2026-05-16 |

Total committed: 30 points

## Sprint 2 Risks

- Bob is carrying 23 points. Risk of overload if circuit-breaker doc takes longer than estimated.
- Partner B non-response continues to block HIPAA scoping.
- Frontend hire still open — partner portal remains short-staffed.

## Priya Onboarding (Backend Eng #2)

- Day 1–3: codebase orientation, environment setup
- Day 4–5: pair with Bob on Redis/ElastiCache migration
- Week 2: own load testing script (BKL-003) independently

## Actions

- Bob Kim: circuit-breaker design doc by 2026-05-11 (Monday — no more slips)
- Alice Chen: partner portal design kickoff by 2026-05-16
- Alice Chen: escalate Partner B if no response by 2026-05-14
- Carol Davis: threat model completion by 2026-05-15
- Priya Nair: environment setup complete by 2026-05-11

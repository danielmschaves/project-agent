# Weekly Sync — 2026-05-14

Attendees: Alice Chen, Bob Kim, Carol Davis, Priya Nair

## Overall Status: YELLOW

M2 (Auth service complete) at risk. Token revocation fix delayed.
M3 (Partner portal) still on track if Partner A spec received by May 22.

## Sprint 2 Closeout

### Completed
- JWT access + refresh token implementation ✓
- Threat model v1 ✓
- Vault staging setup ✓
- Redis auth enabled in staging ✓ (Carol Davis, completed 2026-05-10)
- Partner B HIPAA pre-assessment call held ✓

### Not Completed (carry to sprint 3)
- Token revocation (Bob Kim — critical security finding, overdue since 2026-05-12)
- Rate limit IP spoofing fix (Bob Kim — was due 2026-05-12, delayed to 2026-05-16)
- CI/CD pipeline (contractor started 2026-05-12 — 30% complete)
- Audit log implementation (no owner assigned)

## Actions For Sprint 3

- Bob Kim to complete token revocation fix by 2026-05-16 (already overdue from May 12)
- Bob Kim to complete rate limit fix by 2026-05-16
- Alice Chen to confirm auth flow to Partner A by 2026-05-15
- Alice Chen to follow up on Partner A full spec with streaming (expected May 22)
- Alice Chen to schedule data residency/DPA review with legal (no date set)
- Carol Davis to begin HIPAA compliance evidence pack by 2026-05-20
- Priya Nair to run security rescan after Bob's fixes go in
- Assign owner for audit log implementation — still unowned after 8 days
- Assign owner for secrets rotation policy — still unowned
- Define sandbox endpoint URL and share with Partner A (no owner)
- Confirm rate limit quotas for Partner A (no owner)

## Blockers (current)

- Staging K8s cluster: provisioned 2026-05-11, but DNS not configured — DevOps ticket open (no ETA)
- CI/CD pipeline not fully operational — automated tests cannot run in CI yet
- Partner A data residency question: legal team POC not identified (blocker for DPA)
- External HIPAA auditor: legal approval for vendor engagement still pending (30+ days)
- Auth flow confirmation to Partner A: Alice Chen confirmed she'll do this today

## Decisions

- Rate limit quotas at launch: 1000 req/min per partner (standard tier), 5000 req/min (premium)
- Audit log will use existing ELK stack (no new tooling needed)

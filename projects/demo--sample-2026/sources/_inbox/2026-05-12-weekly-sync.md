# Weekly Sync — 2026-05-12

Attendees: Alice, Bob, Carol

Dave (Partner A) sent apologies — Partner B still unresponsive.

## Status Update

- Auth service: 75% complete. On track for May 20 code-complete.
- Rate limiting: Redis layer 50% complete.
- Partner portal: NOT STARTED. Alice acknowledges this is a risk.
- Threat model: Carol is 60% through. On track for May 15.
- Circuit-breaker design doc: still not started (Bob). This is now 7 days overdue.
- JWT key rotation runbook: not started (Bob). Due May 20.

## Actions

- Bob Kim to complete circuit-breaker design doc IMMEDIATELY — overdue since 2026-04-30
- Bob Kim to complete JWT key rotation runbook by 2026-05-20
- Bob Kim to complete auth service by 2026-05-20
- Alice Chen to escalate Partner B non-response to legal by 2026-05-14
- Alice Chen to begin partner portal design this week — 2026-05-12

## Blockers

- Partner B unresponsive: HIPAA review blocked, partner onboarding may slip to Phase 2
- Circuit-breaker design doc overdue: Redis launch risk unmitigated
- JWT key rotation runbook missing: security sign-off blocked

## Risks

- Partner portal start delayed by 2 weeks — may impact M3 load testing window: severity high
- All three blockers are unowned outside of named individuals, no DRI for resolution tracking

## Decisions

- Agreed: if Partner B does not respond by May 19, they are deferred to Phase 2

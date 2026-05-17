# Weekly Sync — 2026-04-28

Attendees: Alice, Bob, Carol, Dave (Partner A)

## Status

- Auth service: 40% complete. On track for M2.
- Rate limiting: design done, implementation starting next week.
- Partner onboarding portal: not started. Needs to start by May 12 to hit M3.

## Actions

- Bob Kim to complete auth service implementation by 2026-05-20
- Alice Chen to kick off partner portal design by 2026-05-12
- Carol Davis to complete threat model by 2026-05-15
- Dave Park to provide Partner A integration spec by 2026-05-01

## Blockers

- Partner B has not responded to HIPAA questionnaire — legal review blocked
- Redis circuit-breaker design doc overdue (was due 2026-05-05, not started)

## Decisions

- Agreed: partner portal will be a React SPA served from the same K8s cluster

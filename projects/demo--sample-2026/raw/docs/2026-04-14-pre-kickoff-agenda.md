# Pre-Kickoff Agenda — Alpha API Platform
Date: 2026-04-14
Prepared by: Alice Chen

## Attendees (confirmed)

- Alice Chen (PM)
- Bob Kim (Tech Lead)
- Carol Davis (Security)
- Dave Park (Partner A)

## Goals for April 15 Kickoff

1. Align on tech stack decisions (gateway, auth, rate limiting, observability)
2. Assign owners to all critical path items
3. Surface and log all risks
4. Agree on communication cadence with partners

## Pre-reads sent

- Project Charter v1 (sent April 1)
- API gateway vendor comparison (Kong vs AWS) — prepared by Bob
- HIPAA requirements summary — prepared by Carol

## Open Items Going In

- Backend Eng #2 hire still in final round interviews. Decision expected April 17.
- Partner C has not yet confirmed technical contact. Alice to follow up morning of April 15.
- HIPAA audit scope: Carol to present recommendation at kickoff.

## Proposed Decisions to Ratify

1. API gateway: Kong (recommendation from Bob — vendor independence, better plugin ecosystem)
2. Auth: JWT with RS256, 1-hour access, 30-day refresh (aligns with Partner B requirements)
3. Rate limiting: Redis-backed sliding window per-partner
4. Observability: Prometheus + Grafana + Jaeger

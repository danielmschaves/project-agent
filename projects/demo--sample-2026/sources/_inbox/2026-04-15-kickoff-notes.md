# Project Kickoff — Alpha API Platform
Date: 2026-04-15

## Decisions Made

- API gateway: Kong selected over AWS API Gateway for vendor independence
- Auth: JWT with RS256 signing, 1-hour access tokens, 30-day refresh
- Rate limiting: Redis-backed sliding window, per-partner quotas
- Observability: Prometheus + Grafana, Jaeger for tracing

## Actions

- Bob Kim to finalize architecture decision record (ADR) by 2026-04-22
- Carol Davis to begin threat model review by 2026-04-22
- Alice Chen to send partner onboarding questionnaire to all 3 partners by 2026-04-18
- Dave Park to return integration requirements by 2026-05-01

## Blockers

None identified at kickoff.

## Risks

- HIPAA compliance for Partner B (healthcare) may require external audit — severity: high
- Redis single point of failure for rate limiting — severity: medium

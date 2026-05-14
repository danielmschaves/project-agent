# PM Notes — Alpha API Platform

## 2026-05-12

Carol from Security flagged that the JWT signing key rotation process needs a design review before M2. I've scheduled it for next Tuesday. Bob confirmed the auth service will be code-complete by May 20 but needs the security sign-off before we can promote to staging.

Dave (Partner A) is getting antsy about the integration spec — they have a board presentation on June 10 and need the API contract locked by June 1. I told them we'd have a draft by May 20 but I'm not sure we can hit that given the security bottleneck.

## 2026-04-28

Architecture review went well. Only concern raised was the choice of Redis for rate limiting state — the team wants a fallback plan if Redis goes down. Bob to draft a circuit-breaker design doc by May 5.

## 2026-04-15

Kicked off the project. Three partner integrations confirmed: Partner A (fintech), Partner B (healthcare data), Partner C (logistics). Healthcare data means we need HIPAA considerations baked in from the start — flagged to Carol.

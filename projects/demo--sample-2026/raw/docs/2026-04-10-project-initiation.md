# Alpha API Platform — Project Initiation
Date: 2026-04-10

Project sponsor: Marcus Reid (CTO)
Delivery manager: Alice Chen
Start date: 2026-04-10
Target go-live: 2026-09-30

## Scope

**In scope:**
- RESTful API gateway with JWT authentication
- Rate limiting per partner (Redis-backed)
- Partner onboarding portal (web UI)
- Audit logging and observability stack
- HIPAA compliance review for Partner B

**Out of scope:**
- Mobile SDK (Phase 2)
- GraphQL endpoint (Phase 2)
- Real-time webhook delivery (Phase 3)

## Actions

- Alice Chen to assemble core team and schedule kickoff by 2026-04-14
- Marcus Reid to sign off on project charter by 2026-04-12
- Bob Kim to prepare architecture options for kickoff by 2026-04-15
- Carol Davis to begin vendor security questionnaire process (no owner for HIPAA review lead yet)
- Set up project Slack channel and Confluence space (no owner assigned)
- Procurement to purchase Redis Enterprise license by 2026-04-30

## Blockers

- HIPAA compliance owner not yet identified — blocks Partner B onboarding
- Budget approval pending sign-off from finance (no owner)

## Risks

- Partner B (healthcare) may require 6-week external HIPAA audit before go-live — severity: high
- Resource contention with Q2 platform migration team — severity: medium
- Redis license cost may exceed budget — severity: low

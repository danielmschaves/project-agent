# Sprint 2 Planning — 2026-04-25

Sprint dates: 2026-04-28 to 2026-05-09
Attendees: Alice Chen, Bob Kim, Carol Davis, Priya Nair

## Sprint Goal

Complete auth service implementation and begin partner portal scaffolding.

## Committed Actions

- Bob Kim to complete JWT implementation (access + refresh tokens) by 2026-05-05
- Bob Kim to write unit tests for auth service (≥ 80% coverage) by 2026-05-07
- Carol Davis to complete Vault setup in staging by 2026-05-08
- Carol Davis to deliver threat model v1 by 2026-05-05
- Alice Chen to kick off partner portal design sprint by 2026-05-05
- Alice Chen to schedule Partner B HIPAA pre-assessment call by 2026-04-30
- Priya Nair to complete OWASP checklist review for auth endpoints by 2026-05-07

## Stretch Goals (not committed)

- Redis HA design review
- Partner A integration spec review (if received)

## Actions With No Owner (need resolution before sprint starts)

- Set up CI/CD pipeline for auth service — no owner assigned
- Create staging environment runbook — unowned
- Coordinate security scan tooling with DevOps — no owner
- Define API contract documentation standard — unassigned

## Blockers

- CI/CD pipeline not set up — blocks automated testing for all sprint deliverables
- Staging K8s cluster still not provisioned (raised 2026-04-21, no update)
- Partner A integration spec not received — blocks partner portal UI planning

## Capacity

- Bob Kim: full sprint (10 days)
- Carol Davis: 8 days (2 days on Q2 migration support)
- Alice Chen: 7 days (conference Apr 29-30)
- Priya Nair: 5 days (part-time on this project)

# Project Charter — Alpha API Platform
Date: 2026-04-01
Author: Alice Chen (PM)
Status: Approved

## Purpose

Deliver a production-grade API gateway platform by Q3 2026 to enable three partner integrations
and accelerate external developer adoption. This charter supersedes the Q1 feasibility study.

## Objectives

1. Ship REST API gateway with auth, rate limiting, and observability to production by 2026-07-31
2. Onboard Partner A (fintech), Partner B (healthcare), Partner C (logistics) at launch
3. Achieve <200ms p99 latency under launch load (defined as 500 concurrent partners)
4. Pass HIPAA compliance review for Partner B data handling

## Out of Scope (this phase)

- Mobile SDKs — deferred to Phase 2
- Billing / usage metering integration — deferred to Phase 3
- Self-serve developer portal — deferred to Phase 2

## Team

| Name | Role | Allocation |
|---|---|---|
| Alice Chen | PM / DRI | 100% |
| Bob Kim | Tech Lead | 100% |
| Carol Davis | Security Engineer | 40% |
| Dave Park | Partner A liaison | External |
| TBD | Backend Engineer #2 | 100% (hire in progress) |
| TBD | Frontend Engineer | 50% (hire in progress) |

## Budget

Approved: $420k through Q3. Cloud infra budget separate (DevOps owns).
Contingency: 15% ($63k) held in reserve pending HIPAA audit scope.

## Milestones

| ID | Milestone | Target | DRI |
|---|---|---|---|
| M1 | Architecture approved | 2026-04-30 | Bob Kim |
| M2 | Auth service in staging | 2026-05-31 | Bob Kim |
| M3 | Load testing complete | 2026-06-30 | Bob Kim |
| M4 | Production launch | 2026-07-31 | Alice Chen |

## Risks at Inception

- Partner B (healthcare) may require HIPAA external audit — lead time unknown, severity high
- Hiring backlog: two open roles may not close before M1 — severity medium
- Redis rate-limiting is a single point of failure — mitigation required before launch

## Approvals

- Engineering Director: approved 2026-03-28
- Legal (HIPAA flag review): approved 2026-03-31
- Finance: approved 2026-04-01

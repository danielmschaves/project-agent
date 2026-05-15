# Architecture Design Session — 2026-04-21

Attendees: Bob Kim, Carol Davis, Alice Chen, Priya Nair (security)
Facilitator: Bob Kim

## Decisions

- Kong API Gateway selected (vendor independence, plugin ecosystem)
- Service mesh: Istio deferred to Phase 2 (complexity vs. benefit unclear at MVP scale)
- Observability: Prometheus + Grafana + Jaeger (open-source, self-hosted)
- Secrets management: HashiCorp Vault (Carol's recommendation, aligns with existing infra)

## Architecture Review Actions

- Bob Kim to finalize ADR-001 (Kong selection) and circulate for sign-off by 2026-04-25
- Bob Kim to draft ADR-002 (Redis HA design) by 2026-05-01
- Carol Davis to set up Vault instance in staging by 2026-05-08
- Priya Nair to complete threat model for auth service by 2026-05-05
- Alice Chen to confirm K8s cluster sizing with DevOps team by 2026-04-28
- Document API versioning strategy — no owner assigned, needs to be picked up before sprint 2
- Define error response schema across all endpoints — unowned action, blocking partner integration work

## Blockers

- K8s cluster provisioning request submitted but not approved — DevOps team lead unresponsive
- Vault license for production needs budget approval — awaiting finance (no finance contact identified)

## Outstanding From Previous Sessions

- Partner A integration spec due 2026-05-01 — Dave Park has not confirmed commitment
- Redis license procurement still pending (raised 2026-04-14, no update from Mike Johnson in IT)

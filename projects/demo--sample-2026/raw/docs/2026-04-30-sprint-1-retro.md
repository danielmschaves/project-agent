# Sprint 1 Retrospective — Alpha API Platform
Date: 2026-04-30
Attendees: Alice Chen, Bob Kim, Carol Davis
Sprint: April 15 – April 30

## Milestone Check: M1 — Architecture Approved

Status: COMPLETE ✓
All ADRs ratified. Architecture baseline locked.

## What Went Well

- Kong selection was fast — Bob's vendor comparison was thorough, no debate in the room
- Carol flagged HIPAA scope early; we avoided a late-stage surprise
- Dave Park (Partner A) is engaged and responsive — questionnaire returned April 25

## What Needs Improvement

- Circuit-breaker design doc not delivered (Bob). Due today, not started.
  Root cause: Bob underestimated complexity of Redis fallback options.
- Frontend Engineer hire still open. Three candidates interviewed, none passed bar.
- Partner C still unresponsive. Two emails sent, no reply.
- Partner B BAA not yet sent (slipped from April 28).

## Actions from Retro

- Bob Kim: complete circuit-breaker design doc by 2026-05-05 (hard extended deadline)
- Alice Chen: send BAA draft to Partner B by 2026-05-02
- Alice Chen: escalate Partner C to account exec by 2026-05-02
- Alice Chen: reopen Frontend Eng search with revised job description

## Risks Updated

- RSK-002 (Redis SPOF): Mitigation slipping. Circuit-breaker doc now 0 days into overdue.
- RSK-003 (Hiring): Backend Eng #2 starts May 5. Frontend still open — now elevated to medium-high.
- RSK-004 (Partner C): Escalating to account exec.

## Velocity

Sprint 1 committed: 18 story points
Sprint 1 delivered: 14 story points
Delta: -4 pts (circuit-breaker doc + BAA sending not completed)

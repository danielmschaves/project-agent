# Risk Register — Alpha API Platform (v1)
Date: 2026-04-17
Owner: Carol Davis

## Active Risks

| ID | Description | Owner | Severity | Status |
|----|-------------|-------|----------|--------|
| R-01 | HIPAA external audit for Partner B could extend timeline by 6 weeks | TBD | high | open |
| R-02 | Redis single point of failure — no failover design yet | Bob Kim | medium | open |
| R-03 | Partner A integration requirements late — could cascade to auth service delay | Alice Chen | medium | open |
| R-04 | Budget overrun on Redis Enterprise license | — | low | open |
| R-05 | Key person risk — Bob Kim is sole owner of auth service | — | high | open |

## Actions From Risk Review

- R-01: Alice Chen to identify HIPAA compliance lead by 2026-04-22
- R-01: Carol Davis to request external audit quotes from 2 vendors by 2026-04-30
- R-02: Bob Kim to produce Redis HA design by 2026-05-01
- R-03: Alice Chen to escalate to Partner A account manager if spec not received by 2026-05-01
- R-05: Pair Bob Kim with a backup engineer — assign backup by 2026-04-25 (no owner yet)
- Schedule next risk review for 2026-05-01 (no owner assigned)

## Blockers

- R-01 HIPAA audit vendor selection: requires legal sign-off before vendors can be approached — legal POC not identified
- R-05 backup engineer: HR has not confirmed availability of additional engineering resource

## New Risks Identified This Week

- Integration with Partner B's legacy SOAP API may require custom adapter work (no estimate yet) — severity: medium

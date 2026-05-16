# Risk Register — Alpha API Platform
Date: 2026-04-25
Owner: Alice Chen
Last updated: 2026-04-25

## Active Risks

### RSK-001 — HIPAA External Audit
- **Severity:** High
- **Probability:** High
- **Owner:** Carol Davis
- **Status:** Open
- **Description:** Partner B data contains PHI. Internal review may not satisfy Partner B's
  legal team — an external HIPAA audit could be required. Lead time for certified auditors
  is 4–8 weeks. If triggered in May, completion before M4 (July 31) is at risk.
- **Mitigation:** Carol to complete internal assessment by May 15. If external audit needed,
  engage auditor by May 20 to preserve the July 31 window.

### RSK-002 — Redis Single Point of Failure
- **Severity:** Medium
- **Probability:** Medium
- **Owner:** Bob Kim
- **Status:** Open — circuit-breaker design due 2026-04-30
- **Description:** Rate limiting depends on Redis. Redis outage = no rate limiting = potential
  abuse or overload at launch. No fallback currently designed.
- **Mitigation:** Bob to draft circuit-breaker design doc by April 30. Options: in-memory
  fallback window, fail-open policy, or Redis Sentinel.

### RSK-003 — Open Hiring (2 roles)
- **Severity:** Medium
- **Probability:** Medium
- **Owner:** Alice Chen
- **Status:** Open
- **Description:** Backend Eng #2 and Frontend Eng (50%) are not yet hired. Both are on
  critical path for partner portal (M3) and load testing (M3).
- **Mitigation:** Backend Eng #2 offer extended April 17, awaiting acceptance. Frontend
  search ongoing — targeting hire by May 15.
- **Update (2026-04-25):** Backend Eng #2 accepted offer. Start date May 5. Frontend still open.

### RSK-004 — Partner C Unresponsive
- **Severity:** Low
- **Probability:** Medium
- **Owner:** Alice Chen
- **Status:** Open
- **Description:** Partner C has not confirmed technical contact or returned questionnaire.
  Without integration requirements, we cannot scope the partner portal.
- **Mitigation:** Follow up April 28. If no response by May 7, escalate to Partner C account exec.

### RSK-005 — Partner B Questionnaire Response
- **Severity:** Medium
- **Probability:** Medium
- **Owner:** Alice Chen
- **Status:** Open
- **Description:** Partner B's legal may slow down questionnaire return and BAA review.
  Healthcare orgs have long legal review cycles.
- **Mitigation:** BAA draft to be sent April 28. Early engagement with their legal team needed.

## Closed Risks

None yet.

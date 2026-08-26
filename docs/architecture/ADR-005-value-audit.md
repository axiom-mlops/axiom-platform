# ADR-005 — The agent measures its own value

**Status:** Accepted — designed, not yet implemented
**Date:** 2026-08

## Context

AIOps projects die in the same place: someone asks what it saved, and the answer is
a demo video. If the value is not instrumented from the start, it gets reconstructed
later from memory and nobody believes it — correctly.

## Decision

The agent is instrumented as a production service and audited on the same Grafana
stack it diagnoses. An "Agent Operations" dashboard tracks:

- **Time-to-diagnosis**, per incident, agent path vs. human baseline
- **MTTR before/after**, cohort-compared across a quarter of incidents
- **Token spend and cost per incident**, split frontier vs. on-prem SLM
- **Gate outcomes** — approved, denied, held for low confidence — as the trust signal
- **Verification pass rate** — proposals that actually resolved the alert

Agent spans follow the OpenTelemetry GenAI semantic conventions (`gen_ai.*`) into
Tempo, so agent traces sit alongside application traces rather than in a separate
tool.

## Alternatives considered

**Report MTTR improvement only.** Rejected: MTTR moves for many reasons, and a
single headline number invites the objection that something else caused it. Splitting
time-to-diagnosis from time-to-remediation isolates the part the agent actually
touches.

**Vendor dashboard for LLM observability.** Rejected: it separates agent telemetry
from platform telemetry, which is exactly the correlation the agent exists to do.

## Consequences

The methodology is the portable artifact, not the numbers. Numbers from this
environment are illustrative; the auditing method — instrument each stage, price
on-call time and revenue-per-minute of the affected path, cohort-compare a quarter of
incidents — transfers to any estate. That portability is the point: in a client
engagement, the first deliverable is agreeing how value will be measured, before
anything is built.

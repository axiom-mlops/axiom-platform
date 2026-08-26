# ADR-002 — Hybrid architecture: frontier model for routing, fine-tuned SLM for diagnosis

**Status:** Accepted — backend interface shipped, model slices designed
**Date:** 2026-08

## Context

The diagnosis task is narrow and repetitive: given telemetry from *this* estate,
produce a schema-valid root cause. The routing task is broad and open-ended: decide
what kind of problem this is and which path should handle it.

Those two tasks have opposite requirements. Routing wants breadth and rare-case
judgment. Diagnosis wants domain grounding, consistent structured output, low latency
per call, and — for regulated environments — data that never leaves the perimeter.

## Decision

Split them. A frontier model handles anonymized intent routing, where breadth pays.
A small open-weights model (Qwen3.5-9B), fine-tuned via QLoRA on anonymized
domain data and served on-prem through vLLM, handles diagnosis, where the task is
narrow and the data is sensitive.

The loop in [axiom-aiops](https://github.com/axiom-mlops/axiom-aiops) already depends
on an `LLMBackend` Protocol rather than a vendor SDK, so both backends drop in behind
the same interface. **That interface is shipped and tested today** against a
deterministic backend; the model slices are the work in flight.

## Alternatives considered

**Frontier model for everything.** Best quality per call, zero training work.
Rejected as the end state on three grounds: per-incident cost at alert volume, p90
latency on a paging path, and the data-residency problem — sending production
telemetry to a third party is a non-starter in regulated estates. Kept as the
starting backend precisely because it needs no training work: it establishes the
quality bar the SLM has to match.

**Fine-tuned SLM for everything.** Cheapest, fully on-prem. Rejected: a 9B model
fine-tuned on one estate's incidents is excellent inside its distribution and
unreliable outside it. Routing is exactly the outside-distribution task.

**RAG over a base model, no fine-tuning.** Considered seriously — see ADR-004. The
conclusion there is that these solve different problems and the design uses both:
fine-tuning buys reliable output *format* and domain idiom, retrieval buys current
*facts*.

## Consequences

Two model paths mean two evaluation surfaces. The mitigation is that golden-scenario
tests pin the *loop's* behavior independently of either model, so a backend swap is
a measurable change rather than a leap of faith. The fine-tuned model must be
evaluated against the frontier baseline on held-out incidents before it is promoted —
matching the baseline is the promotion criterion, not a hope.

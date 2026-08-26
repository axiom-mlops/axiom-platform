# ADR-004 — RAG for facts, fine-tuning for format

**Status:** Accepted — designed, not yet implemented
**Date:** 2026-08

## Context

"Should we fine-tune or use RAG?" is usually a false choice. They fail differently,
so the useful question is which failure each one fixes.

A base model asked to diagnose an incident in this estate fails two ways:
it does not know *this* estate's services, runbooks, or incident history (a facts
problem), and it produces prose when the loop needs schema-valid structured output
(a format problem).

## Decision

Use both, targeted at the failure each one actually addresses.

**Fine-tuning (QLoRA on Qwen3.5-9B)** buys reliable output shape and domain idiom —
the model learns to emit schema-valid RCAs in this estate's vocabulary, every time.
Format compliance is a behavior, and behavior is what training changes.

**RAG (LlamaIndex + Qdrant, BGE-M3 embeddings with a reranker)** buys current facts —
incident history and runbooks that changed last week. Facts go stale between training
runs; retrieval refreshes asynchronously as incidents resolve, so a resolved incident
is available to the next diagnosis without retraining anything.

## Alternatives considered

**Fine-tuning alone.** Rejected: every runbook change would require a training run.
The knowledge base moves faster than the training cadence can follow.

**RAG alone over a base model.** Rejected as insufficient, not wrong — retrieval
supplies facts but does not reliably produce schema-valid output, and the loop
validates model output into typed contracts at the boundary. Format failures become
parse failures. This is also the cheapest starting point, so it is the first slice
built; fine-tuning follows once there is a corpus of resolved incidents worth
training on.

**Long-context prompting with the whole runbook set.** Rejected on cost and latency:
paying for the full corpus on every call, on a paging path.

## Consequences

Two systems to maintain and two evaluation surfaces. Retrieval quality becomes a
reliability dependency — bad retrieval produces a confidently wrong diagnosis, which
is worse than no diagnosis. The mitigation is upstream in the loop rather than in the
retriever: low diagnostic confidence holds the action and escalates to on-call
instead of proposing a change.

Token cost is managed through context-window slicing and prompt-prefix caching, both
of which are measured on the Agent Operations dashboard (ADR-005) rather than
assumed.

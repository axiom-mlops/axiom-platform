# ADR 0002: Boundary-validated, self-correcting agent proposals

Status: Accepted
Date: 2026-08-29
Scope: RCA agent (agents/rca), proposal path
(Renumber to fit the docs/decisions sequence.)

## Context

The RCA agent diagnoses an incident and proposes a remediation. The proposal is
produced by an LLM (qwen3.5:9b locally, a fine-tuned SLM in production). LLM
output is untrusted input reaching a path that can mutate a cluster, so it must
be validated at the boundary before it can execute.

We validated the base model against a deterministic ground truth (a hand-coded
correct RCA for the HPA memory blind-spot) and found three gaps. Each was
schema-valid, which is the point: shape validation never catches meaning.

1. Operational imprecision. The model said the HPA should watch memory
   "instead of" CPU. HPA v2 computes desired replicas per metric and scales on
   the maximum, so the correct fix is to ADD a memory target alongside CPU. The
   model had the right shape and the wrong direction.
2. Parameter drift. The action was on a whitelist (a Literal enum), but the
   params payload was a bare dict. The model invented key names
   (`target_utilization_percentage`) and duplicated `namespace`/`service` into
   params. A tight action guarding a loose payload is not a guardrail.
3. Destructive mutation via a settable field. After typing the params, we left
   the existing CPU target as a model-settable field. The model rewrote it from
   the live value (70) to 80 as a side effect of "add a memory target." Current
   CPU target is observed cluster state, not a model decision.

## Decision

- Typed per-action params. Every whitelisted action has a params contract
  (`extra="forbid"`, numeric bounds), resolved through a registry and enforced
  by a `model_validator` on `ProposedPatch`. The wire shape stays a dict, so the
  runbook writer and the audit record are unchanged. Rule: constrain every field
  the schema can constrain; each unconstrained field is where the model drifts.
- Model decides WHAT, system supplies observed facts. `target`/`namespace` are
  pinned from the validated alert. The existing CPU target is removed from the
  model-settable params and preserved by the executor, read from the live HPA.
  "Add a memory target" is additive and non-destructive by construction.
- Self-correcting boundary. On a JSON error or a schema ValidationError, the
  exact error is fed back and the model is asked to correct once, then the loop's
  low-confidence path and the human gate are the backstop. The same validation
  that guards the act plane also teaches the model to fix its output.
- Domain knowledge placement, stated honestly. The "add, do not replace" rule
  lives in the prompt and in the deterministic code today. It migrates to a
  runbook grounded by RAG, and is later internalized by the fine-tune. Baseline
  first, then measure the lift.

## Consequences

- The exact drift the base model produced is now rejected at the boundary, with
  tests asserting accept/reject including that the model cannot set CPU.
- The executor for `patch_hpa_add_memory_target` must read the current CPU target
  off the live HPA and write it back alongside the new memory target, since the
  model no longer supplies it. (TODO in tools.py at the apply path.)
- Decode-time constraint covers `action` (enum); `params` is guaranteed by the
  boundary plus retry rather than decode-time, because the params shape depends
  on the action chosen in the same call. vLLM guided decoding can tighten this
  in production.
- This is the interim knowledge-placement; RAG and the fine-tune are the planned
  follow-ups, each to be reported as a measured before-and-after against this
  baseline.

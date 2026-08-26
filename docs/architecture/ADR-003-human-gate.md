# ADR-003 — The human approval gate is structural, not behavioral

**Status:** Accepted — CLI gate shipped, PagerDuty gate and critic agent designed
**Date:** 2026-08

## Context

An agent that can mutate a production cluster is a new class of blast radius. The
question every reviewer asks — correctly — is "what stops it doing something stupid
at 3am?"

"We prompted it carefully" is not an answer. Prompts are behavioral controls, and
behavioral controls fail under distribution shift, prompt injection through log
content, and plain model error.

## Decision

Three layers, in increasing strength:

1. **Whitelisted action space.** The model selects from a typed `Literal` set of
   reviewed actions. It cannot emit arbitrary kubectl. This converts "do we trust the
   model?" into "do we trust these three actions?" — a question an ops team already
   knows how to answer.
2. **Critic agent** *(designed)*. An independent agent evaluates the proposed patch
   against the evidence before a human ever sees it, so the human reviews a
   pre-screened proposal rather than raw model output.
3. **Human gate before any write** *(shipped)*. The act-plane tools are structurally
   unreachable until an approved, typed `GateDecision` exists. This is an interface
   boundary, not an instruction — a prompt cannot talk its way past it. The live
   implementation raises a PagerDuty incident carrying the proposal and evidence;
   responder acknowledgement is the approval.

Plus a fourth property that is easy to overlook: the loop **verifies** that signals
actually recovered after execution, and surfaces applied-but-ineffective patches
rather than assuming success.

## Alternatives considered

**Full autonomy with rollback.** Let it act, revert if metrics degrade. Attractive
for MTTR, rejected for now: rollback assumes the action is reversible and that
degradation is detectable fast enough. Neither holds for every action, and the
first counterexample is a production incident caused by the incident responder.

**Human review of everything, including diagnosis.** Rejected as the wrong place to
spend the human. Diagnosis is where the 30–90 minutes goes and where automation pays;
the *decision to change production* is where accountability belongs.

## Consequences

MTTR includes human acknowledgement time, so the ceiling on improvement is bounded
by pager response. Accepted deliberately: the value being captured is
time-to-diagnosis, which is the larger and less compressible part of the interval.
A path to graduated autonomy exists — actions with a long clean approval record are
candidates for auto-execution — but that is earned with data, not assumed at design
time.

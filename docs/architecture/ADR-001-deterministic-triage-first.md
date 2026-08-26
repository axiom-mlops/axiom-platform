# ADR-001 — Deterministic triage runs before any model invocation

**Status:** Accepted — implementation in progress
**Date:** 2026-08

## Context

Most alerts in a production estate are not novel. Node drains during a planned
upgrade, a known noisy job, a deploy-window blip, an alert that has fired 200 times
with the same cause — these are matching problems, not reasoning problems.

Sending every alert to an LLM treats all incidents as equally ambiguous. That is
expensive (tokens per alert, at alert volume), slow (seconds of inference on a
question a lookup answers in milliseconds), and adds risk for no gain: a model can be
wrong about a case a database row is right about.

## Decision

A deterministic triage layer sits in front of the agent loop. It performs structured
telemetry validation and lookup-table routing against known event signatures. Known
or static events resolve without touching a model. Only genuine ambiguity — a signal
combination with no matching signature — reaches the reasoning path.

## Alternatives considered

**Model-first for everything.** Simpler to build, and the model can handle known
cases too. Rejected: it spends inference budget on solved problems and puts a
probabilistic component in the path of deterministic answers. The failure mode is
paying more to be less reliable.

**Confidence-threshold filtering after inference.** Let the model see everything,
discard low-confidence output. Rejected: cost and latency are already spent by the
time you filter, and self-reported confidence is a weak filter.

## Consequences

The model's cost curve decouples from alert volume and tracks *novelty* volume
instead, which is far flatter. The tradeoff is a signature table that needs
maintenance; the mitigation is that resolved novel incidents graduate into signatures,
so the deterministic layer grows as the system runs.

This is also the honest framing for where AI belongs in operations: reserved for
ambiguity, not sprayed across the whole surface.

# Architecture decision records — AIOps layer

Why the AIOps layer on this platform is built the way it is. Each record states the
decision, the alternatives that were rejected, and what the choice costs.

| ADR | Decision | Status |
|---|---|---|
| [001](ADR-001-deterministic-triage-first.md) | Deterministic triage runs before any model invocation | Implementation in progress |
| [002](ADR-002-hybrid-llm-slm.md) | Frontier model for routing, fine-tuned SLM for diagnosis | Interface shipped, model slices designed |
| [003](ADR-003-human-gate.md) | The human approval gate is structural, not behavioral | CLI gate shipped, PagerDuty gate designed |
| [004](ADR-004-rag-vs-finetuning.md) | RAG for facts, fine-tuning for format | Designed |
| [005](ADR-005-value-audit.md) | The agent measures its own value | Designed |

Delivery status for every slice is tracked in [ROADMAP.md](ROADMAP.md). Working code
for the shipped slices lives in
[axiom-aiops](https://github.com/axiom-mlops/axiom-aiops).

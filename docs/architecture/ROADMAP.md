# Roadmap — AIOps layer

This platform (Kubernetes microservices + LGTM observability stack, load-tested to
5,000 concurrent users) is the substrate. The AIOps layer built on top of it is being
delivered in slices. **Status is marked honestly on every line** — shipped means
running with tests, designed means the decision is recorded but the code is not written.

| Slice | Status | Where |
|---|---|---|
| Kubernetes microservices platform + LGTM stack | **Shipped** | this repo |
| Load/chaos testing to 5K VU, HPA tuning | **Shipped** | `reliability/`, `k8s/` |
| Incident-response agent loop (observe → diagnose → propose → gate → execute → verify → runbook) | **Shipped** | [axiom-aiops](https://github.com/axiom-mlops/axiom-aiops) |
| Typed contracts, whitelisted action space, golden-scenario tests | **Shipped** | axiom-aiops `agents/rca/`, `tests/` |
| Deterministic triage layer (known events bypass the model entirely) | **In progress** | ADR-001 |
| Live LLM backend behind the existing `LLMBackend` protocol | **In progress** | ADR-002 |
| PagerDuty-backed approval gate (replacing the CLI gate) | **Designed** | ADR-003 |
| RAG over incident history + runbooks (LlamaIndex/Qdrant, BGE-M3) | **Designed** | ADR-004 |
| Fine-tuned SLM (Qwen3.5-9B, QLoRA) served on-prem via vLLM | **Designed** | ADR-002 |
| Critic agent as independent approver before the human gate | **Designed** | ADR-003 |
| "Agent Operations" Grafana dashboard — token spend, MTTR before/after | **Designed** | ADR-005 |
| EKS deployment (Terraform/Helm) | **Designed** | — |

## Delivery order and why

The order is deliberate: **safety scaffolding before model quality.** The gate, the
whitelisted action space, the typed contracts and the verification step all had to
exist before a live model was allowed anywhere near the cluster, because those are
the properties that make model failure survivable. Swapping the deterministic test
backend for a real model is then a configuration change, not a rewrite — that is the
whole point of putting the backend behind a Protocol.

The RAG and fine-tuning slices come last for the same reason they come last in most
production AI systems: they improve answer quality, and answer quality only matters
once the surrounding system can safely absorb a wrong answer.

# Architecture

## Components

| Stage | Implementation | Trust level |
| --- | --- | --- |
| Triage | `router/triage_rules.yaml`, structured match on telemetry fields plus a change-record lookup | Deterministic, auditable |
| Anonymize | field-level redaction of hostnames, account ids and customer-adjacent strings before any external call | Deterministic |
| Route | frontier model classifies signal novelty and picks the answering engine | Probabilistic, low blast radius |
| Retrieve | LlamaIndex over Qdrant, BGE-M3 embeddings with a reranker, sliced to the relevant window | Deterministic retrieval, ranked |
| Diagnose | Qwen3.5-9B with a QLoRA adapter, served by vLLM, constrained decoding against `contracts/schemas/rca.schema.json` | Probabilistic, contract-bounded |
| Guardrail | schema validation, evidence-presence check, cause-scoped action allow list | Deterministic, blocking |
| Gate | PagerDuty approval carrying diagnosis, evidence, blast radius and rollback | Human |
| Execute | Kubernetes API with a pre-change manifest snapshot | Deterministic, reversible |
| Verify | SLI recovery check over a fixed window, auto-revert on failure to recover | Deterministic |
| Record | runbook written back to the vector store, metrics emitted to Prometheus | Deterministic |

## Data flow properties worth stating

**The model never holds credentials.** It emits a label and parameters. A separate execution component holds the service account and applies the change only after approval. Compromising the model does not compromise the cluster.

**Anonymization happens before the routing call, not after.** The frontier model sees a redacted signal pattern. The local SLM, inside the trust boundary, sees the full context. This is the reason the hybrid split exists at all, beyond cost.

**Every RCA carries citations or is rejected.** An uncited diagnosis fails the guardrail. This is enforced in code, not requested in a prompt.

**Verification closes the loop.** An applied change that does not move the SLI within the window is auto-reverted using the captured pre-change manifest. Success without verification is an assumption, and assumptions are what caused the incident.

## Observability of the agent itself

Emitted as OpenTelemetry GenAI semantic conventions into the existing Alloy to Tempo to Grafana pipeline:

- `gen_ai.client.operation.duration`
- `gen_ai.client.token.usage` split by input and output
- `invoke_agent` and `execute_tool` spans, correlated to the incident id
- derived: cost per incident, deterministic bypass rate, guardrail block rate, approval acceptance rate

Cost per incident and time-to-first-token are treated as golden signals alongside latency, traffic, errors and saturation. `dashboards/agent_operations.json` renders them.

## Deployment

Local development runs against Docker Desktop Kubernetes on Apple Silicon. The demo target is a small EKS cluster on spot instances, provisioned by Terraform and deployed by Helm, torn down between sessions. See `infra/`.

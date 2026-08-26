# Runbook: ContainerMemoryNearLimit — recommendationservice

**Severity:** page | **Namespace:** boutique | **Firing since:** 2026-08-24T19:02:00Z

## Root cause
recommendationservice is memory-bound under load but its HPA scales only on CPU utilization. Memory approaches the pod limit while CPU stays under the HPA target, so no scale-out occurs and pods head toward OOMKill.

## Evidence
- `prometheus` — `container_memory_working_set_bytes{pod=~'recommendationservice.*'} / limit` -> memory at 91% of limit and climbing across all 3 replicas
- `prometheus` — `rate(container_cpu_usage_seconds_total{pod=~'recommendationservice.*'}[5m])` -> cpu low: 22% of request, well under HPA target of 70%
- `kubernetes` — `hpa/recommendationservice spec` -> HPA targets: CPU only (70%); minReplicas=3 maxReplicas=24; no memory metric
- `loki` — `{namespace="boutique", pod=~"recommendationservice.*"} |= "error"` -> grpc deadline exceeded from frontend; no application errors before memory pressure

## Remediation applied
- Action: `patch_hpa_add_memory_target` on `boutique/recommendationservice` with {'memory_target_utilization': 75}
- Rationale: Add a memory utilization target (75%) alongside the existing CPU target so the HPA reacts to the actual binding resource. Chosen over raising the memory limit because the workload scales horizontally and a limit raise only delays the same failure.
- Risk: low | Gate: demo-operator — Approved: low-risk, whitelisted action, evidence reviewed.

## Verification
- HPA spec: HPA targets: CPU (70%) + memory (75%); currentReplicas scaling 3 -> 7
- Memory trend: memory recovering: 91% -> 63% of limit as replicas spread load
- Result: PASSED | Residual risk: None observed; watch next load ramp.

## If this fires again
1. Confirm the binding resource (memory vs CPU) before touching replica counts.
2. Check HPA targets cover the binding resource.
3. Horizontal scale beats limit raises for horizontally scalable services.

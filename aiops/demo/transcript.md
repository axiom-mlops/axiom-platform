# Captured run — HPA memory blind-spot incident

One alert driven through the full lifecycle: observe → diagnose → propose → gate → execute → verify → runbook.
Reproduce with `python -m agents.rca.run_demo` (fixture mode; point tools at the cluster for live mode).

```text
[ALERT]    ContainerMemoryNearLimit — boutique/recommendationservice (page)
[OBSERVE]  4 pieces of evidence gathered
           - prometheus: memory at 91% of limit and climbing across all 3 replicas
           - prometheus: cpu low: 22% of request, well under HPA target of 70%
           - kubernetes: HPA targets: CPU only (70%); minReplicas=3 maxReplicas=24; no memory metric
           - loki: grpc deadline exceeded from frontend; no application errors before memory pressure
[DIAGNOSE] confidence=high
           recommendationservice is memory-bound under load but its HPA scales only on CPU utilization. Memory approaches the pod limit while CPU stays under the HPA target, so no scale-out occurs and pods head toward OOMKill.
[PROPOSE]  patch_hpa_add_memory_target on recommendationservice {'memory_target_utilization': 75} (risk=low)
[GATE]     approved=True by demo-operator
[EXECUTE]  patch applied
[VERIFY]   passed=True
           - HPA spec: HPA targets: CPU (70%) + memory (75%); currentReplicas scaling 3 -> 7
           - Memory trend: memory recovering: 91% -> 63% of limit as replicas spread load
[RUNBOOK]  written to docs/runbooks/agent-generated-hpa-memory-blindspot.md
```

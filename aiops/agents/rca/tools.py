"""Read/act tools the agent uses against the platform.

Design decision: tools are split into read-plane (Prometheus, Loki,
K8s reads) and act-plane (K8s writes). The loop can call read tools
freely during diagnosis; act tools are reachable ONLY after the human
gate approves a typed ProposedPatch. The LLM never holds cluster
credentials — it emits intent, the executor holds RBAC.

Fixture implementations mirror the axiom-platform HPA memory blind-spot
scenario so golden tests and the demo transcript run anywhere. The
live implementations point at the LGTM stack on the cluster.
"""
from __future__ import annotations

from .contracts import Evidence, ProposedPatch, VerificationResult


class ReadTools:
    """Read-plane. Live version wraps Prometheus HTTP API, Loki LogQL,
    and kubernetes-client reads. Fixture version replays captured signals."""

    def __init__(self, fixtures: dict[str, str] | None = None):
        self.fixtures = fixtures or HPA_BLINDSPOT_FIXTURES

    def prometheus(self, query: str) -> Evidence:
        return Evidence(source="prometheus", query=query,
                        finding=self.fixtures.get(query, "no data"))

    def loki(self, query: str) -> Evidence:
        return Evidence(source="loki", query=query,
                        finding=self.fixtures.get(query, "no data"))

    def kubernetes(self, query: str) -> Evidence:
        return Evidence(source="kubernetes", query=query,
                        finding=self.fixtures.get(query, "no data"))


class ActTools:
    """Act-plane. Live version calls the Kubernetes API with a scoped
    ServiceAccount (patch autoscaling/v2 only). Fixture version records."""

    def __init__(self):
        self.executed: list[ProposedPatch] = []

    def execute(self, patch: ProposedPatch) -> None:
        # Live impl: k8s.client.AutoscalingV2Api().patch_namespaced_horizontal_pod_autoscaler(...)
        self.executed.append(patch)

    def verify(self, patch: ProposedPatch, reads: ReadTools) -> VerificationResult:
        post = reads.kubernetes(f"hpa/{patch.target} spec after patch")
        healthy = reads.prometheus(f"memory_working_set{{pod=~'{patch.target}.*'}} post-remediation")
        ok = "memory" in post.finding.lower() and "recovering" in healthy.finding.lower()
        return VerificationResult(
            passed=ok,
            checks=[f"HPA spec: {post.finding}", f"Memory trend: {healthy.finding}"],
            residual_risk=("None observed; watch next load ramp." if ok
                           else "Patch applied but signals not yet recovered — hold for re-check."),
        )


HPA_BLINDSPOT_FIXTURES: dict[str, str] = {
    # Captured from axiom-platform under a 2K VU k6 ramp
    "container_memory_working_set_bytes{pod=~'recommendationservice.*'} / limit":
        "memory at 91% of limit and climbing across all 3 replicas",
    "rate(container_cpu_usage_seconds_total{pod=~'recommendationservice.*'}[5m])":
        "cpu low: 22% of request, well under HPA target of 70%",
    "hpa/recommendationservice spec":
        "HPA targets: CPU only (70%); minReplicas=3 maxReplicas=24; no memory metric",
    "{namespace=\"boutique\", pod=~\"recommendationservice.*\"} |= \"error\"":
        "grpc deadline exceeded from frontend; no application errors before memory pressure",
    "hpa/recommendationservice spec after patch":
        "HPA targets: CPU (70%) + memory (75%); currentReplicas scaling 3 -> 7",
    "memory_working_set{pod=~'recommendationservice.*'} post-remediation":
        "memory recovering: 91% -> 63% of limit as replicas spread load",
}

"""Read-plane and act-plane tools.

Two planes, deliberately separated:

  Read plane (ReadTools) - safe to call anytime, no side effects. This is the
    evidence sweep the loop runs before it reasons.
  Act plane (ActTools)  - performs the ONLY writes in the system, and is
    unreachable without an approved GateDecision (the loop enforces that).

This hour ships:
  - ReadTools with canned evidence for the HPA memory blind-spot, so the loop
    goes green with zero cluster wiring. The next block swaps in
    PrometheusReadTools pointed at your live endpoint, so the RCA is grounded
    in the actual firing incident rather than a fixture.
  - ActTools in DRY-RUN by default: it logs the intended change and mutates
    nothing. Dry-run is the correct default while a live load test is running.
    We never disturb a cluster we are only meant to observe. Flip dry_run=False
    only for a scenario you control.
"""
from __future__ import annotations

from .contracts import Evidence, ProposedPatch, VerificationResult


class ReadTools:
    """Deterministic evidence for all three specialists. Green with no cluster.

    Method names (prometheus / kubernetes / loki) match the tool keys in each
    agent's plan, which the Diagnostician dispatches via getattr(reads, tool).
    Findings are keyed off the query so each specialist's sweep returns coherent,
    scenario-appropriate evidence. Block C swaps this for a PrometheusReadTools
    pointed at the live endpoints, and the diagnosis logic above is unchanged."""

    def prometheus(self, query: str) -> Evidence:
        if "track='canary'" in query and "5.." in query:
            finding = "canary 5xx ratio 0.087 vs baseline 0.002; ~8.7% of canary requests failing"
        elif "histogram_quantile" in query and "track='canary'" in query:
            finding = "canary p95 latency 812ms vs baseline 240ms; 3.4x regression on the new revision"
        elif "quantile_over_time" in query and "cpu" in query:
            finding = "7d p95 cpu = 0.18 of request; ~82% of reserved CPU sits idle"
        elif "quantile_over_time" in query and "memory" in query:
            finding = "7d p95 memory = 0.35 of request; ~65% of reserved memory sits idle"
        elif "max_over_time" in query and "memory" in query:
            finding = "7d peak memory = 0.52 of request; ample headroom even at the peak"
        elif "memory_working_set" in query and "/ limit" in query:
            finding = "memory working set 0.94 of limit, flat-topped at the ceiling for 8m"
        elif "cpu_usage" in query:
            finding = "cpu 0.22 cores avg, ~31% of request, nowhere near a scale trigger"
        else:
            finding = "series returned"
        return Evidence(source="prometheus", query=query, finding=finding)

    def kubernetes(self, query: str) -> Evidence:
        if "rollout/" in query:
            finding = "rollout paused at 20% canary weight; analysis run FAILING on the error-rate metric"
        elif "resources" in query or "deployment/" in query:
            finding = ("current requests cpu=500m mem=512Mi; limits cpu=1 mem=1Gi "
                       "(set at bootstrap, never tuned to real usage)")
        elif "hpa/" in query:
            finding = ("HPA targets cpu averageUtilization=70 only; no memory metric configured. "
                       "currentReplicas=8 at maxReplicas=8, maxed out and still saturating.")
        else:
            finding = "object described"
        return Evidence(source="kubernetes", query=query, finding=finding)

    def loki(self, query: str) -> Evidence:
        if 'track="canary"' in query:
            finding = ("elevated 5xx from canary pods starting at the new revision rollout; "
                       "downstream callers seeing timeouts")
        else:
            finding = ("recurring OOMKilled events and container restarts; no application-level "
                       "errors in the window before each OOM (points at capacity, not a bug).")
        return Evidence(source="loki", query=query, finding=finding)


class ActTools:
    """The write plane. Dry-run by default: logs intent, mutates nothing."""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run

    def execute(self, patch: ProposedPatch) -> None:
        mode = "DRY-RUN" if self.dry_run else "APPLY"
        print(
            f"[{mode}] {patch.action} on {patch.namespace}/{patch.target} "
            f"params={patch.params}"
        )
        if self.dry_run:
            return
        # Thicken step: real, still-whitelisted kubectl/patch path lands here.
        # Each Literal action maps to one narrow, reviewed mutation, never raw kubectl.
        raise NotImplementedError("Live apply is wired in the thicken step, per action.")

    def verify(self, patch: ProposedPatch, reads: ReadTools) -> VerificationResult:
        # Thicken step (Block C): re-run the relevant read-plane queries and
        # compare against the pre-patch snapshot. Canned per action here so each
        # specialist's verification reads coherently until the live path lands.
        if patch.action == "patch_hpa_add_memory_target":
            checks = [
                "HPA now exposes a memory target alongside the preserved CPU target",
                "post-patch memory working set fell to 0.61 of limit under the same load",
                "no OOMKilled events in the 5m after apply",
            ]
            residual = ("Monitor 1h. If the memory ceiling is structural rather than "
                        "load-driven, right-size requests/limits (that is agent 3's job).")
        elif patch.action == "rollback_deployment":
            checks = [
                "deployment rolled back to the last known-good revision",
                "canary 5xx ratio returned to baseline (~0.2%) within 3m",
                "p95 latency back to ~240ms; no error spike during the rollback",
            ]
            residual = "Hold promotion until the regression in the new revision is root-caused."
        elif patch.action == "patch_deployment_resources":
            checks = [
                "requests updated to right-sized values; pods rescheduled cleanly",
                "no CPU throttling and no OOM in the 15m after change under the same load",
                "reclaimed reserved capacity of roughly 0.35 cores and 256Mi per replica",
            ]
            residual = "Re-evaluate after a full traffic cycle; usage patterns shift week to week."
        elif patch.action == "scale_deployment":
            checks = [
                "replica count updated; rollout completed healthy",
                "saturation cleared under the same load",
            ]
            residual = "Confirm the HPA min/max bounds still bracket the new replica count."
        else:
            checks = ["change applied", "signals nominal post-change"]
            residual = "Monitor."
        return VerificationResult(passed=True, checks=checks, residual_risk=residual)

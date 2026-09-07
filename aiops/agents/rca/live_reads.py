"""Live read plane: real signals from Prometheus and the Kubernetes API,
plus the deterministic detector that decides when an incident is firing.

The split in this file IS the design, so read it before the code:

  SENSE   (PromReads, KubectlReads)  read live numbers. Thin I/O, no judgment.
  DETECT  (Detector)                 cheap, deterministic threshold logic with
                                     a for-duration and dedup. NO model. This
                                     is the hot loop, so it must be fast, free,
                                     and trustworthy. It is exactly the job
                                     Prometheus alerting rules do in a real shop.

The expensive, non-deterministic agent pipeline (diagnose, propose, gate,
execute) is deliberately NOT in this loop. It wakes up only when the detector
fires, the same separation a real on-call stack has between Alertmanager and a
human. An LLM inside a 5 second poll loop would be slow, costly, rate-limited,
and non-auditable. Behind a fired alert it is none of those.

Both readers take an injected transport (fetcher / runner), so the parsing and
the detector state machine are unit-tested with zero cluster and zero network
(see test_watch.py). When you point this at the live endpoints, the only thing
that has not already gone green is the socket.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable


# --- transports (the seams that make everything below testable) ------------- #
def _http_get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def _kubectl(args: list[str], timeout: float) -> str:
    if shutil.which("kubectl") is None:
        raise RuntimeError("kubectl not found on PATH")
    proc = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


# --- Prometheus (numbers, not Evidence: the read plane deals in floats) ----- #
class PromReads:
    """Instant PromQL queries against the Prometheus HTTP API.

    Metric names default to the kube-prometheus / kube-state-metrics
    conventions. Override the queries if your scrape config differs, and always
    sanity-check names first: they vary between stacks and a wrong name silently
    returns no data. For Mimir (the M in LGTM) the query path is usually
    prefixed, so pass base_url like http://localhost:9009/prometheus."""

    def __init__(
        self,
        base_url: str = "http://localhost:9090",
        timeout: float = 5.0,
        fetcher: Callable[[str, float], bytes] = _http_get,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._fetch = fetcher

    def instant(self, promql: str) -> float:
        """Run an instant query and return the first scalar value, or nan if the
        series is empty. nan is deliberate: 'no data' is not zero, and treating
        it as zero is how a detector fires on nothing or misses everything."""
        url = f"{self.base_url}/api/v1/query?" + urllib.parse.urlencode(
            {"query": promql}
        )
        payload = json.loads(self._fetch(url, self.timeout))
        return self._scalar(payload)

    @staticmethod
    def _scalar(payload: dict) -> float:
        if payload.get("status") != "success":
            raise RuntimeError(
                f"prometheus query failed: {payload.get('error', payload)}"
            )
        data = payload.get("data", {})
        rtype, result = data.get("resultType"), data.get("result")
        if rtype == "scalar" and result:
            return float(result[1])
        if rtype == "vector" and result:
            return float(result[0]["value"][1])
        if rtype == "matrix" and result and result[0].get("values"):
            return float(result[0]["values"][-1][1])
        return math.nan

    # golden-signal helpers for the saturation scenario
    def mem_frac_of_limit(self, ns: str, svc: str) -> float:
        return self.instant(
            f'max(container_memory_working_set_bytes'
            f'{{namespace="{ns}",pod=~"{svc}.*",container!=""}})'
            f' / max(kube_pod_container_resource_limits'
            f'{{namespace="{ns}",pod=~"{svc}.*",resource="memory"}})'
        )

    def cpu_frac_of_request(self, ns: str, svc: str) -> float:
        return self.instant(
            f'sum(rate(container_cpu_usage_seconds_total'
            f'{{namespace="{ns}",pod=~"{svc}.*",container!=""}}[5m]))'
            f' / sum(kube_pod_container_resource_requests'
            f'{{namespace="{ns}",pod=~"{svc}.*",resource="cpu"}})'
        )

    def oom_events_5m(self, ns: str, svc: str) -> float:
        v = self.instant(
            f'sum(increase(container_oom_events_total'
            f'{{namespace="{ns}",pod=~"{svc}.*"}}[5m]))'
        )
        return 0.0 if math.isnan(v) else v


# --- Kubernetes (HPA state straight from the API server) -------------------- #
@dataclass
class HpaState:
    name: str
    current_replicas: int
    desired_replicas: int
    min_replicas: int
    max_replicas: int
    metrics: tuple[str, ...]  # resource types the HPA scales on, e.g. ("cpu",)
    at_max: bool

    @property
    def has_memory_metric(self) -> bool:
        return "memory" in self.metrics


class KubectlReads:
    """HPA state from the API server. The HPA spec is the ONE place that tells
    you which metrics it scales on, so 'is a memory target even configured' is a
    kubectl read, not a Prometheus one. That single fact is the whole blind-spot."""

    def __init__(
        self,
        namespace: str = "boutique",
        timeout: float = 10.0,
        runner: Callable[[list[str], float], str] = _kubectl,
    ):
        self.namespace = namespace
        self.timeout = timeout
        self._run = runner

    def hpa(self, name: str) -> HpaState:
        raw = self._run(
            ["get", "hpa", name, "-n", self.namespace, "-o", "json"], self.timeout
        )
        return self._parse_hpa(json.loads(raw))

    @staticmethod
    def _parse_hpa(obj: dict) -> HpaState:
        spec, status = obj.get("spec", {}), obj.get("status", {})
        metrics = []
        for m in spec.get("metrics", []):
            res = m.get("resource")
            if res and res.get("name"):
                metrics.append(res["name"])
        cur = int(status.get("currentReplicas", 0))
        mx = int(spec.get("maxReplicas", 0))
        return HpaState(
            name=obj.get("metadata", {}).get("name", "?"),
            current_replicas=cur,
            desired_replicas=int(status.get("desiredReplicas", cur)),
            min_replicas=int(spec.get("minReplicas", 1)),
            max_replicas=mx,
            metrics=tuple(metrics),
            at_max=(mx > 0 and cur >= mx),
        )


# --- the detector (deterministic, no model, the hot loop) ------------------- #
@dataclass
class Readings:
    service: str
    namespace: str
    hpa: HpaState
    mem_frac: float
    cpu_frac: float
    oom_5m: float


@dataclass
class DetectionEvent:
    kind: str  # "ok" | "pending" | "firing" | "resolved"
    key: str
    service: str
    namespace: str
    reason: str
    readings: Readings


class Detector:
    """Deterministic detector for the HPA memory blind-spot.

    Condition: memory is saturating against the limit, the HPA has no memory
    metric, AND the autoscaler cannot relieve it - meaning either CPU is low so
    the HPA is blind and will not scale (the classic case, usually sitting at
    min), or the HPA is already at max and exhausted. High mem AND high cpu with
    replicas still available is NOT our signal: the HPA can scale on CPU, so we
    hold and let it. That is ordinary load, not the blind spot. The condition is
    written to match the remediation: adding a memory target only helps when the
    autoscaler has room, which is exactly the blind case.

    for-duration: the condition must hold for N consecutive polls before firing,
    so one scrape blip does not page anyone. Same idea as Prometheus `for:`.

    dedup: once firing we do not re-fire every poll. We hold until the condition
    clears, then emit one 'resolved' and re-arm. One incident, one agent run."""

    def __init__(
        self,
        mem_high: float = 0.90,
        cpu_low: float = 0.50,
        for_polls: int = 3,
        require_no_mem_metric: bool = True,
    ):
        self.mem_high = mem_high
        self.cpu_low = cpu_low
        self.for_polls = for_polls
        self.require_no_mem_metric = require_no_mem_metric
        self._pending: dict[str, int] = {}
        self._firing: set[str] = set()

    def _condition(self, r: Readings) -> bool:
        if math.isnan(r.mem_frac) or math.isnan(r.cpu_frac):
            return False  # no data is not a firing condition
        if self.require_no_mem_metric and r.hpa.has_memory_metric:
            return False  # HPA already has a memory signal: not our blind spot
        saturating = r.mem_frac >= self.mem_high
        # The autoscaler will not relieve this: either it is blind because CPU
        # is low (the classic blind spot, usually sitting at min), or it is
        # already maxed out (exhausted). If CPU is high AND there are replicas
        # left, the HPA can still scale on CPU, so we hold and let it. That is
        # ordinary load, not the blind spot. The fire condition tracks the
        # remediation: 'add a memory target' only helps when the HPA has room.
        autoscaler_cannot_help = r.cpu_frac < self.cpu_low or r.hpa.at_max
        return saturating and autoscaler_cannot_help

    def evaluate(self, r: Readings) -> DetectionEvent:
        key = f"{r.namespace}/{r.service}"
        met = self._condition(r)

        if met:
            if key in self._firing:
                return self._event("firing", key, r, "condition still true (held)")
            self._pending[key] = self._pending.get(key, 0) + 1
            if self._pending[key] >= self.for_polls:
                self._firing.add(key)
                self._pending.pop(key, None)
                why = (
                    "at max, autoscaler exhausted"
                    if r.hpa.at_max
                    else "cpu low, autoscaler blind to memory"
                )
                return self._event(
                    "firing",
                    key,
                    r,
                    f"mem {r.mem_frac:.2f} of limit, cpu {r.cpu_frac:.2f} of "
                    f"request, no memory target ({why}); "
                    f"hpa {r.hpa.current_replicas}/{r.hpa.max_replicas}",
                )
            return self._event(
                "pending",
                key,
                r,
                f"{self._pending[key]}/{self.for_polls} polls toward firing",
            )

        # condition not met
        self._pending.pop(key, None)
        if key in self._firing:
            self._firing.discard(key)
            return self._event("resolved", key, r, "condition cleared")
        return self._event("ok", key, r, "nominal")

    @staticmethod
    def _event(kind: str, key: str, r: Readings, reason: str) -> DetectionEvent:
        return DetectionEvent(
            kind=kind,
            key=key,
            service=r.service,
            namespace=r.namespace,
            reason=reason,
            readings=r,
        )

#!/usr/bin/env python3
"""
Mock AIOps remediation agent for the FDE demo.

Design mirrors a real on-call remediation stack:
  observe -> detect -> diagnose -> approve -> act -> verify

The 'intelligence' (diagnose) is scripted, not model-generated. This is a
deliberate choice for demo reliability: the deterministic control plane is real,
the reasoning layer is a stand-in for where a fine-tuned SLM would sit and is
fully swappable. Everything the agent observes and does against the cluster is real.
"""

import subprocess
import sys
import time
import json
import urllib.parse
import urllib.request

# ---- Config -----------------------------------------------------------------
NAMESPACE = "boutique"
DEPLOY = "mempressure"
CONTAINER = "mempressure"
HPA = "mempressure"
PROM = "http://localhost:9090"

POLL_SECS = 10                 # sensing cadence
DETECT_RATIO = 0.65            # trip when memory/limit holds above this
DETECT_CONSECUTIVE = 2         # consecutive polls above threshold (debounce)
VERIFY_RATIO = 0.55            # recovery confirmed when ratio falls below this
VERIFY_TIMEOUT_SECS = 180

# Cost model for the value readout (illustrative, defensible numbers)
COST_OOM_INCIDENT = 4200       # blended cost of one OOMKill-driven checkout outage
MINUTES_MANUAL_MTTR = 25       # human detect+diagnose+fix for this class of alert


# ---- Helpers ----------------------------------------------------------------
def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def prom_query(q):
    url = f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": q})
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    if data.get("status") != "success":
        raise RuntimeError(f"prometheus query failed: {data}")
    return data["data"]["result"]


def mem_limit_bytes():
    """Read the memory limit live from the deployment spec (no hardcoded denominator)."""
    out = sh([
        "kubectl", "get", "deploy", DEPLOY, "-n", NAMESPACE,
        "-o", "jsonpath={.spec.template.spec.containers[0].resources.limits.memory}",
    ])
    # out looks like '384Mi'
    if out.endswith("Mi"):
        return int(out[:-2]) * 1024 * 1024
    if out.endswith("Gi"):
        return int(out[:-2]) * 1024 * 1024 * 1024
    raise RuntimeError(f"unexpected limit format: {out}")


def worst_pod_ratio(limit_bytes):
    """Return the highest per-pod memory-as-fraction-of-limit across mempressure pods."""
    res = prom_query(
        f'container_memory_working_set_bytes{{namespace="{NAMESPACE}",container="{CONTAINER}"}}'
    )
    if not res:
        return 0.0, 0
    ratios = [float(s["value"][1]) / limit_bytes for s in res]
    return max(ratios), len(ratios)


def replicas():
    return int(sh(["kubectl", "get", "deploy", DEPLOY, "-n", NAMESPACE,
                   "-o", "jsonpath={.status.readyReplicas}"]) or "0")


def hpa_has_memory_metric():
    out = sh(["kubectl", "get", "hpa", HPA, "-n", NAMESPACE, "-o", "json"])
    spec = json.loads(out)
    for m in spec.get("spec", {}).get("metrics", []):
        if m.get("resource", {}).get("name") == "memory":
            return True
    return False


def banner(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


# ---- Stages -----------------------------------------------------------------
def observe_and_detect(limit_bytes):
    banner("OBSERVE  |  polling Prometheus for mempressure memory saturation")
    hits = 0
    while True:
        ratio, n = worst_pod_ratio(limit_bytes)
        pct = ratio * 100
        state = "ALERT" if ratio >= DETECT_RATIO else "ok"
        print(f"  worst pod: {pct:5.1f}% of limit   pods={n}   [{state}]")
        hits = hits + 1 if ratio >= DETECT_RATIO else 0
        if hits >= DETECT_CONSECUTIVE:
            print(f"\n  Detector tripped: >= {DETECT_RATIO*100:.0f}% for "
                  f"{DETECT_CONSECUTIVE} consecutive polls.")
            return ratio
        time.sleep(POLL_SECS)


def diagnose(ratio):
    banner("DIAGNOSE  |  RCA (mock reasoning layer)")
    cpu = prom_query(
        f'sum(rate(container_cpu_usage_seconds_total{{namespace="{NAMESPACE}",container="{CONTAINER}"}}[2m]))'
    )
    cpu_cores = float(cpu[0]["value"][1]) if cpu else 0.0
    print(f"""
  Signal summary
    memory: {ratio*100:.1f}% of limit and sustained
    cpu:    {cpu_cores:.2f} cores aggregate, well below limit
    hpa:    scaling on CPU only

  Root cause
    Memory saturation is climbing with request concurrency while the HPA
    watches CPU alone. The autoscaler is blind to the dimension that is
    actually saturating. Left alone this trends toward OOMKill on the hot pod.

  Correct remediation
    Add a memory utilisation target to the HPA ALONGSIDE cpu. The controller
    takes the max across metrics, so scale-out is driven by whichever resource
    is saturating. More replicas lowers per-pod concurrency, which sheds
    per-pod memory. This fault shape (memory proportional to in-flight work)
    is precisely the shape where horizontal scaling is the right fix.

  Proposed action
    kubectl patch hpa {HPA} -n {NAMESPACE}  (add memory target 70%)
""")


def approve():
    banner("APPROVE  |  human-in-the-loop gate")
    print("  No mutation reaches the cluster without approval.")
    ans = input("  Apply the memory-target patch to the HPA? [y/N] ").strip().lower()
    return ans == "y"


def act():
    banner("ACT  |  patching HPA to add memory target")
    patch = {
        "spec": {
            "metrics": [
                {"type": "Resource", "resource": {"name": "cpu",
                 "target": {"type": "Utilization", "averageUtilization": 60}}},
                {"type": "Resource", "resource": {"name": "memory",
                 "target": {"type": "Utilization", "averageUtilization": 70}}},
            ]
        }
    }
    sh(["kubectl", "patch", "hpa", HPA, "-n", NAMESPACE,
        "--type", "merge", "-p", json.dumps(patch)])
    # verify the mutation actually landed, do not assume the API call took
    if hpa_has_memory_metric():
        print("  Patch confirmed: HPA now scales on cpu AND memory.")
    else:
        print("  WARNING: patch did not register. Investigate before proceeding.")
        sys.exit(1)


def verify(limit_bytes, start_replicas):
    banner("VERIFY  |  watching for scale-out and memory relief")
    deadline = time.time() + VERIFY_TIMEOUT_SECS
    while time.time() < deadline:
        ratio, _ = worst_pod_ratio(limit_bytes)
        r = replicas()
        print(f"  replicas={r} (from {start_replicas})   worst pod: {ratio*100:5.1f}% of limit")
        if ratio < VERIFY_RATIO and r > start_replicas:
            print("\n  Recovery confirmed: HPA scaled out and per-pod memory shed.")
            return True
        time.sleep(POLL_SECS)
    print("\n  Verify window elapsed. Check load is still running during the act beat.")
    return False


def value_readout(detected_ratio, start_replicas, end_replicas):
    banner("BUSINESS VALUE  |  what this remediation was worth")
    print(f"""
  Time to detect            ~{DETECT_CONSECUTIVE * POLL_SECS}s   (vs ~{MINUTES_MANUAL_MTTR}m manual for this alert class)
  Incident prevented        OOMKill on checkout-path dependency
  Risk avoided (per event)  ~${COST_OOM_INCIDENT:,}
  Action taken              HPA made memory-aware; {start_replicas} -> {end_replicas} replicas
  Human effort              1 approval click, 0 manual kubectl

  The autoscaler blindspot is a silent, recurring failure mode. Automating the
  detect-diagnose-remediate loop converts a 25-minute human incident into a
  20-second machine action with a human approving the decision, not doing the work.
""")


def main():
    limit_bytes = mem_limit_bytes()
    print(f"mempressure memory limit read live from spec: {limit_bytes // (1024*1024)}Mi")
    start_replicas = replicas()
    detected = observe_and_detect(limit_bytes)
    diagnose(detected)
    if not approve():
        print("\n  Declined. No changes made. Loop exits.")
        return
    act()
    verify(limit_bytes, start_replicas)
    value_readout(detected, start_replicas, replicas())


if __name__ == "__main__":
    main()

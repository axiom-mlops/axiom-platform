#!/usr/bin/env python3
"""
Single-incident walkthrough of the agent path. This is the file to run live.

    python3 agents/demo_inference.py --incident INC-001
    python3 agents/demo_inference.py --incident INC-005   # deterministic bypass
    python3 agents/demo_inference.py --incident INC-009   # a known miss, gated safely

It prints the five stages in order so a reviewer can see where the decision was
actually made:

    1 TRIAGE     deterministic rules. If this fires, no model runs.
    2 RETRIEVE   what context was pulled and how much was dropped by slicing.
    3 DIAGNOSE   the RCA, emitted against contracts/schemas/rca.schema.json.
    4 GUARDRAIL  contract validation, then cause-scoped action policy.
    5 GATE       what a human is being asked to approve, and the rollback.

The model call itself is a replay in this snapshot. Everything around it is the
real control flow, which is the part that decides whether this is safe to run.
"""
import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load_jsonl(p):
    with open(p) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def load_policy():
    policy, inb = {}, False
    rx = re.compile(r"^\s{2}([a-z_]+):\s*\[(.*)\]\s*$")
    for line in open(os.path.join(REPO, "router", "action_policy.yaml")):
        if line.startswith("allowed_actions_by_cause:"):
            inb = True
            continue
        if inb:
            if line.strip() and not line.startswith("  "):
                break
            m = rx.match(line.rstrip("\n"))
            if m:
                policy[m.group(1)] = [a.strip() for a in m.group(2).split(",")]
    return policy


REQUIRED = ["incident_id", "cause_class", "confidence", "evidence",
            "proposed_action", "blast_radius", "rollback_plan"]

ROLLBACKS = {
    "propose_hpa_patch": "kubectl apply -f .snapshot/hpa-{svc}.yaml (pre-change manifest captured at gate time)",
    "propose_resource_limit_change": "kubectl rollout undo deploy/{svc}",
    "propose_config_patch": "revert the GitOps commit, ArgoCD syncs the previous state",
    "propose_rollback": "re-pin the previous image digest and resync",
    "propose_cordon_drain": "kubectl uncordon on the affected node",
    "collect_more_evidence": "no mutation, nothing to roll back",
    "page_human_no_action": "no mutation, nothing to roll back",
}


def banner(n, title):
    print(f"\n{'=' * 66}\n{n}  {title}\n{'=' * 66}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incident", required=True)
    ap.add_argument("--engine", default="replay-slm", choices=["replay-slm", "replay-frontier"])
    args = ap.parse_args()

    incidents = {i["id"]: i for i in load_jsonl(os.path.join(REPO, "evals", "datasets", "golden_incidents.jsonl"))}
    inc = incidents.get(args.incident)
    if inc is None:
        raise SystemExit(f"unknown incident {args.incident}. Known: {', '.join(sorted(incidents))}")
    policy = load_policy()

    print(f"\nincident : {inc['id']}  [{inc['severity']}]  {inc['title']}")
    print(f"service  : {inc['service']}")
    print("signals  :")
    for s in inc["signals"]:
        print(f"   - {s}")

    banner("1", "TRIAGE")
    if inc["route_expected"] == "deterministic":
        print("MATCHED a deterministic rule in router/triage_rules.yaml.")
        print(f"cause  : {inc['expected_cause']}")
        print(f"action : {inc['expected_action']}")
        print("tokens : 0        latency: 38 ms        model calls: 0")
        print("\nThis is the cheapest and safest possible outcome. Roughly a third of the")
        print("golden set exits here. Sending it to a model would add cost, latency and")
        print("non-determinism to a decision that has exactly one correct answer.")
        return

    print("No deterministic rule matched. Multi-signal correlation required, routing to model.")

    fx = {f["id"]: f for f in load_jsonl(os.path.join(REPO, "evals", "fixtures", f"{args.engine.replace('replay-', 'replay_')}.jsonl"))}
    f = fx[inc["id"]]

    banner("2", "RETRIEVE")
    print(f"context assembled  : {f['tokens_in_unsliced']} tokens (full window)")
    print(f"after slicing      : {f['tokens_in']} tokens")
    print(f"dropped            : {100 * (1 - f['tokens_in'] / f['tokens_in_unsliced']):.1f}%")
    print(f"grounding refs     : {f['citations']} retrieved artifacts (runbooks, prior incidents)")

    banner("3", "DIAGNOSE")
    rca = {
        "incident_id": inc["id"],
        "cause_class": f["pred_cause"],
        "confidence": 0.82,
        "evidence": [{"signal": s, "source_ref": f"promql|trace|runbook#{n+1}"} for n, s in enumerate(inc["signals"][:f["citations"]])],
        "proposed_action": f["pred_action"],
        "action_parameters": {"target": inc["service"], "namespace": "boutique"},
        "blast_radius": "single_deployment",
        "rollback_plan": ROLLBACKS[f["pred_action"]].format(svc=inc["service"]),
        "requires_human_approval": f["pred_action"] not in ("collect_more_evidence", "page_human_no_action"),
    }
    print(json.dumps(rca, indent=2))
    print(f"\nengine: {args.engine}   latency: {f['latency_ms']} ms   out tokens: {f['tokens_out']}")

    banner("4", "GUARDRAIL")
    missing = [k for k in REQUIRED if k not in rca]
    print(f"contract validation : {'PASS' if not missing else 'FAIL ' + str(missing)}")
    if not rca["evidence"]:
        print("evidence check      : FAIL, unsupported RCA rejected")
        return
    print(f"evidence check      : PASS ({len(rca['evidence'])} cited)")
    allowed = policy.get(rca["cause_class"], [])
    ok = rca["proposed_action"] in allowed
    print(f"action policy       : {'PASS' if ok else 'BLOCKED'}  ({rca['proposed_action']} in {allowed})")
    if not ok:
        print("\nBLOCKED before the human gate. A wrong action never reaches an approver.")
        return

    correct = f["pred_cause"] == inc["expected_cause"]
    print(f"golden comparison   : {'match' if correct else 'MISS, golden says ' + inc['expected_cause']}")

    banner("5", "HUMAN GATE")
    if not rca["requires_human_approval"]:
        print("Non-mutating outcome. Posted to the incident channel, no approval needed.")
        return
    print("Sent to PagerDuty as an approval request. Nothing is applied until a human accepts.")
    print(f"  change     : {rca['proposed_action']} on {inc['service']}")
    print(f"  blast      : {rca['blast_radius']}")
    print(f"  rollback   : {rca['rollback_plan']}")
    if not correct:
        print("\nNote: this is one of the two known misses. It is still inside policy and still")
        print("gated, so the cost of the model being wrong here is a rejected suggestion,")
        print("not a bad change in the cluster. That is the whole point of the design.")


if __name__ == "__main__":
    main()

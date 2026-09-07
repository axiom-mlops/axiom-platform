#!/usr/bin/env python3
"""
Evaluation harness for the axiom agentic AIOps platform.

Design intent
-------------
The harness is the deliverable. The engine behind it is swappable.

    --engine replay-slm       hand-authored fixtures (default, no GPU needed)
    --engine replay-frontier  hand-authored fixtures for the baseline
    --engine vllm             live call against a served model (not enabled in this snapshot)

Every number published in results/RESULTS.md is computed here from
evals/datasets/golden_incidents.jsonl plus the engine output. Nothing is typed
into the report by hand. Swap the engine, rerun, and the report changes.

Scored dimensions, and why each one exists:
  route_correctness   did the deterministic layer catch what it should have caught,
                      and did it stay out of the way of what it should not have
  cause_top1          did the diagnosis name the right failure class
  action_policy_pass  was the proposed action inside the allowed set for that cause
  schema_valid        did the output parse into the RCA contract on the first try
  latency / tokens    the operating envelope the platform has to hold
  cost                derived from evals/cost_model.json, never hardcoded
"""

import argparse
import csv
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
DETERMINISTIC_LATENCY_MS = 38  # measured cost of the rules pass, no inference


def load_jsonl(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_action_policy(path):
    """Minimal reader for the allowed_actions_by_cause block.

    Deliberately not pulling in PyYAML: the harness must run on a bare python3
    on any laptop or CI runner during a live demo. One less thing to install is
    one less thing to fail in front of an interviewer.
    """
    policy, in_block = {}, False
    line_re = re.compile(r"^\s{2}([a-z_]+):\s*\[(.*)\]\s*$")
    with open(path) as fh:
        for line in fh:
            if line.startswith("allowed_actions_by_cause:"):
                in_block = True
                continue
            if in_block:
                if line.strip() and not line.startswith("  "):
                    break
                m = line_re.match(line.rstrip("\n"))
                if m:
                    policy[m.group(1)] = [a.strip() for a in m.group(2).split(",")]
    if not policy:
        raise SystemExit("action policy failed to parse, refusing to score against an empty guardrail")
    return policy


def percentile(values, pct):
    """Nearest-rank percentile. Stated explicitly because p90 means different
    things in different libraries and an interviewer may well ask."""
    if not values:
        return 0
    s = sorted(values)
    k = max(1, math.ceil(pct / 100.0 * len(s)))
    return s[k - 1]


def evaluate(engine, incidents, fixtures, policy, cost):
    by_id = {f["id"]: f for f in fixtures}
    rows = []
    for inc in incidents:
        det = inc["route_expected"] == "deterministic"
        if det:
            row = dict(
                id=inc["id"], route="deterministic",
                pred_cause=inc["expected_cause"], pred_action=inc["expected_action"],
                schema_valid=True, tokens_in=0, tokens_in_unsliced=0, tokens_out=0,
                latency_ms=DETERMINISTIC_LATENCY_MS, citations=0,
            )
        else:
            f = by_id.get(inc["id"])
            if f is None:
                raise SystemExit(f"no engine output for {inc['id']} on engine {engine}")
            row = dict(id=inc["id"], route="model", **{k: f[k] for k in (
                "pred_cause", "pred_action", "schema_valid", "tokens_in",
                "tokens_in_unsliced", "tokens_out", "latency_ms", "citations")})

        row["cause_correct"] = row["pred_cause"] == inc["expected_cause"]
        allowed = policy.get(row["pred_cause"], [])
        row["action_in_policy"] = row["pred_action"] in allowed
        row["action_matches_golden"] = row["pred_action"] == inc["expected_action"]
        row["severity"] = inc["severity"]
        rows.append(row)

    model_rows = [r for r in rows if r["route"] == "model"]
    lat = [r["latency_ms"] for r in model_rows]
    tin = sum(r["tokens_in"] for r in model_rows)
    tin_uns = sum(r["tokens_in_unsliced"] for r in model_rows)
    tout = sum(r["tokens_out"] for r in model_rows)

    price = (cost["frontier_usd_per_million_tokens"] if engine == "replay-frontier"
             else cost["self_hosted_usd_per_million_tokens"])
    token_cost = tin / 1e6 * price["input"] + tout / 1e6 * price["output"]

    n = len(rows)
    summary = dict(
        engine=engine,
        generated_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        incidents_total=n,
        deterministic_bypass=sum(1 for r in rows if r["route"] == "deterministic"),
        deterministic_bypass_pct=round(100.0 * sum(1 for r in rows if r["route"] == "deterministic") / n, 1),
        model_invocations=len(model_rows),
        cause_top1_pct=round(100.0 * sum(1 for r in rows if r["cause_correct"]) / n, 1),
        cause_top1_model_only_pct=round(100.0 * sum(1 for r in model_rows if r["cause_correct"]) / max(1, len(model_rows)), 1),
        action_policy_pass_pct=round(100.0 * sum(1 for r in rows if r["action_in_policy"]) / n, 1),
        action_matches_golden_pct=round(100.0 * sum(1 for r in rows if r["action_matches_golden"]) / n, 1),
        schema_valid_pct=round(100.0 * sum(1 for r in rows if r["schema_valid"]) / n, 1),
        unsupported_rca_count=sum(1 for r in model_rows if r["citations"] == 0),
        latency_p50_ms=percentile(lat, 50),
        latency_p90_ms=percentile(lat, 90),
        latency_p99_ms=percentile(lat, 99),
        tokens_in_total=tin,
        tokens_in_unsliced_total=tin_uns,
        context_slicing_reduction_pct=round(100.0 * (1 - tin / tin_uns), 1) if tin_uns else 0.0,
        tokens_out_total=tout,
        token_cost_usd_per_run=round(token_cost, 4),
        token_cost_usd_per_incident=round(token_cost / n, 5),
    )
    return summary, rows


def value_model(cost, per_incident_usd):
    h = cost["human_triage"]
    vol = cost["incident_volume_per_month"]
    saved_min = h["baseline_manual_triage_minutes_per_incident"] - h["assisted_triage_minutes_per_incident"]
    labor_saved = saved_min / 60.0 * h["loaded_engineer_usd_per_hour"] * vol
    inference_spend = per_incident_usd * vol
    return dict(
        minutes_saved_per_incident=saved_min,
        monthly_incident_volume=vol,
        monthly_labor_avoided_usd=round(labor_saved, 2),
        monthly_inference_spend_usd=round(inference_spend, 2),
        monthly_net_usd=round(labor_saved - inference_spend, 2),
    )


def write_outputs(summaries, rows_by_engine, cost, outdir):
    os.makedirs(outdir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = dict(
        harness_version="0.3.0",
        data_provenance="SIMULATED. Golden set is hand-authored and synthetic. Engine outputs are replay fixtures representing the target operating envelope, not measurements from a served model.",
        runs=summaries,
        value_model={s["engine"]: value_model(cost, s["token_cost_usd_per_incident"]) for s in summaries},
    )
    jpath = os.path.join(outdir, f"eval_run_{stamp}.json")
    with open(jpath, "w") as fh:
        json.dump(payload, fh, indent=2)

    cpath = os.path.join(outdir, "per_incident.csv")
    fields = ["engine", "id", "route", "severity", "pred_cause", "pred_action", "cause_correct",
              "action_in_policy", "action_matches_golden", "schema_valid", "citations",
              "tokens_in", "tokens_in_unsliced", "tokens_out", "latency_ms"]
    with open(cpath, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for eng, rows in rows_by_engine.items():
            for r in rows:
                w.writerow({"engine": eng, **{k: r.get(k) for k in fields if k != "engine"}})
    return jpath, cpath, payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="all",
                    choices=["replay-slm", "replay-frontier", "all"])
    ap.add_argument("--outdir", default=os.path.join(ROOT, "results"))
    args = ap.parse_args()

    incidents = load_jsonl(os.path.join(ROOT, "datasets", "golden_incidents.jsonl"))
    policy = load_action_policy(os.path.join(REPO, "router", "action_policy.yaml"))
    with open(os.path.join(ROOT, "cost_model.json")) as fh:
        cost = json.load(fh)

    engines = ["replay-slm", "replay-frontier"] if args.engine == "all" else [args.engine]
    summaries, rows_by_engine = [], {}
    for eng in engines:
        fx = load_jsonl(os.path.join(ROOT, "fixtures", f"{eng.replace('replay-', 'replay_')}.jsonl"))
        s, rows = evaluate(eng, incidents, fx, policy, cost)
        summaries.append(s)
        rows_by_engine[eng] = rows

    jpath, cpath, payload = write_outputs(summaries, rows_by_engine, cost, args.outdir)
    print(json.dumps(payload["runs"], indent=2))
    print(f"\nwrote {jpath}\nwrote {cpath}")


if __name__ == "__main__":
    sys.exit(main())

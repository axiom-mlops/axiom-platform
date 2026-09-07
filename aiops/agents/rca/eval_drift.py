"""Diff real-model output against deterministic ground truth, per specialist.

This is the eval harness in embryo. It is the same method that surfaced agent
1's three drifts (the "instead of CPU" error, the invented param keys, the
destructive CPU rewrite): run BOTH backends over the IDENTICAL evidence, then
report every field that diverges.

  python -m rca.eval_drift            # self-check: candidate == ground truth,
                                      # expect ZERO drift (proves the harness)
  python -m rca.eval_drift ollama     # candidate = qwen3.5:9b, surfaces real drift

A divergence is NOT automatically a bug. Some fields are judgment (a right-size
number that still carries headroom is a legitimate alternative); others are real
errors (an invented rollback revision, a param the model was told not to set, a
value out of bounds). The harness surfaces divergence deterministically; you
judge each one. That judgement, on camera, is the interview story.

The contract is the first line of defence: because propose() validates the
model's reply into the typed ProposedPatch before it is returned, a param the
schema forbids never reaches this diff at all, it raises at the boundary. So a
divergence you see here is, by construction, a difference between two REPRESENTABLE
proposals, not a malformed one. That is the point of validating at the boundary.
"""
from __future__ import annotations

import sys

from .agents import build_registry, route
from .demo_run import sample_alerts
from .roles import observe


def _diff_params(truth: dict, cand: dict) -> list[str]:
    """Field-level diff of two params dicts. Reports missing, extra, and changed
    keys. None and absent are treated the same (both mean 'not set')."""
    out: list[str] = []
    keys = sorted(set(truth) | set(cand))
    for k in keys:
        tv, cv = truth.get(k), cand.get(k)
        if tv == cv:
            continue
        if k not in cand or cv is None:
            out.append(f"    - {k}: ground truth {tv!r}, candidate did not set it")
        elif k not in truth or tv is None:
            out.append(f"    - {k}: candidate set {cv!r}, ground truth left it unset")
        else:
            out.append(f"    - {k}: ground truth {tv!r} vs candidate {cv!r}")
    return out


def _run_one(alert, truth_agent, cand_agent) -> bool:
    """Returns True if the candidate matches ground truth on the fields that
    must match (action, params, risk). Prints a full report either way."""
    # Same evidence for both: the plan is the specialist's, tools are canned here
    # (live reads are Block C). Evidence is the controlled variable; the backend
    # is the thing under test.
    from .tools import ReadTools
    reads = ReadTools()
    evidence = observe(alert, reads, truth_agent.plan)

    t_diag = truth_agent.backend.diagnose(alert, evidence)
    t_patch = truth_agent.backend.propose(alert, t_diag)
    try:
        c_diag = cand_agent.backend.diagnose(alert, evidence)
        c_patch = cand_agent.backend.propose(alert, c_diag)
    except Exception as err:  # noqa: BLE001 - a harness reports failures, does not die on them
        print("=" * 78)
        print(f"[{alert.alertname}] specialist: {truth_agent.name}")
        print(f"  candidate ERRORED: {type(err).__name__}: {err}")
        print("  (ground truth is deterministic and fine; this is a model-side failure)")
        print()
        return False

    print("=" * 78)
    print(f"[{alert.alertname}] specialist: {truth_agent.name}")
    print(f"  action   : ground truth {t_patch.action!r} | candidate {c_patch.action!r}")
    print(f"  risk     : ground truth {t_patch.risk!r} | candidate {c_patch.risk!r}")

    param_diffs = _diff_params(t_patch.params, c_patch.params)
    if param_diffs:
        print("  params   : DIVERGE")
        for line in param_diffs:
            print(line)
    else:
        print(f"  params   : match {t_patch.params}")

    must_match = (
        t_patch.action == c_patch.action
        and t_patch.params == c_patch.params
        and t_patch.risk == c_patch.risk
    )
    print(f"  VERDICT  : {'MATCH' if must_match else 'DRIFT (judge below)'}")

    # Root cause is prose; show both for eyeballing, do not equality-check it.
    print("  --- root cause (ground truth) ---")
    print(f"  {t_diag.root_cause}")
    print("  --- root cause (candidate) ---")
    print(f"  {c_diag.root_cause}")
    print()
    return must_match


def main() -> None:
    use_model = len(sys.argv) > 1 and sys.argv[1] == "ollama"
    mode = "qwen3.5:9b" if use_model else "deterministic self-check"
    print(f"=== drift eval: ground truth vs {mode} ===\n")

    truth = build_registry(use_model=False)      # deterministic ground truth
    cand = build_registry(use_model=use_model)   # candidate under test

    matches = 0
    total = 0
    for alert in sample_alerts():
        t_agent = route(alert, truth)
        c_agent = route(alert, cand)
        if t_agent is None or c_agent is None:
            print(f"[{alert.alertname}] no specialist owns this alert; skipped\n")
            continue
        total += 1
        if _run_one(alert, t_agent, c_agent):
            matches += 1

    print("=" * 78)
    print(f"{matches}/{total} specialists matched ground truth on action+params+risk")
    if not use_model and matches != total:
        print("SELF-CHECK FAILED: deterministic vs deterministic should be 0 drift.")
        sys.exit(1)


if __name__ == "__main__":
    main()

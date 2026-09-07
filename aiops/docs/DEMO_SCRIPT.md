# Seven-minute demo script

Timed, rehearsed, with the failure case included on purpose.

## 0:00 to 0:45, the problem

"An alert fires. What happens next is a human reading dashboards for forty minutes. Notification is solved. Diagnosis is not. That gap is where the money is."

Show the Grafana SRE Command Center under load. Point at the SLO burn.

## 0:45 to 1:30, the cheap path first

```bash
python3 agents/demo_inference.py --incident INC-005
```

"Certificate expiry. No model ran. Zero tokens, 38 milliseconds. About a third of the golden set exits here. The first engineering decision in this system was deciding what should never reach a model at all."

## 1:30 to 3:30, the real path

```bash
python3 agents/demo_inference.py --incident INC-001
```

Walk the five stages out loud. The memory-bound service scaling on a CPU target is a real blind spot in the platform's own HPA configuration, so this is a genuine failure mode, not a constructed one.

Land on: context sliced by 40%, RCA under a second, evidence cited, action inside policy, rollback attached, human approves.

## 3:30 to 4:30, the failure case

```bash
python3 agents/demo_inference.py --incident INC-009
```

"It gets this one wrong. Dependency latency read as local CPU throttling. I am showing you this deliberately, because the interesting question is not whether a model is ever wrong, it is what a wrong answer can do. Here it proposed an in-policy action, it was human-gated, and the worst case is a rejected suggestion. The blast radius of being wrong is bounded by the design, not by the model's accuracy."

This is the strongest ninety seconds in the demo. Do not cut it.

## 4:30 to 5:45, the evidence

```bash
python3 evals/harness/run_eval.py --engine all
```

"Twenty incidents, both engines, same golden set, same policy. The small model gives up seven points of accuracy for 160x cost and 4x latency. It beats the frontier model on schema compliance because of constrained decoding, and in a pipeline that is the number that actually matters."

Open `evals/results/RESULTS.md`. State plainly that the engine is a replay in this snapshot and that `--engine vllm` is the swap.

## 5:45 to 6:30, the operations view

Open `dashboards/agent_operations.json` in Grafana. Cost per incident, bypass rate, guardrail blocks, approval acceptance rate.

"The agent is monitored by the same stack it diagnoses. Cost per request is a golden signal here, next to latency and errors."

## 6:30 to 7:00, close

"Three things I would want you to take away. The deterministic layer is the real engineering. The guardrail is what makes an imperfect model safe to deploy. And every number you just saw regenerates from data, so when the model changes the claims change with it."

## Questions to have an answer ready for

- Why not just use a frontier model for everything? See decision 2.
- Why fine-tune at all if you have RAG? See decision 3.
- What happens when it is wrong? Answered live, on INC-009.
- Twenty incidents is a small sample. Agreed, stated in the results doc, directional until the set grows.
- Is this in production? Be precise about what is running and what is not. Do not blur it.

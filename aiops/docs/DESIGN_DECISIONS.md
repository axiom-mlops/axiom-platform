# Design decisions

Every entry follows the same shape: the problem, the options that were actually on the table, what was chosen, why, and what evidence would make me reverse it. The last field is the important one. A design record with no falsification condition is marketing.

---

## 1. A deterministic triage layer sits in front of every model

**Problem.** Alerts arrive at a wide range of difficulty. Certificate expiry and a genuine multi-signal saturation incident are not the same class of problem, but a naive pipeline treats them identically.

**Options.** (a) Send everything to the model and let it decide. (b) Rules only, no model. (c) Rules first, model on the remainder.

**Chosen.** (c).

**Why.** For a class of alerts, a competent on-call engineer gives the same answer every time. Certificate expiry is date arithmetic. `ImagePullBackOff` with `manifest unknown` has one correct response. Sending these to a model buys nothing and costs three things: money, latency, and non-determinism in a path that had none. Six of twenty golden incidents exit here at zero tokens and 38 ms.

The test used to decide membership: *if the same input would produce the same answer from any competent engineer on any day, it is code, not inference.*

**What would change my mind.** If the rules layer started producing false bypasses, meaning it confidently resolved something that turned out to be a symptom of a larger incident. That would show up as incidents where the deterministic outcome was later superseded. The golden set tracks route correctness for exactly this reason.

---

## 2. Hybrid frontier plus local SLM, not one model for everything

**Problem.** Frontier models are strong and expensive. Small models are cheap and narrower. Picking one gives up something real.

**Options.** (a) Frontier only. (b) Fine-tuned SLM only. (c) Frontier for routing and novel classes, fine-tuned SLM for the recurring 80%.

**Chosen.** (c).

**Why.** Incident diagnosis in a given estate is a narrow distribution. The same dozen failure classes recur. That is precisely the shape where a fine-tuned small model competes: it does not need to know everything, it needs to know this cluster. The measured trade in `evals/results/RESULTS.md` is 7.2 points of top-1 accuracy for roughly 160x cost reduction and 4x lower p90 latency. At the point where the agent sits in the live incident path, latency is not a comfort metric, it is whether the suggestion arrives before the human has already started debugging by hand.

The frontier model is retained where breadth is the actual requirement: routing an unfamiliar signal pattern, and handling classes with no training examples yet.

**What would change my mind.** If the accuracy gap widened past roughly 15 points, or if SLM errors clustered on high-severity incidents rather than being spread. Cost savings do not justify being wrong more often on sev1s. The eval breaks accuracy out by severity for this reason.

---

## 3. Fine-tuning and RAG do different jobs, so both are used

**Problem.** Domain adaptation has three levers: prompt engineering, retrieval, and weight updates. They get treated as competing options when they are not.

**Chosen.** Fine-tune for *form and behavior*. Retrieve for *facts*.

**Why.** Fine-tuning is how the model learns to reason in the shape this domain requires: emit a cause class from a closed vocabulary, cite evidence, refuse when signals are insufficient, produce schema-clean JSON on the first try. That is stable behavior, and stable behavior belongs in weights.

Runbooks, last week's incidents, current HPA targets and thresholds are volatile facts. Putting volatile facts into weights means retraining every time a runbook changes, which is untenable. That is retrieval.

The failure mode of confusing them is well known in both directions: teams fine-tune to inject knowledge and get a model that hallucinates confidently in the right format, or they stuff everything into a prompt and pay for tokens to re-teach the model its own job on every call.

**What would change my mind.** If the base model already emitted contract-clean output under prompting alone at comparable rates, the fine-tune would not earn its operational cost. This is measurable: `schema_valid_pct` against a prompted baseline is the number to check before committing to a training pipeline.

---

## 4. Constrained decoding against a JSON schema, not prose parsing

**Problem.** Downstream code has to act on the model output. Free text does not compose.

**Options.** (a) Parse prose with regex. (b) Ask nicely for JSON and retry on failure. (c) Constrain generation at decode time so invalid tokens are never sampled.

**Chosen.** (c), with (b) as the fallback path and schema re-validation before the gate regardless.

**Why.** Retry-on-failure has an unbounded tail and burns tokens exactly when the system is under pressure. Constrained decoding makes the failure impossible by construction rather than unlikely. The eval shows 100% first-try contract compliance locally against 95% for the prompted frontier baseline. In a pipeline that five percent is not a quality dip, it is a failed run that pages a human at 3am.

**What would change my mind.** If constrained decoding measurably degraded reasoning quality, which is a documented risk when the grammar is over-specified. The mitigation is a loose schema on the reasoning fields and a strict one only on the fields code consumes.

---

## 5. A closed action space, not free-form tool calling

**Problem.** An agent that can generate arbitrary `kubectl` commands can do arbitrary damage.

**Options.** (a) Give the model a shell. (b) Give it a broad tool catalogue. (c) Give it a closed label set, scoped per cause class.

**Chosen.** (c). See `router/action_policy.yaml`.

**Why.** The model does not choose *what to do*. It classifies the failure and selects a label from a small allow list attached to that cause. The parameters are filled by code. This collapses the safety review from "can the model be trusted with a cluster" to "is this map correct", which is a question a reviewer can actually answer by reading a 30-line file.

It also means a wrong diagnosis has a bounded blast radius. In the eval, both engines proposed only in-policy actions, and the two SLM misclassifications both fell into an action that was inside policy and human-gated. The cost of being wrong is a rejected suggestion.

**What would change my mind.** Nothing, for mutating actions in a production cluster. For read-only investigation the argument for broader tool access is much stronger, and that is where a wider catalogue would go first.

---

## 6. Human gate on every mutating action, not autonomous remediation

**Problem.** Autonomous remediation is the most impressive-sounding version of this system and the one most likely to be banned by the first risk review it meets.

**Chosen.** Every mutating action goes to a human via PagerDuty with the diagnosis, the evidence, the blast radius, and the rollback attached.

**Why.** Two reasons, one cultural and one technical. Culturally, no regulated enterprise is granting an unproven model write access to production, so an autonomous design is a demo that never ships. Technically, the value in incident response is concentrated in time-to-diagnosis, not time-to-click. If the diagnosis is correct and arrives in under a second with a pre-written patch and a rollback, the approval keystroke is not the bottleneck.

The gate is also what makes the accuracy trade in decision 2 acceptable. An 85% accurate autonomous system is dangerous. An 85% accurate system that proposes a bounded change with the evidence shown is useful, because the human is verifying rather than diagnosing.

**What would change my mind.** Sustained accuracy above roughly 98% on a specific narrow cause class, with a proven rollback path, would justify auto-remediating that one class while everything else stays gated. Autonomy is earned per class, not granted globally.

---

## 7. Hand-rolled agent loop, not a framework

**Problem.** Pydantic AI, LangGraph and similar frameworks handle orchestration well and are the industry default.

**Chosen.** A hand-written observe, decide, act, reflect loop, roughly 120 lines, behind a swappable model interface.

**Why.** For a system this size the framework abstracts away exactly the part that is under scrutiny. When a reviewer asks why the agent retried, or where the context was truncated, or what the model actually received, that has to be answerable by pointing at a line, not by explaining a framework's internals. The loop is small enough to read in one sitting.

This is a scale-dependent call, not a principled objection to frameworks. Past a handful of agents with real branching and persistence, hand-rolling becomes the wrong answer and LangGraph becomes the right one.

**What would change my mind.** Multi-agent handoff with durable state and replay. That is where a framework starts paying for itself and the hand-rolled version starts reimplementing one badly.

---

## 8. An offline golden set with a scoring harness, not demo-by-vibes

**Problem.** Most agent demos are a single happy path. They prove nothing about the tenth case.

**Chosen.** Twenty scored incidents, five scored dimensions, a harness that runs on bare `python3`, and a report generated from the data.

**Why.** This is the difference between a demo and an engineering artifact. It makes regression visible, makes the model swappable without changing the claim structure, and makes every published number reproducible by anyone who clones the repo. The known misses are documented rather than removed, because a benchmark you can only pass is not a benchmark.

Zero dependencies is a deliberate constraint. The harness has to run on a stranger's laptop during a live conversation without a `pip install` step failing.

**What would change my mind.** Nothing about the approach. The set size is the weakness: twenty incidents is enough to catch gross regression and far too small for a confident accuracy claim. It grows as real incidents are curated in, and the accuracy figures should be read as directional until it does.

---

## 9. Agent telemetry goes into the existing LGTM stack, not a dedicated LLM observability tool

**Problem.** The agent needs its own observability: token spend, latency, tool calls, failure rates.

**Options.** (a) Langfuse or Phoenix. (b) OpenTelemetry GenAI semantic conventions into the LGTM stack already running.

**Chosen.** (b).

**Why.** The platform already has Alloy, Tempo, Loki, Prometheus and Grafana with a working RED and SLO pipeline. Emitting `gen_ai.*` spans into that path means the agent is monitored by the same system it is diagnosing, with the same dashboards, the same alerting, and no second backend to run. It also avoids vendor lock-in on a young and fast-moving tool category.

The stronger argument is narrative: an SRE-built AI system should be observable the way SREs observe things. Cost per request and time-to-first-token become additional golden signals, not a separate world.

**What would change my mind.** Prompt-level debugging and trace comparison across prompt versions are genuinely better in a purpose-built tool. If prompt iteration becomes the bottleneck rather than infrastructure, adding one alongside is reasonable.

---

## 10. Self-hosted vLLM, not a hosted inference endpoint

**Problem.** The model needs to be served somewhere.

**Chosen.** vLLM on infrastructure inside the trust boundary.

**Why.** Incident context is production telemetry: hostnames, service topology, error strings, sometimes customer-adjacent identifiers. In financial services and healthcare that data does not leave the environment, and the router anonymizes before any external call regardless. Self-hosting also makes prefix caching and continuous batching available, which is where the token economics in the eval actually come from.

**What would change my mind.** Very low and bursty incident volume. Paying for an idle GPU to serve a handful of daily incidents is worse than a hosted endpoint. The break-even is a real calculation, and it lives in `evals/cost_model.json`.

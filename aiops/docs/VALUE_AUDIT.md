# Value audit

Structured against the five questions a value engineer asks. The dollar figures are placeholders sourced from `evals/cost_model.json`. The method is the deliverable, not the number.

## 1. Business problem

Production incidents cost money in three places: customer-visible downtime and any contractual penalty attached to it, engineer hours consumed by manual triage, and the opportunity cost of senior engineers being pulled off delivery work into diagnosis.

Existing automation does not touch the expensive part. Threshold alerting and PagerDuty routing solve *notification*. They do not solve *diagnosis*. Rule-based automation handles failure modes someone already anticipated and wrote a rule for, which by definition excludes the novel multi-signal failures that consume the most time.

The specific gap: time from alert to correct hypothesis. That interval is almost entirely human, almost entirely repeated across incidents that resemble each other, and almost entirely invisible in existing tooling.

## 2. Value created

| Lever | Mechanism | How it is measured |
| --- | --- | --- |
| Time to diagnosis | correct hypothesis with cited evidence delivered under a second | median alert-to-hypothesis timestamp delta, before and after |
| Triage load | a third of alerts resolved deterministically, never reaching a human | count of deterministic-route outcomes per month |
| Consistency | the same failure class gets the same diagnosis regardless of who is on call | variance in resolution path for repeated cause classes |
| Knowledge retention | every resolved incident writes a runbook back to the retrieval store | runbook coverage against observed cause classes |
| Inference spend | routing keeps the expensive model out of the routine path | cost per incident, tracked live in Grafana |

## 3. Tools and why each was chosen

Covered per decision in `DESIGN_DECISIONS.md`. The short form: deterministic rules where the answer is fixed, a fine-tuned small model where the distribution is narrow and recurring, a frontier model where breadth is genuinely needed, retrieval for anything that changes, and a human for anything that mutates state.

## 4. Prototype to scaled solution

The scaling axis is cause-class coverage, not request volume. Adding a failure class means adding golden incidents, adding the cause to the action policy, and retraining the adapter. It does not mean rearchitecting.

The path: single agent on one cluster, then the same chassis pointed at deployment verification and resource right-sizing, then multi-cluster with the retrieval store as the shared institutional memory. Each step reuses the loop, the contract, the guardrail and the harness.

## 5. How value was audited

Three levels, in increasing order of trustworthiness.

**Offline.** The harness scores every change against a fixed golden set. A regression in accuracy, schema compliance or policy adherence is visible before deployment, and the report regenerates from data rather than being written.

**Live.** The Agent Operations dashboard tracks cost per incident, deterministic bypass rate, guardrail block rate, and approval acceptance rate. Approval acceptance is the honest quality signal, because it is a human verdict on every single suggestion, recorded automatically.

**Business.** Time-to-diagnosis before and after, multiplied by loaded engineer cost and incident volume, minus inference spend. The critical discipline is that the baseline must come from the real incident record rather than from an estimate. A value claim built on an assumed baseline is not an audit, it is a projection with a table around it.

Stated plainly, and this is the part worth saying out loud in a room: at realistic incident volumes the inference savings are rounding error. Engineer time is the entire value. The architecture matters because it is what makes the model fast enough to sit in the live path and safe enough to be allowed there, not because it saves money on tokens.

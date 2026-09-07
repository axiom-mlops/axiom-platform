# Fine-tuning

Status: configured, not yet trained. `qlora_config.yaml` is the run definition.

## What the adapter is teaching

Not facts. Behavior. Three things specifically:

1. Emit a cause class from the closed vocabulary in `router/action_policy.yaml`, never an invented one.
2. Cite retrieved evidence for every claim, or return `collect_more_evidence`.
3. Produce schema-clean JSON on the first attempt, without prose wrapping.

Facts live in retrieval, because runbooks and thresholds change weekly and weights do not.

## Dataset construction

Sources, in order of value: resolved incidents with a confirmed root cause, existing runbooks reshaped into signal-to-cause pairs, and synthetic negatives.

The negatives matter more than the positives. `train.sample.jsonl` includes a refusal example on purpose. A model trained only on solvable incidents learns that every input has an answer, which is the exact failure you cannot afford in an on-call path.

Anonymization runs before anything enters the training set: hostnames, account identifiers, customer-adjacent strings.

## Holdout discipline

The golden eval incidents are excluded from training. This is stated in the config and enforced in the data build. A model scored on its own training data is not scored.

## Acceptance gate

The adapter does not replace the current one unless it clears the thresholds in `qlora_config.yaml` against the golden set. Promotion is a gate, not a judgement call.

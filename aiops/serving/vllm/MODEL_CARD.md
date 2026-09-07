# Model card, diagnostician

| Field | Value |
| --- | --- |
| Base | Qwen3.5-9B, Apache 2.0 |
| Adaptation | QLoRA adapter, r=32, incident RCA task |
| Serving | vLLM, constrained decoding, prefix caching |
| Status in this snapshot | not trained, not served. Replay fixtures stand in |
| Intended use | proposing a root cause and a bounded, human-gated remediation for Kubernetes incidents in a single estate |
| Out of scope | autonomous remediation, security incident response, anything outside the trained cause vocabulary |

## Known limitations

- Misclassifies failures whose distinguishing signal is a comparison across scopes rather than a threshold on one series. Two documented cases in the golden set.
- Twenty-incident evaluation set. Accuracy figures are directional.
- Confidence is a calibration target used for gate thresholds. It is not a statistical confidence interval, and it should not be described as one.

## Safety posture

The model has no credentials and cannot execute. It classifies and selects from a closed action set. Every mutating outcome requires human approval, carries a captured rollback, and is auto-reverted if the SLI does not recover within the verification window.

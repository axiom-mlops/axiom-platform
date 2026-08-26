# aiops/ — cluster-side integration for the agent layer

The reasoning code lives in [axiom-aiops](https://github.com/axiom-mlops/axiom-aiops).
This directory is the seam where it meets the cluster: the alert that triggers it,
the route that delivers it, and the identity that bounds what it can touch.

| Path | What it does |
|---|---|
| `alerts/saturation-rules.yaml` | Defines `ContainerMemoryNearLimit` — the alert the agent's demo diagnoses — plus a rule that detects the HPA blind spot directly |
| `alerting/alertmanager-agent-route.yaml` | Routes `aiops_eligible` alerts to the agent webhook while the human page fires unchanged |
| `rbac/agent-serviceaccount.yaml` | ServiceAccount, Role, RoleBinding scoping the executor to HPA patches and scale subresources |

## The design property these three files enforce

The agent's safety story has two halves, and only one of them is in the agent's code.

In [axiom-aiops](https://github.com/axiom-mlops/axiom-aiops), the model selects from a
typed whitelist of actions and a human gate stands between proposal and execution.
That is application-level control — necessary, but it is code, and code has bugs.

These files are the half that does not depend on the agent behaving correctly. The
Role grants patch on HorizontalPodAutoscalers and nothing else, so a compromised,
confused, or prompt-injected agent still cannot delete a Deployment, read a Secret,
or exec into a pod. The API server refuses, and no amount of clever input changes that.

Defence in depth: the whitelist is what the agent *will* do; RBAC is what it *can* do.

## Apply

```bash
kubectl apply -f aiops/rbac/agent-serviceaccount.yaml
kubectl apply -f aiops/alerts/saturation-rules.yaml
kubectl apply -f aiops/alerting/alertmanager-agent-route.yaml

# Confirm the permission boundary is real
kubectl auth can-i patch hpa --as=system:serviceaccount:boutique:aiops-rca-agent -n boutique   # yes
kubectl auth can-i delete deployment --as=system:serviceaccount:boutique:aiops-rca-agent -n boutique  # no
kubectl auth can-i get secrets --as=system:serviceaccount:boutique:aiops-rca-agent -n boutique       # no
```

Those three `can-i` checks are worth running on camera. They turn "least privilege"
from an assertion into a demonstration.

## Status

Alert rules and RBAC are applyable today. The webhook receiver in axiom-aiops
currently runs as a CLI demo against captured fixtures; wiring it as an in-cluster
Service is the next slice — see [../docs/architecture/ROADMAP.md](../docs/architecture/ROADMAP.md).

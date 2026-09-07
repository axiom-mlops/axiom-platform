# Infrastructure

Status: partial and honestly labelled. Terraform for EKS is scaffolded, Helm packaging is scaffolded. Local development runs on Docker Desktop Kubernetes on Apple Silicon, which is where the platform and the LGTM stack are actually validated to 5,000 virtual users.

## Demo posture

The EKS target is two spot `t3.large` nodes, provisioned for a session and torn down after. A GPU node group is only attached when the served model is in play, which in this snapshot it is not.

This is a cost decision, not a capability one. An always-on cluster for a portfolio project burns money to prove something a fifteen-minute apply proves just as well.

## Layout

```
infra/
  terraform/   EKS cluster, node groups, IRSA for the agent service account
  helm/        agent chart, values per environment
```

## Access model worth noting

The agent service account has a narrow role: read telemetry, and patch a specific set of resource kinds inside one namespace. It cannot delete, cannot touch RBAC, and cannot reach another namespace. The action policy in `router/action_policy.yaml` is the logical guardrail. This is the one that holds if the logical guardrail is bypassed.

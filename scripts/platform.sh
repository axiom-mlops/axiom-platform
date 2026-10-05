#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
cmd="${1:-}"; env="${2:-local-mac}"
case "$env" in
  local-mac) ctx="docker-desktop" ;;
  azure-dev) ctx="aks-axiom-dev" ;;
  aws-dev)   ctx="eks-axiom-dev" ;;
  *) echo "unknown env: $env"; exit 2 ;;
esac
case "$cmd" in
  render)   kubectl kustomize "clusters/$env" ;;
  diff)     kubectl --context "$ctx" diff -k "clusters/$env" || true ;;
  apply)    kubectl --context "$ctx" apply -k "clusters/$env" ;;
  validate) for e in local-mac azure-dev aws-dev; do echo "== $e"; kubectl kustomize "clusters/$e" | kubeconform -strict -ignore-missing-schemas -summary -; done ;;
  *) echo "usage: scripts/platform.sh {render|diff|apply|validate} [local-mac|azure-dev|aws-dev]"; exit 2 ;;
esac

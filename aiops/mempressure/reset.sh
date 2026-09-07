#!/usr/bin/env bash
set -euo pipefail
kubectl apply -f "$(dirname "$0")/k8s/mempressure.yaml"
kubectl rollout status deploy/mempressure -n boutique --timeout=120s
kubectl get pods -n boutique -l app=mempressure
echo "mempressure reset to clean baseline."

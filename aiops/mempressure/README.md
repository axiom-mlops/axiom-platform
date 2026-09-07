# mempressure

Purpose-built pressure service for the FDE demo. Produces a deterministic,
reproducible autoscaler-blindspot incident: memory climbs with request
concurrency while a CPU-only HPA stays blind.

/heavy    per-request memory hold; memory scales with concurrency.
          Correct remediation: add a memory target to the HPA (scale-out sheds per-pod memory).
/allocate fixed blob; constant-high regardless of replicas.
          Correct remediation: vertical right-sizing.
/release  clears the fixed blob.

Build:  docker build -t mempressure:demo .
Deploy: kubectl apply -f k8s/mempressure.yaml
Load:   kubectl port-forward svc/mempressure -n boutique 8080:8080
        k6 run load/load.js

# axiom-platform: Reliability substrate for agentic AIOps

## The problem

Incident response is the most expensive toil in operations, and the expensive part is
not the fix. It is the 30–90 minutes an on-call engineer spends correlating metrics,
logs, and configuration before anyone knows *what* to fix. That interval is MTTR, and
MTTR is revenue on any path that touches checkout.

Rule-based automation does not close it. PagerDuty routes, threshold alerts, and
restart scripts handle failures someone already anticipated. They cannot reason across
signals about a failure mode nobody wrote a rule for which is precisely the class of
incident that consumes the 90 minutes.

The concrete instance this project is built around: a service that is **memory-bound
under load while its HPA scales on CPU only**. Memory climbs toward the pod limit, CPU
stays under the autoscaling target, no scale-out happens, and pods trend toward
OOMKill while the dashboard for the metric the autoscaler watches stays green. No
single signal is alarming. The diagnosis lives in the *correlation*, which is exactly
the part that costs an engineer an hour at 3am.

## The solution

An agent that performs the correlation-and-diagnosis phase in seconds and proposes the
fix, while a human stays accountable for the decision to change production.

```
Alertmanager alert  (aiops/alerts/)
      │
      ▼
 OBSERVE    evidence sweep: memory vs limit, CPU vs request, HPA spec, error logs
      ▼
 DIAGNOSE   structured root cause + confidence + blast radius, validated into a
      │     typed contract low confidence escalates instead of guessing
      ▼
 PROPOSE    a fix from a whitelisted action space, never a generated command
      ▼
 GATE       human approval the act-plane is structurally unreachable without it
      ▼
 EXECUTE    scoped Kubernetes patch under a Role that permits HPA writes and nothing else
      ▼
 VERIFY     did the signals actually recover? applied-but-ineffective is surfaced
      ▼
 RUNBOOK    written from the audit trail, for the next human
```

Agent implementation: **[axiom-aiops](https://github.com/axiom-mlops/axiom-aiops)**.
Cluster-side integration: **[`aiops/`](aiops/)**. Design reasoning and per-slice
delivery status: **[`docs/architecture/`](docs/architecture/)**.

## Results

Split deliberately into what is measured and what is modeled. The distinction is the
point: an AIOps project that cannot tell you which is which is not ready to be trusted
with production.

### Measured today

| Result | Evidence |
|---|---|
| Full incident lifecycle runs end to end on the HPA memory blind-spot scenario | [`demo/transcript.md`](https://github.com/axiom-mlops/axiom-aiops/blob/main/demo/transcript.md) |
| Agent produces a correct root cause and a correct remediation for that scenario | same transcript, `confidence=high` |
| Gate denial results in zero cluster writes; low confidence escalates instead of acting | golden-scenario tests, green in CI |
| Executor cannot exceed its permission boundary | `kubectl auth can-i patch hpa` → yes; `delete deployment`, `get secrets` → no |
| Runbook is generated from the audit trail, not hand-written | [agent-generated runbook](https://github.com/axiom-mlops/axiom-aiops/blob/main/docs/runbooks/agent-generated-hpa-memory-blindspot.md) |
| Platform sustains 1,000 VU with HPA scale-out under load | `scripts/load-test_1000vusers.js`, Grafana dashboards |

### Modeled, not yet measured

The numbers below are a **worked model with stated assumptions**, not observations.
They exist to show the auditing method, which is the portable artifact the figures
themselves would be re-derived per environment.

| Quantity | Human-only | With agent | Assumption |
|---|---|---|---|
| Time to diagnosis | 30–90 min | seconds | Correlation is the dominant term; the agent's sweep is fixed-cost |
| Time to safe remediation | diagnosis + drafting + review | diagnosis + one approval | Patch is pre-vetted and whitelisted |
| Knowledge capture | postmortem days later, or never | runbook at resolution time | Generated from the audit trail |

**Method, once the agent runs against real incidents:** instrument each loop stage for
duration and outcome; price on-call time and revenue-per-minute of the affected path;
cohort-compare a quarter of incidents before and after. Agent telemetry lands in the
same Grafana stack it diagnoses, see
[ADR-005](docs/architecture/ADR-005-value-audit.md).

### Honest status

The loop, its safety properties, and the cluster integration are shipped and tested.
The live model backend, RAG over incident history, and the fine-tuned on-prem SLM are
designed and documented, not built every slice is marked in
[ROADMAP.md](docs/architecture/ROADMAP.md). Safety scaffolding was built first on
purpose: the backend swaps in behind a Protocol, and model quality only matters once
the surrounding system can absorb a wrong answer.

---

## The platform underneath

Kubernetes microservices platform with a full LGTM observability stack, HPA autoscaling,
chaos readiness, and a load-testing harness the environment the agents run against.

[![CI — Manifest Validation](https://github.com/axiom-sre/sre-demo-platform/actions/workflows/ci.yaml/badge.svg)](https://github.com/axiom-sre/sre-demo-platform/actions/workflows/ci.yaml)
[![Stack](https://img.shields.io/badge/stack-LGTM-orange)](https://grafana.com/oss/)
[![k8s](https://img.shields.io/badge/kubernetes-docker--desktop-blue)](https://www.docker.com/products/docker-desktop/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 📐 Architecture

```
┌─────────────────────────────────────────────────────────┐
│  namespace: boutique                                    │
│  Google Online Boutique (11 microservices)              │
│  frontend · cart · checkout · product · recommend ...   │
│  HPA on 6 services scales to 1000+ VU                │
└────────────────────┬────────────────────────────────────┘
                     │ OTLP traces · pod logs · /metrics
┌────────────────────▼────────────────────────────────────┐
│  namespace: observability  (LGTM stack)                 │
│                                                         │
│  Grafana Alloy (DaemonSet)                              │
│    ├─ Traces  → Tempo   (2.4.1)   ← trace backend      │
│    ├─ Metrics → Prometheus (2.51) ← metrics backend    │
│    └─ Logs    → Loki (3.0.0)      ← log backend        │
│                                                         │
│  Grafana (10.x): 3 pre-loaded dashboards              │
│    ├─ Golden Signals                                    │
│    ├─ Pod & Platform Stats                              │
│    └─ SLI / SLO / Error Budget                         │
└─────────────────────────────────────────────────────────┘
```

## 🗂️ Repo Layout

```
aiops/                          # seam between the platform and the agent layer
├── alerts/                     # PrometheusRule the agent consumes
├── alerting/                   # Alertmanager route to the agent webhook
└── rbac/                       # ServiceAccount + Role bounding the executor

k8s/
├── boutique/
│   ├── boutique.yaml          # All 11 boutique services + Redis
│   └── hpa.yaml               # HPA for 6 services (CPU-budget tuned)
├── namespaces/
│   ├── namespaces.yaml        # observability + boutique namespaces + ResourceQuotas
│   └── priority-classes.yaml  # observability-high + system-node-critical
├── observability/
│   ├── alloy/alloy.yaml       # DaemonSet: OTLP collector + log shipper (River syntax)
│   ├── grafana/grafana.yaml   # Grafana + 3 dashboards (PVC-backed)
│   ├── infrastructure/        # metrics-server, node-exporter, kube-state-metrics
│   ├── loki/loki.yaml         # Loki 3.0 (filesystem backend)
│   ├── prometheus/prometheus.yaml  # Prometheus 2.51 (TSDB + remote_write receiver)
│   └── tempo/tempo.yaml       # Tempo 2.4.1 (WAL + metrics_generator)
└── scripts/
    ├── bootstrap.sh           # First-time setup (run once)
    ├── start.sh               # Full stack startup (after every reboot)
    ├── manage.sh              # stop / nuke / status / debug / logs / budget
    ├── verify-stability.sh    # Pipeline health verification
    ├── find-url.sh            # Finds the correct k6 BASE_URL
    ├── load-test_10vusers.js  # Smoke test
    ├── load-test_100vusers.js # Moderate load
    └── load-test_1000vusers.js # Full chaos-ready load test
```

## ⚡ Quick Start

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker Desktop | 4.28+ | [docker.com](https://www.docker.com/products/docker-desktop/) |
| kubectl | 1.29+ | `brew install kubectl` |
| k6 | 0.50+ | `brew install k6` |

**Docker Desktop resources (required):**
- Memory: **24 GB** (minimum 20 GB)
- CPU: **8 cores**
- Enable Kubernetes in Docker Desktop → Settings → Kubernetes

### First-time setup (once per machine)

```bash
git clone git@github.com:axiom-mlops/axiom-platform.git
cd axiom-platform/k8s
bash scripts/bootstrap.sh
```

Bootstrap takes ~5-10 min on first run (image pulls ~2 GB).

### After every reboot

```bash
cd k8s
bash scripts/start.sh
```

### Verify everything is healthy

```bash
bash scripts/verify-stability.sh --short
bash scripts/manage.sh status
bash scripts/manage.sh budget
```

---

## 🌐 Endpoints

| Service | URL | Notes |
|---------|-----|-------|
| Boutique | http://localhost:8080 | LoadBalancer → NodePort 30080 |
| Grafana | http://localhost:3000 | `admin` / `admin` |
| Prometheus | http://localhost:9090 | |
| Alloy UI | http://localhost:12345 | Pipeline graph |
| Tempo | http://localhost:3200 | |
| Loki | http://localhost:3100 | |

---

## 🔨 Load Testing

```bash
# Find the correct BASE_URL for your Docker Desktop config
export BASE_URL=$(bash scripts/find-url.sh --export)

# Smoke test (10 VU, 2 min)
k6 run --env BASE_URL=$BASE_URL scripts/load-test_10vusers.js

# Moderate load (100 VU)
k6 run --env BASE_URL=$BASE_URL scripts/load-test_100vusers.js

# Full load triggers HPA scale-out (1000 VU)
k6 run --env BASE_URL=$BASE_URL scripts/load-test_1000vusers.js

# Watch HPA react in real time (separate terminal)
bash scripts/manage.sh hpa-watch
```

---

## 🛠️ Operations

```bash
bash scripts/manage.sh status       # pod + HPA + PVC status
bash scripts/manage.sh debug        # full diagnostic dump
bash scripts/manage.sh budget       # node CPU/memory budget
bash scripts/manage.sh logs frontend boutique   # tail service logs
bash scripts/manage.sh cart-debug   # deep-dive cartservice diagnostics
bash scripts/manage.sh restart frontend boutique
bash scripts/manage.sh top          # kubectl top for both namespaces
bash scripts/manage.sh stop         # graceful teardown (PVCs preserved)
bash scripts/manage.sh nuke         # full reset, deletes all data
```

---

## 📊 Grafana Dashboards

Three pre-loaded dashboards (survive pod restarts via PVC):

| Dashboard | What it shows |
|-----------|--------------|
| **Boutique — Golden Signals** | Latency, traffic, errors, saturation per service |
| **Boutique — Pod & Platform Stats** | HPA replica counts, CPU/memory by pod, node pressure |
| **Boutique — SLI / SLO / Error Budget** | 99.9% availability SLO, error budget burn rate |

---

## 🏗️ Design Decisions

### Why Alloy as a DaemonSet?
Log collection via `hostPath:/var/log/pods` requires one agent per node. A DaemonSet ensures Alloy is on every node automatically. `system-node-critical` PriorityClass means it survives node pressure events.

### Why PriorityClasses?
At 1000 VU, boutique HPA scale-out creates node CPU/memory pressure. Without priority, the scheduler evicts observability pods first (largest memory consumers). `observability-high` ensures Prometheus, Tempo, and Loki survive boutique scaling storms.

### Why the cart HPA maxReplicas was reduced from 8 → 5?
8 × 500m = 4000m requests for cartservice alone. At 1000 VU this fires simultaneously with frontend scaling (10 × 500m = 5000m), exhausting node CPU budget and causing cascading Pending pods. See `hpa.yaml` for full budget math.

### Why 60s terminationGracePeriodSeconds on Tempo?
Tempo's ingester WAL flush + block compaction takes 15-30s under load. SIGTERM during this window loses the current WAL block. 30s was too tight at 1000 VU sustained for 10+ min.

---

## 🗺️ Roadmap

- [ ] Chaos Engineering (Chaos Mesh / LitmusChaos)
- [x] Alerting rules (Prometheus) — `aiops/alerts/`
- [x] Alert routing to the agent + on-call — `aiops/alerting/`
- [ ] SLO burn rate alerts (Sloth)
- [ ] Distributed load testing (k6 operator)
- [ ] GitOps (FluxCD / ArgoCD)
- [ ] Multi-cluster simulation (kind)
- [x] Runbook automation: agent writes runbooks from its audit trail

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 📄 License

MIT — see [LICENSE](LICENSE).

## Agentic AIOps

Deterministic-first incident diagnosis with a fine-tuned SLM, closed-action
guardrails, and a runnable evaluation harness: [`aiops/`](aiops/README.md)

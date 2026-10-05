# datadog integration

Status: Planned. Not enabled on any cluster.

Integration contract, identical for every vendor in observability/vendors/:

1. OTLP fan-out: an exporter on the Alloy gateway sends traces, metrics and logs to the vendor's OTLP endpoint. Credentials come from a Kubernetes Secret, never from git.
2. Native agent, optional: the vendor's operator or Helm chart, deployed as its own Argo CD application, for capabilities OTLP does not cover.
3. Opt-in: a cluster enables it by listing this folder under components: in clusters/<env>/kustomization.yaml. LGTM remains the system of record.

Planned files: kustomization.yaml (kind: Component), values.yaml, alloy-exporter.alloy, secret.example.yaml.

# Retrieval

## What is indexed

| Corpus | Refresh | Why it is retrieval and not weights |
| --- | --- | --- |
| Runbooks | on commit | changes weekly, must be current at inference time |
| Resolved incidents with confirmed cause | on resolution | this is the institutional memory the whole system compounds on |
| Current HPA targets, limits, SLO definitions | hourly | live configuration, stale values produce confidently wrong RCAs |
| Postmortems | on commit | narrative context for cause classes that recur |

## Pipeline

BGE-M3 embeddings, Qdrant vector store, a reranker over the top candidates, orchestrated in LlamaIndex. Ingestion is asynchronous so a runbook commit does not block the incident path.

## Context slicing

The retrieved set is sliced before it reaches the model: highest-ranked chunks within the incident time window, deduplicated against the system prompt. Measured reduction against the unsliced window is 40%, computed in `evals/harness/run_eval.py` from both token counts rather than asserted.

The reason to care is not the cost. It is that irrelevant context measurably degrades reasoning quality, and an on-call path cannot afford a model distracted by last month's unrelated outage.

## Failure mode to watch

Retrieval returning nothing is a valid and important outcome. The correct response is `collect_more_evidence`, not a guess from parametric memory. This is trained in explicitly and enforced by the evidence-presence check in the guardrail.

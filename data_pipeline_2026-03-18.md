# How the Transformation Branch Works

This branch builds the **data pipeline** for a multi-agent Kubernetes Root Cause Analysis (RCA) system. Here's the flow:

## Pipeline Overview

```
k8s_synth_generator_portfolio.py   →   synthetic_source.jsonl (8,502 incidents)
                                              ↓
                                    transform_incidents.py
                                      ↓                ↓
                            agent1_structured.parquet   agent2_evidence.parquet
                            (tabular/ML models)        (semantic/LLM models)
```

## 1. Synthetic Data Generation (`k8s_synth_generator_portfolio.py`)

Generates **8,502 synthetic Kubernetes incidents** across **18 failure scenarios** (500 each), including:

- CrashLoopBackOff (OOM, bad args, probe failures, RBAC, DNS)
- ImagePull errors, missing secrets/configmaps
- Scheduling failures (taints, resources, node selectors)
- PVC mount failures, quota exceeded

Each incident includes realistic `kubectl` output (get pods, describe, events), container logs, metrics, and structured remediation steps.

## 2. Agent-Specific Transforms (`transform_incidents.py`)

### Agent 1 — Structured RCA (`agent1_structured.parquet`)

- Flattened tabular format (27 columns) for tree-based models (XGBoost, LightGBM)
- Features: pod_status, waiting_reason, error_message, restart_count, event signals, plus derived `symptom_family` and `root_cause_family` labels

### Agent 2 — Semantic/Evidence (`agent2_evidence.parquet`)

- Combined text fields for LLM/RAG models
- Evidence text (kubectl output), diagnosis, fix plans, kubectl actions, verification, and rollback steps

## 3. Infrastructure (Kubernetes YAMLs)

- **ELK Stack** (Elasticsearch + Logstash + Kibana) for log ingestion and visualization
- **Spark notebook** deployment for log enrichment and analysis

## 4. Notebooks

- `rca_data_transforms.ipynb` — demonstrates the transformation workflow
- `spark_data.ipynb` — Spark log enrichment
- `json_to_parquet_pipeline.ipynb` — format conversion

## Multi-Agent Model Context

The branch feeds into Models 1-6 described in the project's `multi_agent_models.md`, with Agent 1 handling structured classification and Agent 2 handling semantic understanding/remediation generation.

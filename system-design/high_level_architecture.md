# High-Level System Architecture

## Multi-Agent Collaboration System for Kubernetes Root Cause Analysis

---

## Cloud Deployment Architecture (K8s)

```
+-------------------------------+          +------------------------------------------------------+
|   User Application Node       |          |   K8s Cluster (EKS / Kind)                           |
|                               |          |                                                      |
|  +-------------------------+  |          |  +---------------------+   +----------------------+  |
|  |   User Application      |  |          |  |  MAS Collab System  |<->|  Nexus Trace         |  |
|  +-------------------------+  |   logs   |  |  (Multi-Agent       |   |  Application         |  |
|  |   Filebeat              |--|--------->|  |   Coordination      |   |  (Incident Tracking)  |  |
|  +-------------------------+  |          |  |   Layer)             |   +----------+-----------+  |
|  | Node Exporter+Prometheus|  |          |  +---------------------+              |              |
|  +-------------------------+  |          |                                       v              |
|                               |          |                              +--------+--------+     |
+-------------------------------+          |                              |     MongoDB      |     |
                                           |                              |  (Incident Store) |     |
                                           |                              +-----------------+     |
                                           |                                                      |
                                           |  +----------+     +-----------+                      |
                                           |  |  Kafka   |---->|   ELK     |                      |
                                           |  | (Stream) |     | (Elastic, |                      |
                                           |  +----------+     |  Logstash,|                      |
                                           |                   |  Kibana)  |                      |
                                           |                   +-----------+                      |
                                           |                                                      |
                                           |  +-------------------+   +------------------------+  |
                                           |  | Ray               |   | Spark                  |  |
                                           |  | (Model Training)  |<->| (Data Transformation)  |  |
                                           |  +--------+----------+   +----------+-------------+  |
                                           |           |                         |                 |
                                           +-----------+-------------------------+-----------------+
                                                       |                         |
                                                       v                         v
                                           +---------------------+    +---------------------+
                                           |     S3 Bucket       |<---|  Python Generated   |
                                           |  (Model Artifacts,  |    |  Data               |
                                           |   Training Data,    |    |  (Synthetic K8s     |
                                           |   Parquet Files)    |    |   Incidents)         |
                                           +---------------------+    +---------------------+
```

### Cloud Deployment Components

| Component | Purpose | Technology |
|-----------|---------|-----------|
| **User Application** | Monitored K8s workloads generating logs and metrics | Application pods |
| **Filebeat** | Ships container logs from each node to the processing pipeline | Filebeat DaemonSet |
| **Node Exporter + Prometheus** | Collects node-level and pod-level metrics | Prometheus stack |
| **Kafka** | Event streaming buffer between log producers and consumers | Apache Kafka |
| **ELK Stack** | Log ingestion (Logstash), indexing (Elasticsearch), visualization (Kibana) | ECK 8.14 |
| **Spark** | Data transformation — raw JSONL to agent-specific Parquet datasets | Apache Spark |
| **Ray** | Distributed model training — LoRA fine-tuning of Mistral-7B and Qwen2.5-7B | Ray Train |
| **S3 Bucket** | Central storage for training data, Parquet files, and LoRA model artifacts | AWS S3 |
| **Python Generated Data** | Synthetic incident generator producing 8,502 K8s incidents | `k8s_synth_generator_portfolio.py` |
| **MongoDB** | Persistent store for incident records and RCA results | MongoDB |
| **Nexus Trace Application** | Incident tracking and traceability UI for SRE teams | Web application |
| **MAS Collab System** | The multi-agent coordination layer (15 agents, 4 models) | Python + LLM APIs |

---

## Logical System Architecture

```
+-------------------------------+          +-------------------------------+
|   Data Sources                |          |   External Storage            |
|                               |          |                               |
|   User App Logs (Filebeat) ---|---+      |   S3 Bucket                   |
|   K8s Events (API server)  ---|---+      |     - synthetic_source.jsonl  |
|   Metrics (Prometheus)     ---|---+      |     - agent1_structured.parq  |
|   Synthetic Generator      ---|---+      |     - agent2_evidence.parq    |
+-------------------------------+   |      |     - LoRA adapters           |
                                    |      +-------------------------------+
                                    |                  ^       |
                                    v                  |       v
+--------------------------------------------------------------------+
|                                                                    |
|   Data & ML Pipeline (Spark + Ray)                                 |
|                                                                    |
|   Kafka --> ELK (ingest + index)                                   |
|                                                                    |
|   Spark: transform_incidents.py                                    |
|     JSONL --> agent1_structured.parquet (27 cols)                   |
|           --> agent2_evidence.parquet   (16 cols)                   |
|                                                                    |
|   Ray: LoRA Fine-Tuning                                            |
|     Mistral-7B (classification) + Qwen2.5-7B (generation)         |
|     4-bit NF4 quantization, SFTTrainer, cosine LR                 |
|                                                                    |
+------------------------------+-------------------------------------+
                               |
                     LoRA adapters + Parquet data
                               |
                               v
+--------------------------------------------------------------------+
|                                                                    |
|   Multi-Agent Coordination Layer (15 agents, 4 models)             |
|                                                                    |
|   +------------------------------------------------------------+   |
|   |  M1: Sequential Pipeline (6 agents, ~$0)                  |   |
|   |  Ingestion -> Preprocess -> Detect -> Planner -> Execute   |   |
|   +------------------------------------------------------------+   |
|                         | inherits + adds                          |
|   +------------------------------------------------------------+   |
|   |  M2: + Shared Knowledge (7 agents, ~$0)                   |   |
|   |  + Retrieval Agent --> FAISS Knowledge Base --> Planner    |   |
|   +------------------------------------------------------------+   |
|                         | inherits + adds                          |
|   +------------------------------------------------------------+   |
|   |  M3: + Blackboard & Validation (9 agents, ~$0.10)         |   |
|   |  + Intent Classifier + Validation Agent + Blackboard       |   |
|   +------------------------------------------------------------+   |
|                         | inherits + adds                          |
|   +------------------------------------------------------------+   |
|   |  M4: + Debate, Safety & Memory (14 agents, ~$0.45)        |   |
|   |  + 3 Debaters + Referee + Safety (HiTL) + Memory Manager  |   |
|   +------------------------------------------------------------+   |
|                                                                    |
+---------------------+------------------+--------------------------+
                      |                  |
                      v                  v
        +-------------------+  +----------------------+
        |   Remediation     |  |   Continuous         |
        |   Executor Agent  |  |   Learning           |
        |   (kubectl cmds)  |  |   Memory Manager     |
        |   Reviewer Agent  |  |   indexes outcomes    |
        |   (verify+rollbk) |  |   for future RAG     |
        +-------------------+  +----------------------+
                      |                  |
                      v                  v
        +-------------------------------------------+
        |   Outputs & Observability                  |
        |                                            |
        |   RCA Report (diagnosis + fix + rollback)  |
        |   Kibana Dashboards (live logs, alerts)    |
        |   MongoDB (incident records)               |
        |   Nexus Trace (SRE tracking UI)            |
        +-------------------------------------------+
```

---

## System Components at a Glance

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Data Source** | Synthetic generator + live K8s + Prometheus | 8,502 incidents across 18 failure types + live metrics |
| **Streaming** | Apache Kafka | Event buffer between log producers and processing pipeline |
| **Data Pipeline** | Apache Spark, pandas, PyArrow | Transform raw JSONL into agent-specific Parquet datasets |
| **Model Training** | Ray + LoRA + bitsandbytes (4-bit) | Distributed fine-tuning of Mistral-7B + Qwen2.5-7B |
| **Storage** | AWS S3 | Training data, Parquet files, LoRA model artifacts |
| **Agent Layer** | 15 agents across 4 models | Progressive coordination: pipeline -> RAG -> blackboard -> debate |
| **Incident Store** | MongoDB | Persistent incident records and RCA results |
| **Observability** | ELK Stack (ECK 8.14) | Filebeat -> Logstash -> Elasticsearch -> Kibana |
| **Tracking UI** | Nexus Trace Application | Incident tracking and traceability for SRE teams |
| **Infrastructure** | K8s (Kind / EKS), Terraform, Flux CD | Local, AWS staging, AWS production |
| **Web UI** | Kibana dashboards | Live logs, alerts, agent activity, approval queue |

---

## 4 Models — Progressive Agent Coordination

```
    Model 1                 Model 2                Model 3                 Model 4
    Baseline                + Shared Knowledge     + Blackboard            + Debate & Safety
    --------                ------------------     + Validation            + Memory
                                                   ------------            ----------------

    6 agents                7 agents               9 agents                14 agents
    ~$0/incident            ~$0/incident            ~$0.10/incident        ~$0.45/incident

    Sequential              + Retrieval Agent      + Intent Classifier     + 3 Debaters
    message                 + FAISS Knowledge      + Validation Agent      + Referee
    passing                   Base (RAG)           + Shared Blackboard     + Safety Agent (HiTL)
                                                   + Opus/GPT-4o          + Memory Manager

    Mistral-7B              Qwen2.5-7B             Claude Opus 4           Opus + GPT-4o +
    LoRA                    LoRA                   GPT-4o                  DeepSeek-R1

    "Minimum viable        "Does shared           "Does validation       "Does debate +
     MAS that beats         knowledge              reduce                 safety + memory
     rules"                 help?"                 hallucinations?"       enable self-
                                                                          improvement?"
```

---

## Agent Autonomy Tiers

```
+-------------------------------------------------------------------+
|                                                                   |
|   TIER 1: Fully Autonomous (read-only, no side effects)           |
|   10 of 15 agents                                                 |
|                                                                   |
|   Data Ingestion, Preprocessing, Anomaly Detection,               |
|   Intent Classifier, Retrieval Agent, Validation Agent,           |
|   Debater 1, Debater 2, Debater 3, Referee                       |
|                                                                   |
+-------------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
|                                                                   |
|   TIER 2: Semi-Autonomous (writes, logged, reversible)            |
|   3 of 15 agents                                                  |
|                                                                   |
|   Planner (writes RCA plan)                                       |
|   Executor (low/medium risk: 30s delay for human override)        |
|   Memory Manager (writes to knowledge base)                       |
|   Reviewer (may trigger rollback)                                 |
|                                                                   |
+-------------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
|                                                                   |
|   TIER 3: Human-in-the-Loop (destructive actions blocked)         |
|   2 of 15 agents                                                  |
|                                                                   |
|   Safety Agent (gates all high-risk execution)                    |
|   Executor (high risk: delete pods, modify RBAC, scale to 0)     |
|                                                                   |
|   --> Human approves in Kibana dashboard before execution         |
|                                                                   |
+-------------------------------------------------------------------+
```

---

## Infrastructure

```
+------------------+     +---------------------+     +----------------------------------+
|   Local Dev      |     |   AWS Staging       |     |   AWS Production                 |
|                  |     |                     |     |                                  |
|   Kind (Docker)  |     |   EKS + Flux CD    |     |   EKS + Terraform                |
|   clusters/elk/  |     |   GitOps            |     |   v1.31, 2x c7i-flex.large      |
|   make apply     |     |   clusters/elk-aws/ |     |   VPC 10.0.0.0/16                |
|                  |     |                     |     |                                  |
|   Zero cost      |     |   Auto-reconcile    |     |   clusters/elk-prod/             |
+------------------+     +---------------------+     +----------------------------------+

All 3 environments run:
  Filebeat --> Kafka --> Logstash 8.14 --> Elasticsearch 8.14 --> Kibana 8.14

Cloud services:
  S3 Bucket     (training data, Parquet, LoRA adapters)
  MongoDB       (incident records, RCA results)
  Ray Cluster   (distributed LoRA fine-tuning)
  Spark         (data transformation at scale)
  Nexus Trace   (SRE incident tracking UI)
```

---

## End-to-End Data Pipeline

Data flows through two parallel tracks:

**ML/Agent Pipeline** (top row): Raw K8s incidents are transformed into training data, fine-tune LLMs, and orchestrate agents through one of four progressive coordination patterns (sequential -> RAG -> blackboard -> debate) to produce automated remediations.

**Observability Pipeline** (bottom row): Continuously ships, parses, indexes, and visualizes operational logs for human monitoring and post-incident review.

```
ML/AGENT PIPELINE:

  K8s Incident  -->  JSONL      -->  Transform     -->  Parquet     -->  LoRA Train   -->  Agent Pipeline  -->  Coordination  -->  Remediate
  Synthetic/Live     40 MB raw       Flatten+Encode     Agent 1 & 2     Mistral+Qwen      Detect->RCA->Plan   4 Model Patterns   Execute+Verify
                         |                                  |                |                    |                   |
                         v                                  v                v                    v                   v
                     S3 Bucket <----------------------- S3 Bucket       S3 Bucket           MongoDB            Memory Manager
                     (raw data)                        (Parquet)        (adapters)          (results)          (self-improvement)


OBSERVABILITY PIPELINE:

  Filebeat      -->  Kafka       -->  Logstash       -->  Elasticsearch  -->  Kibana
  Ship logs          Stream buffer    Parse & normalize    Index & store       Visualize
  (DaemonSet)                                                                 (dashboards, alerts,
                                                                               agent activity,
                                                                               approval queue)
```

---

## Evaluation Framework

```
                    Same 500 held-out incidents
                              |
          +-------------------+-------------------+
          |         |         |                   |
        Model 1   Model 2   Model 3             Model 4
          |         |         |                   |
        Fuzzy     Combined  RCA Acc +           Debate diversity +
        Accuracy  Score     Hallucination       Error-repeat +
        + F1      (struct   Detection Rate      Safety violations +
        + Conf.    + kw)    + Tool-call Acc     Human override rate
        Matrix                                        |
          |         |         |                       |
          +---------+---------+-----------------------+
                              |
                    Friedman test (4 models)
                    McNemar's (pairwise)
                    Accuracy vs. Cost curve
```

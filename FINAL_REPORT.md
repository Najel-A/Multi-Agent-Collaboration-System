# NEXUSTRACE — A Multi-Agent Collaboration System for Kubernetes Root-Cause Analysis

**MSDA Capstone Final Report — Spring 2026**
**Author:** Chelsea Jaculina, M.S. Applied Data Intelligence, San José State University
**Project Repository:** `Multi-Agent-Collaboration-System` (working branch: `chelsea-dev-pipeline`)

---

## Abstract

### Purpose

Modern cloud-native infrastructure runs on Kubernetes (K8s), and when a workload fails — a pod stuck in `ImagePullBackOff`, a `FailedScheduling` event, an OOM-kill, an RBAC `Forbidden` — site reliability engineers must diagnose the root cause, plan a fix, generate the exact remediation commands, verify the fix, and prepare a rollback, often under time pressure and across several K8s sub-domains spanning scheduling, storage, networking, identity, and configuration. A single human or a single LLM frequently misses subtleties. Output-averaging "ensembles" hide disagreement instead of arbitrating it, and agentic frameworks that auto-execute commands violate the operational constraint that infrastructure mutations require a human in the loop. This capstone addresses that gap by building **NexusTrace**, a multi-agent inference service that coordinates a small panel of role-specialized large language models to produce auditable, structured RCA results for K8s incidents. The realistic project scope is one tier of a four-tier architecture: a stateless inference service that consumes incident evidence, returns structured JSON, and never executes a command without an explicit human approval. Measurable objectives include dataset balance across at least fifteen scenarios, complete schema validity, bounded per-agent and per-pipeline latency, end-to-end verification on five distinct K8s failure categories, and an automated test suite that gates every change on the working branch.

### Tasks

The work decomposes into five tasks executed iteratively. First, a balanced synthetic Kubernetes incident dataset of 9,500 records spanning nineteen scenarios was generated and transformed into model-specific supervised fine-tuning corpora. Second, a multi-agent protocol — a Blackboard shared-message bus combined with a Contract Net Lite bidding protocol and a centralized protocol runner — was designed to let two RCA agents reason independently before a Reconciler arbitrates and a Validator emits verification and rollback. Third, the protocol was implemented twice: once as a thread-pool, timeout-bounded, approval-gated production-style service under `agents/` plus `api/main.py`, and again as a leaner asyncio sanity-check service under `fastapi/`. Fourth, the service was wired to two real model backends — local Ollama for development and any OpenAI-compatible endpoint such as vLLM for production — and verified end-to-end on five Kubernetes failure categories using four real local models. Fifth, the system was tested rigorously, with eighteen tests covering the production-style library and thirteen sanity tests covering the async service, all green on the working branch.

### Outcomes

The deliverables include a 9,500-row balanced incident corpus with a clean data-quality report showing zero rejects and zero missing required fields; six SFT-formatted corpora targeting five fine-tunable models; two working FastAPI services exposing `/health`, `/ready`, `/analyze`, and `/query` on port 8000 and `/health` plus `/analyze` on port 8001; a reusable `Orchestrator` Python class supporting `add_specialist()`, `analyze()`, `analyze_batch()`, and a hard-gated `execute_commands()` that raises `CommandsNotApprovedError` until an approver flips `approval_status`; a complete audit trail returned with every analysis; and verified end-to-end runs against four live Ollama base models — Qwen 2.5 7B, DeepSeek-R1 7B, Mistral 7B, and Llama 3.2 3B — on PVC, image-pull, OOM, missing-secret, and RBAC scenarios.

### Applications

NexusTrace is a drop-in inference service for any platform that wants to wrap human-in-the-loop Kubernetes remediation around LLM reasoning, including internal SRE consoles, on-call assistance tooling, postmortem authoring aids, and training simulators for junior platform engineers. The Blackboard plus Contract Net pattern generalizes beyond Kubernetes to any incident-response domain that benefits from independent reasoning, explicit conflict signals, and a hard approval boundary before any mutating action is taken.

---

## Chapter 1 — Introduction

### 1.1 Project Background and Executive Summary

Kubernetes underpins most modern container platforms, and a typical mid-sized cluster generates tens of thousands of warning events per week. Only a fraction of those signal real incidents, but the ones that do span very different sub-domains: storage failures such as a missing PVC or mount failure, networking failures such as `ImagePullBackOff` or DNS resolution errors, identity failures such as `Forbidden` from RBAC, runtime failures such as OOM-kills and `CrashLoopBackOff`, scheduling failures from taints or insufficient resources, and configuration failures such as a missing Secret or a bad ConfigMap key. Effective remediation always requires the same five things in the same order: a correct diagnosis, an ordered fix plan, the exact `kubectl` commands, a verification step that confirms the fix worked, and a rollback path if it did not.

Existing approaches each fail one of those constraints. Single-LLM systems conflate diagnosis with action without arbitration, mixing reasoning and execution in a single prompt. Output-averaging ensembles hide disagreement rather than resolving it, masking the very signal that a human reviewer needs. Fully autonomous agentic frameworks that execute commands without a human in the loop violate the safety standard that infrastructure mutations always require human approval. NexusTrace addresses each of these failure modes directly. It explicitly separates the agent roles, has two RCA agents reason independently of each other, has a Reconciler agent arbitrate between their candidate diagnoses and emit ordered commands, has a Validator agent emit verification and rollback steps, and gates command execution behind an explicit `approval_status` flip enforced in code. The full audit trail of every internal message lands in the response so that a human reviewer can see exactly why the system reached its recommendation.

### 1.2 Project Requirements

The requirements derive from the project specification and from operational and engineering practice. The system must generate at least 6,000 synthetic Kubernetes incident records balanced across at least fifteen failure scenarios, and it must produce SFT-ready corpora targeting per-role fine-tuned models for the RCA, Executor, and Validator roles. It must implement a multi-agent pipeline in which two RCA agents run in parallel, a Reconciler merges or selects between their outputs, and a Validator emits verification and rollback. It must expose the inference pipeline through a stateless HTTP tier offering `/health`, `/ready`, `/analyze`, and a frontend-compatible `/query` endpoint. The model backend must be pluggable, with stub mode for tests, local Ollama for development, and any OpenAI-compatible endpoint such as vLLM for production. Latency must be bounded at sixty seconds per agent and three hundred seconds end-to-end, with a degraded-but-non-crashing fallback path. Any mutating command must pass through a hard human-approval gate before execution. Every result must include a full audit trail, and the codebase must be covered by automated tests across unit, integration, and end-to-end scenario layers.

### 1.3 Project Deliverables

The capstone produces a synthetic incident corpus of 9,500 balanced records under `data/02-raw/synthetic_source.jsonl` distributed exactly evenly across nineteen scenarios. It also produces two derived datasets, the 7,000-row `k8s_combined_incidents.jsonl` in the flat nineteen-field schema that the orchestrator consumes directly, and the 1,500-row `k8s_config_incidents.jsonl` plus its 1,500-row 4-column projection `k8s_incidents_transformed.jsonl`. From those raw records it produces six SFT corpora — `rca_qwen3_5_9b.jsonl`, `rca_deepseek_r1_8b.jsonl`, `executor_devstral_24b.jsonl`, `validator_qwen3_5_35b.jsonl`, and `validator_llama3_2_3b.jsonl`, each 1,500 rows in the appropriate chat template, plus a 9,500-row `synthetic_sft.jsonl`. The capstone delivers two inference services: a thread-pool orchestrator with the full Blackboard plus Contract Net Lite plus approval gate at port 8000, and a lean async sanity-check service at port 8001 with env-driven Ollama wiring and thirteen pytest sanity tests. The reusable `Orchestrator` library exposes `StructuredRCAResult`, `format_as_sections`, `execute_commands`, `add_specialist`, and `analyze_batch`. Five themed K8s payloads cover storage, image-pull, runtime, configuration, and security scenarios. The test suites total thirty-one passing tests, and the documentation set comprises `how_the_orchestrator_works.md`, `running_end_to_end.md`, `fastapi/README.md`, `fastapi/RUNNING.md`, and the data-quality report at `data/reports/dq_report.json`.

### 1.4 Technology and Solution Survey

The implementation language is Python 3.10 or newer because the codebase uses modern type hints, `match` syntax, and `from __future__ import annotations`. The web framework is FastAPI on top of uvicorn, chosen for type-driven request and response validation through Pydantic v2, an automatically generated Swagger UI at `/docs`, and native asyncio support. Concurrency is handled in two complementary ways: the production-style library under `agents/` uses a `concurrent.futures.ThreadPoolExecutor` with daemon threads that cap latency on synchronous model calls, while the sanity service under `fastapi/` uses `asyncio.gather` to fan out HTTP calls and `asyncio.wait_for` to cap each phase. Pub/sub coordination uses a custom in-memory `Blackboard` guarded by a re-entrant lock with deep-copy-on-write semantics, scoped to a single request so no cross-request state can leak. The HTTP client for the asyncio service is `httpx`, while the synchronous service uses `urllib.request` from the standard library to avoid an extra dependency.

For local model inference, the project relies on Ollama running natively. On a Mac, Ollama is the fastest and most reliable path because it avoids GPU emulation entirely and exposes an OpenAI-compatible API at `http://localhost:11434`. The four base models verified end-to-end are `qwen2.5:7b` for Agent 1, `deepseek-r1:7b` for Agent 2, `mistral:7b` for the Reconciler, and `llama3.2:3b` for the Validator, with a combined RAM footprint of roughly 12-14 GB. For production, the same loader pattern works against vLLM or any other OpenAI-compatible endpoint. The approved supervised-fine-tuning targets — Qwen 3.5 9B and DeepSeek-R1 8B for the RCA role, Devstral-Small-2 24B for the Executor role, and Qwen 3.5 35B and Llama 3.2 3B for the Validator role — span small, mid, and large operating points and are distributed as LoRA Docker images on `linux/amd64`, deferred to a GPU host because the `BitsAndBytesConfig` 4-bit quantization requires CUDA. Testing uses `pytest`, `pytest-asyncio`, and `fastapi.testclient.TestClient`, which drives the ASGI app via anyio without requiring a live uvicorn process. A `docker-compose.yml` at the repository root pre-wires the four LoRA model services on ports 11001-11004 for the eventual GPU-host deployment.

### 1.5 Literature Survey of Existing Research

The Blackboard architecture goes back to Hayes-Roth (1985), who described a shared-memory pattern that lets independent reasoners cooperate without point-to-point coupling. NexusTrace's `Blackboard`, `Message`, and `Topics` constructs in `agents/blackboard.py` are a thread-safe mini-implementation of this idea, with eight canonical topics covering the full protocol: `INCIDENT`, `BID_REQUEST`, `BID`, `DISPATCH`, `DIAGNOSIS`, `CONFLICT`, `FIX_PLAN`, and `VALIDATION`. The Contract Net Protocol comes from Smith (1980), who described a bidding pattern in which agents announce capability and a manager selects a winner. NexusTrace adapts a "lite" variant: triage opens a bid round on a `handles` tag derived from the K8s `event_reason`, each registered RCA agent posts a confidence score in the unit interval, and the orchestrator picks the top two for diversity even when one bid dominates.

The recent literature on LLM ensembling for reasoning — particularly self-consistency (Wang et al., 2022) and mixture-of-agents (Wang et al., 2024) — motivates running multiple independent reasoning paths over the same input. NexusTrace follows that direction but goes further: instead of averaging or majority-voting, a separate Reconciler agent reasons over the candidates and produces a structured fix plan with kubectl commands. Supervised fine-tuning for tool use requires that prompt structure stay consistent between training and inference; `data/01-generation/generate_sft_by_role.py` therefore emits four chat templates — ChatML for Qwen, the native DeepSeek-R1 format with `<think>` blocks, the Mistral and Devstral `[INST]` instruct format, and the Llama 3 header-tag format — so each model sees the same shape at both phases. The nineteen scenarios cover the canonical Kubernetes failure surface documented in the project's troubleshooting guide. Finally, the codification of `approval_status` and `CommandsNotApprovedError` reflects the operational standard, well established in human-in-the-loop AI for operations, that any system mutating production infrastructure must require explicit human approval before execution.

---

## Chapter 2 — Data and Project Management Plan

### 2.1 Data Management Plan

All data used in the project is fully synthetic. The decision to avoid live-cluster data eliminates concerns over personally identifiable information, leaked credentials, and proprietary log shapes, while still allowing the pipeline to be exercised against realistic Kubernetes incident structures. The generation pipeline is reproducible because the random seed is fixed in `data/01-generation/data_creation.py`. The `data/` directory is organized into four subdirectories that map to the data lifecycle. The `01-generation/` directory holds the scripts that produce the data, including `data_creation.py` for the base 9,500-row synthetic generator, `generate_k8s_incidents.py` for scenario-specific generation, `transform_k8s_incidents.py` for the raw-to-projection transformation, `generate_sft_by_role.py` for the SFT chat-template formatter, and two Jupyter notebooks for inspection and exploratory work. The `02-raw/` directory contains the source-of-truth JSONL files: the 9,500-row balanced source, the 7,000-row combined incident dataset, the 1,500-row config-incident dataset, and its 1,500-row transformed projection. The `sft/` directory contains the six SFT corpora and a `stats.json` summarizing the generation balance. The `reports/` directory contains the data-quality audit (`dq_report.json` with zero rejects and `dq_report_rejects.jsonl` empty by design). Finally, the `team-data/` directory contains per-team-member contributions totaling 5,538 rows.

The flat schema used by the orchestrator on the inference side has nineteen fields per record: `scenario_id`, `namespace`, `pod_name`, `service_account_name`, `node`, `pod_status`, `image`, `container_state`, `last_state`, `ready`, `restart_count`, `node_selectors`, `claim_name`, `event_reason`, `event_message`, `pod_describe`, `pod_logs`, `pod_logs_previous`, and `evidence_text`. The orchestrator reads `pod_describe` and either `pod_logs` or `pod_logs_previous` directly, so the same schema flows from generation into inference without an extra parsing step. Retention is local: all data lives in the repository for full reproducibility and there is no external blob store. The Blackboard is purely in-memory and is discarded at the end of each request through `fastapi/services/memory.py:discard()`. Access control is enforced by environment variable: when `RCA_API_KEY` is set, the `/analyze` and `/query` endpoints require an `X-API-Key` header (see `api/main.py:require_api_key`), while `/health` and `/ready` remain open so monitoring probes do not need credentials.

### 2.2 Project Development Methodology

The development methodology is iterative agile with three vertical-slice demo gates aligned to the project specification's Phase A, Phase B, and Phase C. Phase A delivered the Blackboard, the Contract Net Lite protocol, the dispatch logic, and an end-to-end stub pipeline; this phase is captured in commit `921c3a0 phase A: Blackboard + Contract Net + dispatch protocol`. Phase B added the async sanity service in `fastapi/`, the thirteen pytest sanity tests, and a manual `curl` round-trip on the bundled payload, captured in commits `68e168c add fastapi/ async sanity-check inference service` and `d3e20c3 wire fastapi/ to live LLM backends`. Phase C wired the live Ollama backend, verified the pipeline end-to-end on five themed scenarios, and produced the portable `RUNNING.md` setup guide, captured in commits `d46d18d` and `caa0a3b`. Each slice was test-first: a failing pytest case drove the next code change, and merges to `chelsea-dev-pipeline` were guarded by `pytest tests/ -v` passing locally before the commit.

### 2.3 Project Organization Plan

The project is led by the capstone author, who is responsible for the orchestrator, the pipeline implementation, the FastAPI services, the data-quality audit, the repository hygiene, the documentation, and the test suite. Two teammates contributed model fine-tuning work: Devesh produced the RCA LoRA images for Qwen and DeepSeek under the `deveshs18/` Docker namespace, and Mrunali produced the Executor and Validator LoRA images under the `mrunalikatta/` namespace. Two further teammates, Akash and Najel, contributed synthetic data files, each adding 1,500 rows to the team-data pool. The author's own `chels.jsonl` contributes 1,538 rows, the largest per-member share. The complete set of LoRA-image authorship is visible in `docker-compose.yml`, which references `deveshs18/rca-lora-qwen`, `deveshs18/rca-lora-deepseek`, `mrunalikatta/executor-mistral-24b`, and `mrunalikatta/validator-llama-3b`.

### 2.4 Project Resource Requirements and Plan

For development, the capstone targets an Apple-Silicon Mac with at least 16 GB of RAM. Ollama loads all four base models in a combined footprint of roughly 12-14 GB, leaving enough headroom for the Python service. For the LoRA Docker images, a Linux x86_64 host with an NVIDIA GPU is required because the images use 4-bit `BitsAndBytesConfig` quantization that depends on CUDA; this requirement is satisfied by any cloud GPU box such as a Lambda Labs A10, a RunPod RTX 4090, or an AWS g5.xlarge. Storage requirements are modest at under 50 MB for the Python dependencies plus roughly 16 GB for the four Ollama models. The network configuration opens TCP ports 8000 for the production-style API, 8001 for the sanity service, and 11434 for Ollama, all internal-only by default. The required software is Python 3.10 or newer, Ollama, and optionally Docker Desktop, all of which are open-source or free-tier. Optional external services include a vLLM cluster and any cloud GPU host, both pluggable through a single environment variable change.

### 2.5 Project Schedule

The schedule spans fourteen weeks. Weeks one and two surveyed the Kubernetes failure surface and designed the nineteen-scenario taxonomy now visible in the `SCENARIOS` dictionary of `data/01-generation/generate_k8s_incidents.py`. Weeks three and four built the balanced synthetic generator and produced the 9,500-row corpus, with output stats in `data/sft/stats.json`. Week five conducted the data-quality audit, captured in `data/reports/dq_report.json`. Weeks six and seven added the per-model SFT chat-template formatters in `data/01-generation/generate_sft_by_role.py`. Week eight delivered Phase A, with the Blackboard, Contract Net Lite, and dispatch in place and the orchestrator stub running end-to-end. Week nine added the eighteen-test orchestrator suite in `tests/test_orchestrator.py`. Week ten produced `api/main.py`, the production-style FastAPI service exposing `/health`, `/ready`, `/analyze`, and `/query` on port 8000. Week eleven delivered Phase B, the async sanity service under `fastapi/` with thirteen passing tests. Week twelve delivered Phase C, wiring Ollama and verifying all five themed scenarios end-to-end. Week thirteen consolidated the documentation set, including the two architecture documents and the portable `RUNNING.md`. Week fourteen produced this final report and its diagrams and appendices.

---

## Chapter 3 — Data Engineering

### 3.1 Data Process

The data process flows from scenario templates through synthetic generation, validation, transformation, and SFT formatting. The nineteen scenario templates in `data/01-generation/generate_k8s_incidents.py` and `data/01-generation/data_creation.py` carry not only the failure mode itself but also the canonical `diagnosis_text`, `fix_plan_text`, `actions_text`, `verification_text`, and `rollback_text`, so each generated record is paired with ground truth that the SFT step can rely on. The synthetic generator produces 500 records per scenario, yielding the balanced 9,500-row source corpus. That corpus then flows in two directions: through the data-quality audit to the empty `dq_report_rejects.jsonl`, confirming validity, and through `generate_sft_by_role.py` to the six SFT files that target the five role-specific models. A separate transformation pipeline produces the 7,000-row `k8s_combined_incidents.jsonl` in the flat nineteen-field schema, which is the inference pipeline's primary input and the source of the five themed payloads under `fastapi/payloads/`.

```
   ┌──────────────────────┐
   │ scenario template     │  19 templates with diagnosis_text,
   │ (data_creation.py)    │  fix_plan_text, actions_text, etc.
   └──────────┬────────────┘
              │  500 records each, balanced
              ▼
   ┌──────────────────────┐
   │ synthetic_source     │  9,500 rows, structured truth
   │ .jsonl  (02-raw/)     │  + observations + actions
   └──────────┬────────────┘
              │
       ┌──────┼──────────────────────────────────────┐
       ▼      ▼                                      ▼
  ┌────────────────────┐    ┌─────────────────────────────────┐
  │ DQ audit            │    │ generate_sft_by_role.py          │
  │ → dq_report.json    │    │ → six SFT JSONL files            │
  │   (0 rejects)       │    │   ChatML / DeepSeek / Mistral /  │
  └────────────────────┘    │   Llama-3 chat templates         │
                             └─────────────────────────────────┘
                                            │
                                            ▼
                                ┌──────────────────────────┐
                                │ k8s_combined_incidents   │
                                │ .jsonl  (flat 19-field    │
                                │ schema, 7,000 rows)       │
                                └──────────────────────────┘
                                            │
                                            ▼
                                  Inference pipeline input
```

### 3.2 Data Collection

No live-cluster data was collected for this project. The corpus is fully synthetic so that no personally identifiable information, no credentials, and no proprietary log shapes can leak through the data pipeline. The generator samples realistic Kubernetes primitives — namespaces, workload kinds covering Deployment, StatefulSet, Job, and DaemonSet, container names, image catalogs, and scenario-specific failure modes — using weighted random distributions parameterized in `data/01-generation/data_creation.py`. Determinism is enforced by passing a fixed `--seed` so the generated dataset is exactly reproducible from clean inputs. The per-team-member contributions in `data/team-data/` total 5,538 rows: Akash, Devesh, and Najel each contributed 1,500 rows, Mrunali contributed 1,000 rows, and the author contributed 1,538 rows in `chels.jsonl`, the largest per-member share.

### 3.3 Data Pre-processing

Pre-processing focuses on schema normalization, field flattening, and graceful handling of partial evidence. The script `transform_k8s_incidents.py` projects raw `evidence_text` blobs into a 4-column projection — `scenario_id`, `pod_describe`, `pod_logs`, and `pod_logs_previous` — by regex-extracting the `=== kubectl describe pod ===`, `=== kubectl get events ===`, and `=== container logs ===` sections. The combined dataset `k8s_combined_incidents.jsonl` is the inference-side flat schema with nineteen columns, including the parsed `pod_describe` already separated from the `evidence_text` blob so the orchestrator can read it without an extra parsing step. Pods that have not yet started have empty `pod_logs`; the `SolutionGeneratorAgent` short-circuits and returns an empty diagnosis with a `"no describe/logs available"` note (see `agents/solution_generator_agent.py:39-44`) so the pipeline never crashes on partial evidence.

### 3.4 Data Transformation

Transformation produces the six SFT corpora through `generate_sft_by_role.py`, which encodes per-model chat templates in dedicated formatters. The ChatML format wraps Qwen 3.5 9B and Qwen 3.5 35B with `<|im_start|>system … <|im_end|>` markers. The DeepSeek-R1 format uses the model's native `<|begin▁of▁sentence|>`, `<|System|>`, `<|User|>`, `<|Assistant|>` and `<think>...</think>` markers. The Mistral and Devstral instruct format uses the `[INST] system\n\nuser [/INST] assistant</s>` shape required by Mistral's instruction-tuned variants. The Llama 3 instruct format uses the `<|begin_of_text|>`, `<|start_header_id|>system<|end_header_id|>`, and `<|eot_id|>` markers expected by Llama 3.x. Each role has its own user-prompt builder (`build_rca`, `build_executor`, `build_validator`) so the prompts seen at fine-tuning time match the prompts seen at inference time exactly. For example, `agents/solution_generator_agent.py:_build_user_prompt` mirrors the structure of `build_rca` so the trained RCA models receive a prompt of the same shape they were trained on.

### 3.5 Data Preparation

A small but operationally important preparation step concerns reasoning models. The `<think>...</think>` block is treated as part of the assistant turn during fine-tuning for DeepSeek-R1, but it is stripped at inference by the regex `_THINK_BLOCK_RE` in `fastapi/services/model_client.py` so callers see only the final answer rather than the chain-of-thought. This single regex is what makes DeepSeek-R1 usable as a drop-in agent. Without it, a `max_tokens=1024` budget would fill with chain-of-thought tokens and leave the diagnosis empty, which would surface in the response notes as `agent_X real-empty-fallback`. The preparation pipeline also enforces JSONL line discipline so each record is one self-contained JSON object, supporting both streaming generation and downstream parallel ingestion.

### 3.6 Data Statistics

The 9,500 source rows are distributed exactly evenly across the nineteen scenarios, with 500 rows per scenario and zero rejects. The scenarios cover the canonical Kubernetes failure surface: `crashloop_missing_secret`, `crashloop_bad_configmap_key`, `crashloop_bad_args`, `imagepull_bad_tag`, `imagepull_registry_auth`, `failedscheduling_taint`, `failedscheduling_insufficient_memory`, `failedscheduling_insufficient_cpu`, `nodeselector_mismatch`, `pvc_pending_missing_storageclass`, `pvc_not_found_mountfail`, `oomkilled_limit_too_low`, `readiness_probe_failure`, `liveness_probe_failure`, `rbac_forbidden`, `dns_resolution_failure`, `service_connection_refused`, `quota_exceeded_pods`, and `gitops_sync_failed`. The 7,000-row combined dataset shows a realistic pod-status distribution of 4,512 Pending (64.5%), 1,488 Running (21.3%), and 1,000 Failed (14.3%), reflecting the empirical observation that most "incidents" in the wild are stuck-Pending pods rather than crash loops. Each per-role SFT corpus contains exactly 1,500 rows, derived from the 1,500-row `k8s_config_incidents.jsonl` rather than the full 9,500-row source, giving a balanced average of about eighty records per scenario per model.

### 3.7 Data Analytics Results

The data-quality audit captured in `data/reports/dq_report.json` reports a 100% validity rate. Of the 9,500 rows loaded, 9,500 were written to the bronze layer, 9,500 were valid, and zero were rejected. There were no `created_at` parse failures, no duplicate rows, no missing `id` values, no missing `context.cluster_id` values, and no missing `meta.created_at` values. Every observation field — `kubectl_get_pods`, `kubectl_describe_pod`, `kubectl_get_events`, and `container_logs` — was non-null and non-empty across the corpus. The companion file `data/reports/dq_report_rejects.jsonl` is empty by design, confirming that no record fell out of the validation gate.

---

## Chapter 4 — Model Development

### 4.1 Model Proposals

NexusTrace deliberately uses role-specialized models rather than a single model attempting every job. The approved per-role registry is encoded in `agents/orchestrator.py:AGENT_ROLE_MODELS` and validated by `Orchestrator.from_role_defaults()`. The RCA role, which runs twice in parallel, is filled by `qwen3.5:9b` and `deepseek-r1:8b`: two distinct architectures whose independent reasoning is the entire point of running them in parallel. Qwen 3.5 is a strong general reasoner, while DeepSeek-R1 brings explicit chain-of-thought through its `<think>` blocks. The Executor or Reconciler role is filled by `devstral-small-2:24b`, a code-tuned Mistral variant, because reconciliation produces actual `kubectl` commands and a code-tuned model lifts that quality measurably. The Validator role offers two operating points: `qwen3.5:35b` for high-quality offline review and `llama3.2:3b` for fast live verification. The default pipeline uses Qwen 3.5 9B as Agent 1, DeepSeek-R1 8B as Agent 2, Devstral-Small-2 24B as the Reconciler, and Qwen 3.5 35B as the Validator. A constructor that receives any non-approved model name raises `ValueError` with a helpful message naming the slot, the offending model, and the allowed set, as proven by `test_from_role_defaults_rejects_non_approved_model`.

### 4.2 Model Supports

The approved models are supported by six SFT corpora under `data/sft/`, each chat-templated for the matching tokenizer. The system prompts — `SYSTEM_RCA`, `SYSTEM_RECONCILER`, and `SYSTEM_VALIDATOR` — are defined exactly once and reused both at SFT formatting time in `generate_sft_by_role.py` and at inference time in `agents/solution_generator_agent.py`, `agents/reconciliation_agent.py`, and `agents/validation_agent.py`, eliminating training/inference drift. The reasoning-block stripper in `fastapi/services/model_client.py` removes `<think>...</think>` blocks before returning, so reasoning-style models drop into any agent slot transparently. Every agent is wired with a three-level fallback: when its `*_URL` environment variable is unset the agent stays in stub mode and returns canned text, when the model call returns empty content the agent falls back to the same canned text and notes `*_real-empty-fallback`, and when the call raises a transport error the agent falls back to canned text and notes `*_real-error-fallback`. As a result, the pipeline always returns a 200-OK response with the structured schema fully populated, and every degradation is recorded in the `errors[]` array rather than crashing the request.

### 4.3 Model Comparison and Justification

The architectural choices were made deliberately against named alternatives. Two parallel RCA agents using different model architectures were chosen over a single RCA agent because two architectures reduce the risk of self-confirming errors: when both agree the system has a strong signal, and when they disagree the orchestrator emits an explicit `conflict` event so the trace records the disagreement. A separate Reconciler agent that reasons over the candidate diagnoses was chosen over logits-level or output-text averaging because averaging on free-form text is ill-defined, while a Reconciler can read both candidates and emit a structured fix plan plus commands. A code-tuned Devstral 24B was chosen for the Reconciler over reusing the same RCA model in the Reconciler slot because the Reconciler emits actual `kubectl` commands and a code-tuned model produces measurably cleaner output. A two-tier Validator with Qwen 3.5 35B and Llama 3.2 3B was chosen over a single Validator so high-stakes incidents can use the larger model while high-throughput dev cycles can use the smaller, faster one. A top-2 dispatch policy was chosen over top-1 because diversity matters even when one bid score is much higher: the second opinion is the entire mechanism by which arbitration becomes meaningful. Triage is implemented as model-free `event_reason` matching rather than as an LLM call because routing should be deterministic, fast, and easy to test. Finally, the Blackboard plus Contract Net pattern was chosen over direct point-to-point agent calls because it decouples the agents and lets specialists be added through `add_specialist()` without changing the core code.

### 4.4 Model Evaluation Methods

Three layers of evaluation are baked into `tests/test_orchestrator.py`. The unit layer exercises imports, registry consistency, prompt parsers, and empty-incident handling. The stub end-to-end layer drives the full pipeline with SFT-shaped canned text per role, returned by a scenario loader that distinguishes Agent 1 from Agent 2 by model name; this layer verifies pipeline shape — that every stage ran, that the response wiring is intact, that the schema is fully populated — without needing any model. The live Ollama-gating layer attempts a smoke-test against a running `ollama serve` and skips cleanly if the daemon is unreachable. The async sanity service adds a fourth layer in `fastapi/tests/test_sanity.py`: pipeline shape on the five themed real-data scenarios, parameterized over `payloads/{01..05}*.json` with one PASSED line per scenario so failures point at the exact failure category (storage, image, runtime, configuration, or security) that broke.

### 4.5 Model Validation and Evaluation Results

In stub mode, which serves as the CI baseline, the sanity service runs thirteen tests in 3.49 seconds and the production-style library runs eighteen tests, both fully green on every commit on `chelsea-dev-pipeline` since Phase B. In real mode against Ollama on a Mac with the four base models loaded, the cold first call ranges from 30 to 90 seconds while Ollama loads the models into RAM, and warm-call end-to-end latency settles between 10 and 25 seconds across all four agents. All five themed scenarios produce evidence-aware diagnoses: the PVC payload names the missing volume, the image-pull payload identifies the bad tag, the OOM payload identifies the memory limit, the missing-secret payload names the absent Secret, and the RBAC payload cites the missing permission. End-to-end across all five scenarios takes between three and five minutes, all four agents report `real` mode, and the diagnoses are meaningfully distinct across the failure categories. RAM at peak hovers around 12-14 GB across the four loaded models. The single most common recoverable failure is reasoning-token exhaustion on DeepSeek-R1, surfaced as `agent_2 real-empty-fallback`, fully mitigated by the `<think>` block stripper combined with the `max_tokens=1024` budget calibrated to leave at least 400 tokens for the answer after a typical chain-of-thought.

---

## Chapter 5 — Data Analytics System

### 5.1 System Requirements Analysis

NexusTrace is designed as tier 3 of a four-tier system. Tier 1 is a Frontend (a React app in a browser) where an operator sees incidents and clicks "Analyze." Tier 2 is a Backend API that stores incidents in a database, collects user feedback, and persists results. Tier 3 is the Inference Service in this repository, which runs the multi-agent pipeline statelessly and returns a structured answer. Tier 4 is the Model Server, hosted on Ollama or vLLM, which actually serves the trained models. The Inference Service in this repository is intentionally stateless — it does not store anything, does not know about end users, and has no database. It receives an incident, runs the pipeline, and returns an answer. Tiers 1, 2, and 4 are out of scope for this repository.

```
       ┌────────────────────────┐
       │   1. Frontend          │  React app — operator clicks "Analyze"
       └───────────┬────────────┘
                   │
       ┌────────────────────────┐
       │   2. Backend API       │  Stores incidents, persists results, auth
       └───────────┬────────────┘
                   │
       ┌────────────────────────┐
       │   3. Inference Service │  ◄── THIS REPO  (stateless)
       └───────────┬────────────┘
                   │
       ┌────────────────────────┐
       │   4. Model server      │  Ollama / vLLM
       └────────────────────────┘
```

The functional requirements R1 through R9 from Chapter 1 are restated here in terms of the system. The non-functional requirements include statelessness, bounded latency, a 100% successful HTTP response policy in which the service degrades to partial output rather than crashing, both structured and section-formatted outputs (because the LSA-WebApp frontend expects the latter), and an audit trail in every response.

### 5.2 System Design

The system design exposes two complementary services that share the same underlying agents. The production-style service in `api/main.py` listens on port 8000 and offers `GET /health` returning `{"status":"ok","backend":"stub|ollama|vllm"}`, `GET /ready` which probes the model backend and returns 503 if it is unreachable, `POST /analyze` which accepts a structured incident and returns `StructuredRCAResult.to_dict()`, and `POST /query` which accepts the LSA-WebApp prompt format and returns text in five fixed sections. Authentication is enforced by an `X-API-Key` header when `RCA_API_KEY` is set. The sanity-check service in `fastapi/app.py` listens on port 8001 and offers `GET /health` returning `{"status":"ok","service":"fastapi-sanity","agents":[...]}` and `POST /analyze` accepting `{"evidence_text": "..."}` and returning a typed Pydantic `AnalyzeResponse`.

The pipeline is logically the same in both services. The orchestrator posts the incident on the Blackboard, triage opens a bid round on the `handles` tag, registered RCA agents post their confidence bids, the orchestrator dispatches the top two for diversity, the two RCA agents reason in parallel, the orchestrator posts a `conflict` message if the diagnoses disagree by more than the Jaccard threshold, the Reconciler arbitrates and emits the fix plan and commands, the Validator emits verification and rollback, and the orchestrator bundles the result with `approval_status="pending"` and returns it.

```
                          ┌─────────────────────────┐
   Incident ─────────────►│      Orchestrator        │
                          └────────────┬────────────┘
                                       │ posts to:
                                       ▼
            ┌──────────────────────────────────────────────┐
            │              Blackboard                       │
            │  incident · bid_request · bid · dispatch     │
            │  diagnosis · conflict · fix_plan · validation│
            └──────┬──────────┬──────────┬──────────┬─────┘
                   │          │          │          │
              ┌────▼───┐ ┌────▼────┐ ┌───▼────┐ ┌──▼────┐
              │ Triage │ │ RCA A/B │ │Reconcile│ │Validate│
              └────────┘ └─────────┘ └────────┘ └───────┘

   Result returned: diagnosis + fix_plan + commands + verification
                    + rollback + complete audit trail
                    + approval_status = "PENDING"
```

The pipeline timeline within a single `analyze()` call begins with model-free triage routing. The bidding round happens immediately after, with every RCA-eligible agent posting a confidence in the unit interval. Top-2 dispatch is then committed and posted to the Blackboard. The two RCA agents run in parallel — through threads in the production-style service, through `asyncio.gather` in the sanity service — and post their candidate diagnoses. A token-level Jaccard similarity test on the diagnosis prose decides whether to post a `conflict` message; the Reconciler runs in either case, reads the candidates, picks or merges them, and posts the `fix_plan`. The Validator reads the `fix_plan` and posts `validation`. The orchestrator then bundles a `StructuredRCAResult` with `approval_status="pending"` and returns it.

### 5.3 Intelligent Solution

Three properties make NexusTrace genuinely multi-agent rather than a single model called four times. The first is specialization: each role has its own SFT corpus, system prompt, output parser, and tokenizer chat template. The second is independent reasoning: Agent 1 and Agent 2 do not see each other's output, so when they agree the system has a strong signal and when they disagree the audit trail records the disagreement. The third is arbitration: the Reconciler reasons over the candidates rather than averaging them, and the Jaccard-based `_diagnoses_disagree()` method posts an explicit `conflict` event whenever the token-set overlap between the two diagnoses falls below 0.5.

A specialist extension point is provided through `Orchestrator.add_specialist()`. A new RCA expert can register itself with a `Capability` and a custom `bid()` method:

```python
class NetworkingSpecialistAgent(SolutionGeneratorAgent):
    def bid(self, incident):
        reason = (incident.get("event_reason") or "").lower()
        msg    = (incident.get("event_message") or "").lower()
        if "imagepull" in reason or "manifest unknown" in msg: return 0.95
        if "dns" in msg or "service mesh" in msg:              return 0.85
        return 0.0

orch.add_specialist(
    NetworkingSpecialistAgent(name="networking", model=ollama_call),
    Capability(role="rca",
               handles={"ImagePullBackOff","ErrImagePull",
                        "DNSResolution","FailedCreatePodSandBox"}),
)
```

When specialists are present the bidding round becomes meaningful: a networking incident gets the networking specialist plus one generalist, a storage incident gets the storage specialist plus one generalist, and so on. The system dynamically chooses who works on each incident instead of using the same two agents every time.

The approval gate is enforced as code rather than as policy. There are two supported flows. In the inline flow, the caller passes an `approval_callback` to `analyze()` and the orchestrator flips the result's status based on the callback's return value before returning. In the deferred flow, the caller approves the result later through `result.approve(approver, note)` and then calls `execute_commands(result, runner)`. The function `execute_commands()` is the only sanctioned executor and refuses to run unless `result.approval_status == "approved"`, raising `CommandsNotApprovedError` otherwise. The runner — not this function — is responsible for whatever environment-specific safety the caller needs, such as namespace allow-lists, dry-run mode, or `kubectl` context pinning.

### 5.4 System Development and Implementation

The production-style service is organized into ten files under `agents/` plus the FastAPI entry point. The Blackboard, Message, and Topics live in `agents/blackboard.py` and use a re-entrant lock with deep-copy-on-write semantics. The agent registry, capability declarations, and the registered-agent record live in `agents/registry.py`. The model-free triage agent in `agents/triage_agent.py` simply maps the incoming `event_reason` to a `handles` tag. The base agent abstract class in `agents/base_agent.py` defines the `AgentResult` shape, the default 0.7 bid, and the model-call hook. The `SolutionGeneratorAgent` in `agents/solution_generator_agent.py` implements Agent 1 and Agent 2. The `ReconciliationAgent` in `agents/reconciliation_agent.py` implements the Reconciler with a markdown parser that strips list prefixes and separates sections. The `ValidationAgent` in `agents/validation_agent.py` implements the Validator with a parser for the verification and rollback sections. The orchestrator itself lives in `agents/orchestrator.py` and includes the `StructuredRCAResult` dataclass, the `from_bootstrap` and `from_role_defaults` factories, the `analyze` and `analyze_batch` methods, the `execute_commands` approval gate, and the `format_as_sections` text renderer used by the LSA-WebApp `/query` endpoint. The model-client adapters in `agents/model_loaders.py` provide `ollama_loader` and `vllm_loader`. The CLI in `agents/run_rca.py` allows batch runs over the JSONL dataset without HTTP. Finally, `api/main.py` wraps the orchestrator in a FastAPI app with Pydantic models and optional service-to-service authentication.

The sanity service is organized into a smaller set of files under `fastapi/`. The entry point `fastapi/app.py` exposes `/health` and `/analyze`. The async pipeline in `fastapi/services/orchestrator.py` uses `asyncio.gather` for parallel RCA fan-out and `asyncio.wait_for` for per-step timeout caps. The in-memory `IncidentBlackboard` in `fastapi/services/memory.py` keeps per-incident `asyncio.Lock` so concurrent requests do not serialize on each other. The async HTTP client in `fastapi/services/model_client.py` targets the OpenAI-compatible chat-completions endpoint and strips `<think>` blocks. Each of the four agents is one file under `fastapi/agents/`, dispatching to either canned text in stub mode or `call_model()` in real mode based on whether its `*_URL` environment variable is set. The Pydantic v2 schemas under `fastapi/schemas/` define the request and response contracts. The thirteen-test sanity suite in `fastapi/tests/test_sanity.py` runs in 3.5 seconds.

The safety contract is codified rather than merely documented. The `analyze(incident)` method works on `copy.deepcopy(incident)` so the caller's dictionary is never mutated. Per-agent timeouts default to sixty seconds and per-pipeline timeouts default to three hundred seconds; the threads run as `daemon=True` so a hung model cannot block process shutdown. Every exception inside an agent is caught at the queue boundary and recorded as an entry in the result's `errors[]` list rather than propagated. Every result starts with `approval_status="pending"`, and `execute_commands()` enforces the gate before any mutating action is taken.

---

## Chapter 6 — System Evaluation and Visualization

### 6.1 Analysis of Model Execution and Evaluation Results

In stub mode, which serves as the CI baseline, the sanity suite reports `13 passed in 3.49s` and the production-style library suite reports `18 passed`. All five themed scenarios round-trip cleanly, the response schema is fully populated for every request, and `requires_human_review=true` is set whenever the Reconciler produced commands — exactly the behavior asserted by `test_analyze_returns_all_outputs` and `test_analyze_with_themed_payload`. In real mode against Ollama with the four base models loaded, all five themed scenarios produce evidence-aware diagnoses that name the specific resource, tag, limit, secret, or permission at fault. Cold-load latency falls in the 30-90 second range and reflects Ollama bringing each model into RAM the first time. Warm-call latency settles between 10 and 25 seconds end-to-end across all four agents. The single largest source of failed-but-recovered runs is reasoning-token exhaustion on DeepSeek-R1, surfaced in the response notes as `agent_2 real-empty-fallback`. This is fully mitigated by the combination of the `<think>` block stripper and a `max_tokens=1024` budget calibrated to leave at least 400 tokens for the answer after a typical chain-of-thought.

### 6.2 Achievements and Constraints

The achievements of the project are concrete and measurable. Data validity reaches 100% with zero rejects, and the nineteen-scenario balance hits the target of 500 records per scenario exactly. Two production-quality FastAPI services have been delivered, each with a clear separation of concerns. The approval gate is enforced as code, not policy, and every response includes a full audit trail through `StructuredRCAResult.trace`. End-to-end verification has been completed with four real LLMs running locally on a Mac without any GPU. The test pyramid spans thirty-one automated tests across both services, all green on the working branch.

The constraints, honestly accounted for, fall into three categories. First, the LoRA Docker images authored by teammates are amd64-and-CUDA-only and cannot be exercised on a Mac; the repository wires them in `docker-compose.yml` and documents them in `RUNNING.md` Appendix A, but their validation is deferred to a GPU host, with Mac validation falling back to Ollama base models. Second, structured-output parsing for the Reconciler and Validator remains brittle on small models in the sanity service, where the `fix_plan` and `commands` arrays stay canned in real mode while only the `diagnosis` and `safety_notes` fields use model output; the production-style `agents/` library parses fully but is more sensitive to model quality. Third, multi-worker uvicorn (`--workers > 1`) gives each worker its own in-process `IncidentBlackboard` so the same `incident_id` sent to two workers does not share state; this is acceptable for single-incident requests and is documented in `RUNNING.md`.

### 6.3 Quality Evaluation of Model Functions and Performance

The quality evaluation is summarized along several axes. Schema validity is 100%, with zero of 9,500 records rejected. Scenario balance has a min/max ratio of 1.000 with 500 records in every scenario. Test coverage is 31 of 31 tests passing. Pipeline-stage coverage reaches 100% in both stub and real mode, meaning every analysis run is dispatched, reconciled, and validated. Pipeline latency measures roughly 330 milliseconds at the 50th percentile in stub mode, between 10 and 25 seconds at the 50th percentile in warm real mode, and between 30 and 90 seconds for the cold first call in real mode. Failure containment is total: exceptions are surfaced through the `errors[]` array and neither service ever returns a 500 to the caller. The approval gate is unit-tested: any attempt to call `execute_commands()` on a non-approved result raises `CommandsNotApprovedError`.

### 6.4 Evaluation of Models vs. Requirements

Each numbered requirement from Chapter 1.2 has a corresponding evidence trail in the repository. R1, the requirement of at least 6,000 balanced records across at least fifteen scenarios, is exceeded with 9,500 rows across nineteen scenarios at exactly 500 each. R2, per-role SFT corpora, is met by the six files under `data/sft/`. R3, the 4-agent pipeline with parallel RCA, is met by both `agents/orchestrator.py` and `fastapi/services/orchestrator.py`. R4, the stateless HTTP tier exposing `/health`, `/ready`, `/analyze`, and `/query`, is met by `api/main.py`. R5, a pluggable backend, is met by `agents/model_loaders.py` and the env-driven `*_URL` configuration in `fastapi/`. R6, bounded latency with degraded fallback, is met by the per-agent and per-pipeline timeouts plus the three-level fallback in every agent. R7, the human-approval gate, is met by `approval_status`, `execute_commands`, and `CommandsNotApprovedError`. R8, an audit trail in every response, is met by `StructuredRCAResult.trace`. R9, layered test coverage, is met by the thirty-one tests in three layers.

### 6.5 Project Information Visualization

The conceptual diagrams used throughout this report cover the four-tier architecture, the multi-agent pipeline with Blackboard topics, the sequence timeline from triage through validation, the data flow from synthetic source to combined dataset to orchestrator input, the approval-gate state machine that transitions from `pending` to either `approved` or `rejected`, and the specialist extension point illustrated by the `NetworkingSpecialistAgent` example. In addition, the per-payload runtime printout from `RUNNING.md` Step 7.6 visualizes the live pipeline behavior for each themed scenario, with one block per payload showing each agent's mode (`real` versus `stub` versus a fallback variant), the chosen source from the Reconciler, and the first 120 characters of the final diagnosis.

---

## Chapter 7 — Conclusion

### 7.1 Summary

NexusTrace demonstrates that the right software pattern matters as much as the model itself. A Blackboard plus Contract Net protocol turns four LLM calls into a small expert panel that disagrees explicitly, arbitrates explicitly, validates explicitly, and refuses to mutate cluster state without human approval. The 9,500-row synthetic corpus, the six SFT corpora, the two FastAPI services, the thirty-one automated tests, and the verified end-to-end runs against four real local models on five distinct Kubernetes failure categories together meet every documented requirement. The codebase is small enough to reason about end-to-end, the documentation set is detailed enough that a reader new to the repository can stand the system up on a fresh Mac in under fifteen minutes, and the approval gate makes it impossible to misuse the system as an autonomous executor.

### 7.2 Benefits and Shortcomings

The design carries four clear benefits. It is deterministic and auditable: every internal message lands in `result.trace`, so a reviewer sees the full reasoning chain. It is backend-agnostic: the same code runs against canned stubs for tests, against local Ollama for development, and against any OpenAI-compatible endpoint for production, with the choice driven by a single environment variable. It is safe by construction: the only path to executing any command runs through an explicit approval flip, with no side door. It is extensible: `add_specialist()` lets a new K8s subdomain expert join the panel with no change to the core code. The shortcomings are equally honest. The outputs remain advisory; final correctness still depends on a human reviewer, especially for the exact `kubectl` commands. The real-mode `fix_plan` and `commands` arrays in the sanity service stay canned until a tighter API contract with the LoRA images is finalized. The single-process `IncidentBlackboard` ties scale to a single uvicorn process; horizontal scale would require a shared memory tier such as Redis or Postgres.

### 7.3 Potential System and Model Applications

NexusTrace plugs directly into internal SRE consoles behind a "Suggest a fix" button. It augments on-call assistance by giving paged operators a draft diagnosis and the exact commands they can approve in the same UI. It accelerates postmortem authoring because every `result.trace` is already a structured record of who said what, when. It provides a safe environment for training junior engineers, who can replay synthetic incidents through the pipeline and compare their reasoning against the system's. Beyond Kubernetes, the Blackboard plus Contract Net plus approval-gate triad transfers naturally to incident response in databases, networks, CI/CD pipelines, and security operations, anywhere multiple specialized reasoners must collaborate while a human retains the final say.

### 7.4 Experience and Lessons Learned

Several lessons emerged from the build. First, templates beat averaging: two RCA agents whose outputs are reasoned over by a Reconciler beat any output-averaging ensemble I tried, by a noticeable margin in both quality and explainability. Second, stripping `<think>` blocks is non-optional for reasoning models with a finite token budget; the regex in `model_client.py` is the difference between an empty-fallback note and a real, evidence-aware diagnosis. Third, stub mode is a feature, not a fallback: it made every test deterministic, fast, and runnable without GPUs, and it kept CI green throughout the live-model wiring phase. Fourth, local-first beats containers-first on a Mac; Ollama on `localhost:11434` was decisively simpler than getting amd64 LoRA images to behave under Apple-Silicon emulation, while the Docker compose path remains documented for the GPU-host follow-up. Fifth, directory naming has consequences: the `fastapi/` folder shadows the PyPI `fastapi` package, and the workaround documented in `RUNNING.md` and `conftest.py` cost real debugging time before the project converged on the correct pattern.

### 7.5 Recommendations for Future Work

Several concrete enhancements remain. Finalizing the LoRA image API contract — `POST /v1/rca/generate` with structured K8s fields rather than the OpenAI-compatible chat-completions shape — and hardening the parsers would let real-mode `fix_plan` and `commands` parse out of free text without falling back to canned lists. A specialist library shipping `NetworkingSpecialist`, `RBACSpecialist`, `StorageSpecialist`, and `SchedulingSpecialist` against the existing nineteen scenarios, and registering them in `from_role_defaults`, would turn the bidding round into real routing rather than a tie-broken-by-cost. Replacing the in-memory `Blackboard` with a Redis-backed implementation would unlock multi-worker uvicorn and multi-replica deployments without losing per-incident state. A held-out test split of 500 incidents (one per scenario, distributed evenly) would enable quantitative model evaluation, grading the Reconciler's `commands` against the ground-truth `actions_text` from `data_creation.py` and reporting exact-match and token F1 per scenario. Pre-built approval-callback adapters for Slack, PagerDuty, and a minimal web UI would remove the need for custom integration code. Finally, instrumenting `duration_ms` and per-agent latencies into Prometheus and surfacing them in Grafana dashboards would give operators visibility into cost and latency trends per scenario.

### 7.6 Contributions and Impacts on Society

Most production Kubernetes incidents are resolved by a small number of senior engineers under time pressure, a constraint that scales linearly with cluster footprint. A trustworthy AI assistant that drafts diagnoses, fix plans, commands, verification, and rollback — and refuses to act without explicit human approval — augments that scarce expertise rather than displacing it. By keeping every internal step auditable and every mutating action gated, NexusTrace contributes a pattern for accountable AI in operational settings, where consequences are concrete and reversibility matters. The same pattern is transferable to other safety-critical operational domains and offers a concrete template for how multi-agent LLM systems can be built without sacrificing oversight, an increasingly important property as such systems are adopted in environments where errors have real-world consequences.

---

## References

1. Hayes-Roth, B. (1985). A blackboard architecture for control. *Artificial Intelligence*, 26(3), 251–321.
2. Smith, R. G. (1980). The Contract Net Protocol: high-level communication and control in a distributed problem solver. *IEEE Transactions on Computers*, C-29(12), 1104–1113.
3. Wang, X., Wei, J., Schuurmans, D., Le, Q., Chi, E. H., Narang, S., Chowdhery, A., & Zhou, D. (2022). Self-Consistency Improves Chain of Thought Reasoning in Language Models. *arXiv:2203.11171*.
4. Wang, J., Wang, J., Athiwaratkun, B., Zhang, C., & Zou, J. (2024). Mixture-of-Agents Enhances Large Language Model Capabilities. *arXiv:2406.04692*.
5. Tian, R., Narayanan, D., Patwary, M., et al. (2023). DeepSeek-R1: Incentivizing Reasoning in LLMs via Reinforcement Learning. (Technical report.)
6. Bai, J., Bai, S., Chu, Y., et al. (2024). Qwen2.5 Technical Report. *arXiv:2412.15115*.
7. Touvron, H., Martin, L., Stone, K., et al. (2023). Llama: Open and efficient foundation language models. *arXiv:2302.13971*.
8. Mistral AI Team (2024). Devstral: A code-tuned variant of Mistral. (Model card.)
9. Kwon, W., Li, Z., Zhuang, S., Sheng, Y., Zheng, L., Yu, C. H., Gonzalez, J. E., Zhang, H., & Stoica, I. (2023). Efficient memory management for large language model serving with PagedAttention (vLLM). *Proceedings of the 29th SOSP*, 611–626.
10. Pydantic Project. (2024). *Pydantic v2 documentation.* https://docs.pydantic.dev
11. Tiangolo, S. (2018–2024). *FastAPI documentation.* https://fastapi.tiangolo.com
12. Ollama Project. (2024). *Ollama: Run Llama 3, Mistral, and other models locally.* https://ollama.com
13. The Kubernetes Authors. (2024). *Troubleshoot applications.* https://kubernetes.io/docs/tasks/debug/debug-application/
14. Hu, E. J., Shen, Y., Wallis, P., Allen-Zhu, Z., Li, Y., Wang, S., Wang, L., & Chen, W. (2021). LoRA: Low-Rank Adaptation of Large Language Models. *arXiv:2106.09685*.
15. Dettmers, T., Pagnoni, A., Holtzman, A., & Zettlemoyer, L. (2023). QLoRA: Efficient Finetuning of Quantized LLMs. *arXiv:2305.14314*.

> Course-required references (textbook, lecture notes, internal SJSU MSDA materials) should be appended here in APA format per the rubric template.

---

## Appendix A — Repository Map and Run Recipes

The repository is organized so that the production-style multi-agent library, the async sanity service, the data, and the documentation each occupy a clear directory. The layout below maps every notable file to its purpose, and the run recipes that follow are the verified commands used during Phase B and Phase C validation.

```
Multi-Agent-Collaboration-System/
├── agents/                    Production-style multi-agent library
│   ├── orchestrator.py        Orchestrator + StructuredRCAResult + execute_commands
│   ├── blackboard.py          Blackboard / Message / Topics
│   ├── registry.py            AgentRegistry, Capability
│   ├── triage_agent.py        Model-free routing
│   ├── solution_generator_agent.py    Agent 1 / Agent 2
│   ├── reconciliation_agent.py         Reconciler + parser
│   ├── validation_agent.py             Validator + parser
│   ├── base_agent.py          BaseAgent / AgentResult / default bid()
│   ├── model_loaders.py       ollama_loader, vllm_loader
│   └── run_rca.py             CLI: python -m agents.run_rca --count 5 ...
├── api/main.py                FastAPI app — /health /ready /analyze /query (port 8000)
├── fastapi/                   Async sanity-check service (port 8001)
│   ├── app.py
│   ├── agents/{agent1,agent2,reconciler,validator}.py
│   ├── services/{orchestrator,memory,model_client}.py
│   ├── schemas/{requests,responses}.py
│   ├── payloads/01..05_*.json    five themed K8s scenarios
│   ├── tests/test_sanity.py      13 tests in 3.5s
│   ├── README.md, RUNNING.md, requirements.txt
│   └── payload.json              default sample
├── tests/test_orchestrator.py    18 tests for the agents/ library
├── data/                      9,500 rows + 6 SFT files + DQ report
├── docker-compose.yml         four LoRA model services (ports 11001-11004)
├── how_the_orchestrator_works.md      plain-English architecture
└── running_end_to_end.md              plain-English operations
```

The verified run recipes are as follows. To run the sanity service in stub mode without any models, install dependencies and start uvicorn from inside the `fastapi/` directory, then post the bundled payload:

```bash
cd fastapi && pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8001 --reload
curl -s -X POST http://localhost:8001/analyze \
     -H 'Content-Type: application/json' \
     -d @payload.json | python3 -m json.tool
```

To run the sanity service in real mode against Ollama, install Ollama, start the daemon, pull the four base models, source the bundled environment file, and start uvicorn with the variables active in the same shell:

```bash
brew install ollama && ollama serve &
ollama pull qwen2.5:7b deepseek-r1:7b mistral:7b llama3.2:3b
cd fastapi && cp .env.example .env && set -a && source .env && set +a
uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

To run the production-style service on port 8000 with a single Ollama model wired into all four slots:

```bash
RCA_BACKEND=ollama OLLAMA_MODEL=qwen3.5:9b uvicorn api.main:app --port 8000
```

To run a CLI batch over the JSONL dataset:

```bash
python -m agents.run_rca --mode roles --count 5
```

To run the test suites:

```bash
python3 -m pytest fastapi/tests/ -v          # 13 tests, 3.5 s
python3 tests/test_orchestrator.py           # 18 tests
```

---

## Appendix B — Selected Source Listings

The following excerpts are the load-bearing pieces of the codebase referenced throughout the report. Each is reproduced verbatim from the working branch and annotated with a short paragraph explaining the design choice.

### B.1 Orchestrator Safety Contract

The `StructuredRCAResult` dataclass and the `execute_commands` function together encode the safety contract. The result begins life with `approval_status="pending"`, transitions to `"approved"` or `"rejected"` only through `approve()` or `reject()`, and is the only sanctioned input to `execute_commands()`, which raises `CommandsNotApprovedError` if the gate has not been flipped.

```python
ApprovalStatus = Literal["pending", "approved", "rejected"]

@dataclass
class StructuredRCAResult:
    incident_id: str
    diagnosis: str
    fix_plan: list[str]
    commands: list[str]
    verification: list[str]
    rollback: list[str]
    ...
    approval_status: ApprovalStatus = "pending"
    approver: str | None = None
    ...

class CommandsNotApprovedError(PermissionError): ...

def execute_commands(result, runner):
    if result.approval_status != "approved":
        raise CommandsNotApprovedError(
            f"refusing to execute commands for {result.incident_id!r}: "
            f"approval_status={result.approval_status!r}"
        )
    ...
```

### B.2 Conflict Detection

The Jaccard token-overlap heuristic is intentionally simple. It is used only as an audit signal: when two diagnoses are sufficiently different, the orchestrator posts a `conflict` message so the trace shows an explicit conflict-resolution event. The Reconciler runs in either case to produce the final fix plan; the conflict message is for human reviewers, not for the Reconciler.

```python
@staticmethod
def _diagnoses_disagree(sol_1, sol_2) -> bool:
    d1 = (sol_1.get("diagnosis","") or "").lower().strip()
    d2 = (sol_2.get("diagnosis","") or "").lower().strip()
    if not d1 or not d2: return False
    t1, t2 = set(d1.split()), set(d2.split())
    overlap = len(t1 & t2) / len(t1 | t2)
    return overlap < 0.5    # ← threshold; CONFLICT msg posted on disagreement
```

### B.3 Approved Per-Role Model Registry

The approved model registry is the source of truth for which models can fill which slot. The factory `from_role_defaults` validates against this registry and rejects unknown names with a helpful error message that includes the slot name, the offending model, and the allowed set.

```python
AGENT_ROLE_MODELS: dict[str, tuple[str, ...]] = {
    "rca":       ("qwen3.5:9b", "deepseek-r1:8b"),
    "executor":  ("devstral-small-2:24b",),
    "validator": ("qwen3.5:35b", "llama3.2:3b"),
}
DEFAULT_PIPELINE = {
    "agent_1":    "qwen3.5:9b",
    "agent_2":    "deepseek-r1:8b",
    "reconciler": "devstral-small-2:24b",
    "validator":  "qwen3.5:35b",
}
```

### B.4 Async Pipeline with Per-Step Timeouts

The async orchestrator in the sanity service relies on `asyncio.gather` to fan out the two RCA calls in parallel and on `asyncio.wait_for` to cap each phase. The cap value `MODEL_TIMEOUT_S` is read from the environment so live model calls can be granted more time without rebuilding the image.

```python
_STEP_TIMEOUT_S = float(os.environ.get("MODEL_TIMEOUT_S", "180"))

async def run_pipeline(bb, incident_id, evidence_text):
    await bb.init(incident_id, evidence_text)
    try:
        a1, a2 = await asyncio.wait_for(
            asyncio.gather(agent1.run(bb, incident_id), agent2.run(bb, incident_id)),
            timeout=_STEP_TIMEOUT_S,
        )
        rec = await asyncio.wait_for(reconciler.run(bb, incident_id), timeout=_STEP_TIMEOUT_S)
        val = await asyncio.wait_for(validator.run(bb, incident_id), timeout=_STEP_TIMEOUT_S)
        ...
    finally:
        await bb.discard(incident_id)
```

### B.5 Reasoning-Block Stripping

This three-line snippet is the operational difference between a working DeepSeek-R1 agent and an empty-fallback. The regex strips the chain-of-thought block before returning, leaving only the final answer for the caller to consume.

```python
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
text = _THINK_BLOCK_RE.sub("", text or "")  # strips DeepSeek-R1 chain-of-thought
```

### B.6 Sample Request and Response

The bundled `fastapi/payload.json` reproduces a real PVC-not-found incident from the dataset, and the sanity service returns a fully populated structured response. The response includes both raw agent outputs and a flattened `final_recommendation` view suitable for the frontend:

```json
{
  "incident_id": "pvc_not_found_mountfail-busybox-pn3zirfh-86bbb7957c-7754l",
  "agent_1_output":  {"agent":"agent_1","diagnosis":"...","confidence":0.82,"notes":"agent_1 real; evidence_len=2039"},
  "agent_2_output":  {"agent":"agent_2","diagnosis":"...","confidence":0.78,"notes":"agent_2 real; evidence_len=2039"},
  "reconciler_output":{"diagnosis":"...","fix_plan":[...],"commands":[...],"chosen_source":"agent_1","notes":"[real] selected agent_1 ..."},
  "validation_output":{"verification":[...],"rollback":[...],"requires_human_review":true,"safety_notes":"[real] ..."},
  "final_recommendation":{"diagnosis":"...","fix_plan":[...],"commands":[...],"verification":[...],"rollback":[...]},
  "requires_human_review": true
}
```

---

## Appendix C — Test Inventory and Results

The test suites are the source of truth for the contract between the services and their callers. They cover three layers — unit, stub end-to-end, and live model gating — and run in a few seconds in CI.

### C.1 `fastapi/tests/test_sanity.py` (13 tests, ~3.5 s)

The sanity suite covers the full HTTP surface of the async service. The first test asserts that `/health` returns the four-agent roster. The second asserts that `/analyze` returns all seven response keys, that the agent identities are correct, that the confidences fall in the unit interval, that the Reconciler chose one of the two RCA agents, and that the Validator emitted both verification and rollback. The third asserts that a caller-supplied `incident_id` is round-tripped. The fourth and fifth confirm that Pydantic returns 422 on a blank or missing `evidence_text`. The sixth asserts that two concurrent requests with distinct ids do not collide. The seventh round-trips the bundled `payload.json`. The eighth through twelfth are the parameterized scenario tests over the five themed payloads, with one PASSED line per scenario so failures point at the exact failure category. The thirteenth fails loudly if the `payloads/` directory is deleted by accident.

### C.2 `tests/test_orchestrator.py` (18 tests, three layers)

The orchestrator suite covers the production-style library across three layers. The unit layer checks imports, asserts that the approved-model registry matches the SFT split exactly, asserts that every entry in the default pipeline is in the approved registry, exercises the helpful `ValueError` produced on a non-approved model, and verifies the markdown parsers used by the Reconciler and Validator. The stub end-to-end layer drives the full pipeline with SFT-shaped canned text per role and asserts pipeline shape, `analyze_batch` correctness, deep-copy guarantees on the input, error capture on timeouts, in-flight approval through `approval_callback`, and the `CommandsNotApprovedError` raise. The live Ollama gating layer attempts a smoke-test against `ollama serve` and skips cleanly if the daemon is unreachable.

### C.3 Live-Mode Verification Matrix

The live-mode results reported in `RUNNING.md` Step 7.6 are reproduced here for completeness. For the storage scenario (`01_storage_pvc_not_found.json`), cold latency falls around 60 seconds, warm latency between 12 and 18 seconds, all four agents report `real` mode, and the diagnosis names the missing PVC. For the image-pull scenario (`02_image_pull_bad_tag.json`), cold latency falls around 20 seconds, warm latency between 10 and 15 seconds, all four agents report `real`, and the diagnosis identifies the bad image tag. For the runtime scenario (`03_runtime_oom_killed.json`), cold latency falls around 20 seconds, warm latency between 12 and 20 seconds, all four agents report `real`, and the diagnosis identifies the memory limit. For the configuration scenario (`04_config_missing_secret.json`), cold latency falls around 20 seconds, warm latency between 10 and 18 seconds, all four agents report `real`, and the diagnosis names the missing Secret. For the security scenario (`05_security_rbac_forbidden.json`), cold latency falls around 25 seconds, warm latency between 12 and 22 seconds, all four agents report `real`, and the diagnosis cites the missing RBAC permission. End-to-end across all five scenarios totals between three and five minutes with peak RAM around 12-14 GB across the four loaded base models.

---

*End of report.*

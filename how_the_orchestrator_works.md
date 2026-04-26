# How the Orchestrator Works (Plain English)

This document explains, without assuming you know how to code, what the orchestrator does and how it works step by step. There is one short technical appendix at the bottom for engineers; you can ignore it.

---

## What problem this solves

When something goes wrong inside Kubernetes (the system that runs containerized apps), engineers have to:

1. Figure out **what** broke and **why** ("the diagnosis").
2. Decide **what to do** to fix it ("the fix plan").
3. Run the **specific commands** that perform the fix ("the actions").
4. Confirm the **fix worked** ("verification").
5. Have a **plan to undo it** if the fix made things worse ("rollback").

This is high-stakes, fast, and requires expertise across many sub-areas (networking, storage, permissions, scheduling). One person — or one AI model — can miss things.

The orchestrator is a software conductor that coordinates **a small team of specialized AI agents** to do this work together, in seconds, the same way a panel of human experts would.

---

## The cast of characters

Think of the system as a small expert panel that meets briefly when an incident arrives. The members are:

| Role | What they do | Compare to a human… |
|---|---|---|
| **Triage** | Reads the incident, decides what kind of problem it is | The intake clerk at an ER |
| **RCA Agent A** *(Qwen 3.5 9B)* | Proposes a diagnosis based on the evidence | First doctor's opinion |
| **RCA Agent B** *(DeepSeek R1 8B)* | Independently proposes a diagnosis | Second doctor's opinion |
| **Reconciler** *(Devstral 24B)* | Compares the two diagnoses, picks or merges them, writes the fix plan and the exact commands | Senior physician who decides treatment |
| **Validator** *(Qwen 3.5 35B or Llama 3.2 3B)* | Decides how to verify the fix worked, and how to undo it if needed | Pharmacist double-checking the prescription |
| **Specialists** *(optional)* | Networking expert, storage expert, RBAC expert, etc. | Sub-specialists called in when relevant |

The Triage member never gives medical opinions — it just routes the case. The two RCA agents work **independently** so they don't influence each other. The Reconciler **arbitrates**. The Validator **checks**.

Behind the scenes, a software component called the **Orchestrator** runs the meeting. It doesn't have an opinion — it makes sure the right people are heard at the right time and that the meeting ends with a clear recommendation.

---

## The shared whiteboard

The most important piece is something called a **Blackboard**. Think of a whiteboard in the middle of the room that everyone can see and write on. Whenever someone says something important, it goes up on the whiteboard with their name and a timestamp.

The whiteboard has labeled sections (called "topics"):

| Section on the whiteboard | What goes here |
|---|---|
| `incident` | The incoming problem description |
| `bid_request` | "Who can handle this?" |
| `bid` | Each agent's confidence score, "I'm 0.95 sure I can help" |
| `dispatch` | The Orchestrator's announcement of who got picked |
| `diagnosis` | Each picked agent's proposed diagnosis |
| `conflict` | Posted only when two diagnoses meaningfully disagree |
| `fix_plan` | The Reconciler's final fix plan + exact commands |
| `validation` | The Validator's verification + rollback steps |

This whiteboard is wiped clean for each new incident. Everything written on it during the meeting is saved as the **trace** — a complete record of who said what, in what order, when. That trace is included in the final answer so a human can audit every decision.

---

## The meeting, step by step

Here is how one incident is handled, end to end. Every numbered step happens automatically; the "meeting" usually finishes in 10–30 seconds.

### Step 1 — Incident arrives

Something calls the orchestrator with an incident. Example:

> *"A pod called worker-vkz64 is stuck in 'ImagePullBackOff' because it can't find the image tag v9.9.9 in the registry."*

The Orchestrator copies the incident (so the original is never modified) and posts it on the **whiteboard** under `incident`.

### Step 2 — Triage routes the incident

The Triage member reads the incident and notices the failure type is **`ImagePullBackOff`**. It posts a "bid request" on the whiteboard saying:

> "Looking for an RCA expert who handles `ImagePullBackOff`."

### Step 3 — Bidding round

Every RCA expert in the room raises a hand and writes their **confidence score** on the whiteboard:

| Agent | Confidence | Why |
|---|---|---|
| **RCA Agent A** (general) | 0.70 | "I'm a generalist, I can probably help" |
| **RCA Agent B** (general) | 0.70 | "Same — I'm a generalist" |
| **Networking Specialist** *(if present)* | 0.95 | "ImagePullBackOff is exactly my subdomain" |

### Step 4 — Dispatch

The Orchestrator picks the **top two** bidders (always two, for diversity — even when one is much higher than the others). It posts the picks on the whiteboard under `dispatch`.

In our example: **Networking Specialist** (0.95) and **RCA Agent B** (0.70).

### Step 5 — The two picked agents reason in parallel

Both agents read the incident from the whiteboard and **work independently** — they do not see each other's work. Each posts a candidate diagnosis under `diagnosis`:

> *Networking Specialist*: *"Image tag v9.9.9 does not exist in the registry. The deployment was probably updated to a tag that was never published."*

> *RCA Agent B*: *"The 'manifest unknown' error usually means the requested tag is not in the registry — this is a tag mismatch, not an authentication problem."*

### Step 6 — Conflict check

The Orchestrator compares the two diagnoses. If they overlap a lot (both say "tag missing"), they agree, and we move on. If they disagree, the Orchestrator posts a `conflict` message — a record that there were two competing answers that needed arbitration.

In our example, they agree. No conflict posted.

### Step 7 — Reconciliation

The **Reconciler** (Devstral 24B) reads both diagnoses, the original incident, and the conflict signal (if any). It writes:

- A **single** unified diagnosis
- A numbered **fix plan**
- The **exact commands** (e.g., `kubectl set image deployment/worker worker=myreg.io/worker:v9.5.2`)
- **Notes** about which candidate it preferred and why

This is posted under `fix_plan`.

### Step 8 — Validation

The **Validator** (Qwen 3.5 35B for high-quality, or Llama 3.2 3B for fast) reads the fix plan and produces:

- **Verification** — checks to confirm the fix worked ("the pod transitions from Pending to Running")
- **Rollback** — steps to undo the fix if it causes new problems

This is posted under `validation`.

### Step 9 — Bundle and return

The Orchestrator gathers everything from the whiteboard and produces the final result:

- The diagnosis
- The fix plan
- The commands
- The verification steps
- The rollback steps
- A complete **audit trail** (every message that appeared on the whiteboard, in order)
- An **approval flag** set to **PENDING** — meaning, no human has yet approved the commands to actually run

The result is returned to whoever called the system.

---

## The safety rules

The orchestrator follows four hard rules, every time:

1. **No surprise mutations.** It works on its own copy of the incident; the caller's data is never altered.
2. **Bounded time.** Each agent gets at most 60 seconds; the whole meeting gets at most 5 minutes (configurable). If a model hangs, the meeting still ends.
3. **Failures are noted, not crashes.** If one agent fails or times out, the others continue. Failures are recorded in an `errors` field on the result.
4. **No commands run without human approval.** The fix commands are produced as text only. They are tagged **PENDING APPROVAL**. A separate, deliberate step (`approve()` then `execute_commands()`) is required to actually run any of them. There is no other path.

---

## Picture of the workflow

```
                          ┌─────────────────────────┐
   Incident ─────────────►│       Orchestrator       │
   (e.g. "pod stuck in    │  (runs the meeting)      │
   ImagePullBackOff")     └────────────┬────────────┘
                                       │ posts everything to:
                                       ▼
            ┌──────────────────────────────────────────────┐
            │                Whiteboard                     │
            │  ─ incident ─ bid_request ─ bid ─ dispatch  │
            │  ─ diagnosis ─ conflict ─ fix_plan ─ validation │
            └──────┬──────────┬──────────┬──────────┬─────┘
                   │          │          │          │
              ┌────▼───┐ ┌────▼────┐ ┌───▼────┐ ┌──▼────┐
              │ Triage │ │ RCA A/B │ │Reconcile│ │Validate│
              │ (route)│ │  (diag) │ │ (plan)  │ │(verify)│
              └────────┘ └─────────┘ └────────┘ └───────┘

   Result returned to caller:  diagnosis + fix plan + commands + verify
                              + rollback + complete audit trail
                              + approval_status = "PENDING"
```

---

## What "specialists" are

The default cast has two general-purpose RCA agents (A and B). The architecture also lets you add **specialists** — agents that are extra-confident on specific kinds of K8s incidents.

Examples of specialists you could add:

| Specialist | Best at handling | How it bids |
|---|---|---|
| **Networking** | ImagePullBackOff, DNS issues, service mesh, ingress | 0.95 if event mentions image-pull or DNS, otherwise 0.0 |
| **RBAC** | Forbidden / Unauthorized errors, missing service accounts | 0.95 if event is "Forbidden", otherwise 0.0 |
| **Storage** | Missing PersistentVolumeClaim, mount failures | 0.95 if event is FailedMount or PVCNotFound |
| **Scheduling** | FailedScheduling, taints, resource limits | 0.95 if pod is unschedulable |

Specialists do not require their own trained model — each one shares one of the existing five models, but uses a custom prompt and bid logic. Adding one is a few lines of code (see the engineering appendix).

When specialists are present, the bidding round has real meaning: a Networking incident gets the Networking specialist plus one generalist. A storage incident gets the Storage specialist plus one generalist. The system **dynamically chooses who works on each incident** instead of using the same two agents every time.

---

## Why this is a useful design

Three properties make this a "multi-agent" system, not just "one model called four times":

1. **Specialization.** Each role has a specific job, with its own prompt, parser, and (often) a different model tuned for that job.
2. **Independent reasoning.** The two RCA agents don't see each other's work — when they agree, that's a strong signal; when they disagree, the system records the disagreement and arbitrates explicitly.
3. **Arbitration.** A separate Reconciler agent compares and merges. Most "ensemble" approaches just average outputs; this system **reasons about** them.

Together, these reduce the chance of self-confirming errors (where a model that proposes a wrong fix also validates it) and make the audit trail show **why** every decision was made.

---

## What if something fails?

| What can fail | What happens |
|---|---|
| One RCA agent times out | The other one's diagnosis is used; the failure is recorded in `errors` |
| Both RCA agents time out | The reconciler runs with empty inputs and produces an empty plan; the result clearly shows nothing usable was generated |
| The reconciler fails | The validator runs with no plan; the result has empty `commands` |
| The model server is unreachable | A separate `/ready` health probe fails before traffic is sent |
| Someone tries to execute commands without approval | The system raises `CommandsNotApprovedError` — execution is impossible without the explicit approval step |

You will always get a result back, you will always be able to see exactly what happened in the trace, and nothing destructive can happen by accident.

---

## How long does this take?

| Backend | Typical end-to-end | What dominates the time |
|---|---|---|
| Stub responses (built-in) | ~10 ms | Nothing real runs; useful for testing the plumbing |
| Local Ollama (one GPU) | ~10–30 s | The four model calls; cold starts can add another 30 s |
| vLLM cluster (one GPU per model) | ~5–15 s | The slowest of the four parallel/sequential calls |

The two RCA agents run in parallel, so adding the second one doesn't roughly double the time — it adds at most the slower of the two.

---

## What this system is NOT

To set expectations:

- **It is not autonomous.** It does not run kubectl commands by itself. Every command requires a human approval before execution.
- **It is not a replacement for an SRE.** It produces an opinionated recommendation. A trained engineer should review the diagnosis and especially the commands before approving.
- **It does not learn from a single run.** Improvements come from re-training the underlying models on new examples, not from in-flight feedback.
- **It is not infallible.** Like any LLM-driven system, it can be wrong. The two-agent + reconciler design and the audit trail are there to help humans catch errors faster, not to claim the system never makes them.

---

## Engineering appendix

For developers — short reference. Skip this if you're a non-technical reader.

**Pattern.** Blackboard system + Contract Net Lite + a centralized protocol runner. Reference: Hayes-Roth 1985 (Blackboards), Smith 1980 (Contract Net).

**Key files.**
- `agents/blackboard.py` — `Blackboard`, `Message`, `Topics`. Thread-safe pub/sub.
- `agents/registry.py` — `AgentRegistry`, `Capability`. Discovery by role and handles match.
- `agents/triage_agent.py` — Model-free routing.
- `agents/orchestrator.py` — `Orchestrator.analyze()` runs the protocol; `add_specialist()` registers new RCA experts.
- `agents/{base,solution_generator,reconciliation,validation}_agent.py` — The four core agents.

**Public API (unchanged from the previous shape).**
```python
from agents.orchestrator import Orchestrator
from agents.model_loaders import ollama_loader

orch = Orchestrator.from_role_defaults(ollama_loader())
result = orch.analyze(incident_dict)
print(result.diagnosis, result.fix_plan, result.commands)
print(result.trace)  # full audit log
```

**Adding a specialist.**
```python
from agents.solution_generator_agent import SolutionGeneratorAgent
from agents.registry import Capability

class NetworkingSpecialistAgent(SolutionGeneratorAgent):
    def bid(self, incident):
        reason = (incident.get("event_reason") or "").lower()
        msg    = (incident.get("event_message") or "").lower()
        if "imagepull" in reason or "manifest unknown" in msg:
            return 0.95
        if "dns" in msg or "service mesh" in msg:
            return 0.85
        return 0.0

orch.add_specialist(
    NetworkingSpecialistAgent(name="networking", model=ollama_call),
    Capability(role="rca",
               handles={"ImagePullBackOff", "ErrImagePull",
                        "DNSResolution", "FailedCreatePodSandBox"}),
)
```

**Tests.** `tests/test_orchestrator.py` — 18 tests covering the full pipeline plus stub end-to-end and live Ollama gating. Run with `python3 tests/test_orchestrator.py`.

**Related reading.**
- `running_end_to_end.md` — how to deploy and call the inference service.

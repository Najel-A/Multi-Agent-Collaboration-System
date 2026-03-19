Typical Agents in a Multi-Agent Collaboration System                                                                                                             
                                                                                                                                                                 
  Core Agent Roles                                                                                                                                                 
                                                                                                                                                                 
  Every MAS collaboration system uses some combination of these fundamental roles:                                                                                 
                                                                                                                                                                 
  ---                                                                                                                                                              
  1. Planner Agent                                                                                                                                                 
                                                                                                                                                                   
  What it does: Receives a task, breaks it into sub-tasks, decides which agents to involve and in what order.

  Why it matters: Without a planner, agents don't know what to work on. It's the brain that turns a vague problem into a structured workflow.

  In your project: Triage Planner (M2), Strategy Planner (M3)

  ---
  2. Executor Agent

  What it does: Carries out a specific sub-task assigned by the planner. It does the actual work — runs analysis, generates output, calls tools.

  In your project: Diagnostic Executor (M2), Signal Executor (M3), Evidence Analyst (M4)

  ---
  3. Reviewer / Critic Agent

  What it does: Evaluates another agent's output for correctness, completeness, and safety. Accepts or rejects with feedback.

  Why it matters: Prevents errors from propagating. Acts as quality control.

  In your project: Evidence Reviewer (M2), Safety Reviewer (M3), Debate Referee (M4)

  ---
  4. Retrieval Agent

  What it does: Searches a knowledge base (vector store, database, documents) and returns relevant context to other agents.

  Why it matters: Grounds agent reasoning in real data instead of hallucination.

  In your project: Context Retriever (M3)

  ---
  5. Coordinator / Orchestrator Agent

  What it does: Manages communication between agents. Routes messages, resolves conflicts, handles turn-taking.

  Why it matters: Without coordination, agents talk past each other or duplicate work.

  In your project: The framework itself (CrewAI, LangGraph, AutoGen) handles this role

  ---
  6. Specialist Agent

  What it does: Deep expertise in one domain. Only activates when the task matches its specialty.

  Why it matters: A generalist agent can't match domain-specific accuracy.

  Examples in MAS literature: Security Specialist, Network Specialist, Database Specialist, Performance Specialist

  ---
  7. Debater Agent

  What it does: Proposes a hypothesis and defends it against challenges from other debaters. Provides a unique reasoning perspective.

  Why it matters: Adversarial debate reduces bias, catches hallucinations, and surfaces alternative explanations.

  In your project: Evidence Analyst, Pattern Matcher, Contrarian Investigator (M4)

  ---
  8. Safety / Human-in-the-Loop Agent

  What it does: Gates high-risk actions. Blocks dangerous outputs. Escalates to humans when confidence is low.

  Why it matters: Prevents autonomous agents from causing damage.

  In your project: Risk Assessor (M4), Safety Reviewer (M3)

  ---
  9. Memory Agent

  What it does: Stores past interactions, resolved tasks, and learned patterns. Provides long-term context across sessions.

  Why it matters: Without memory, agents repeat mistakes and can't improve over time.

  Common implementations: Vector store (FAISS), shared blackboard, context window management

  ---
  10. Merger / Synthesizer Agent

  What it does: Combines outputs from multiple agents into a single coherent response. Resolves contradictions.

  Why it matters: When multiple agents work in parallel, someone needs to reconcile their findings.

  In your project: Consensus Merger (M3), Debate Referee (M4)

  ---
  Common Collaboration Patterns

  Pattern A: Hierarchical (Boss → Workers)

  Planner
    ├──▶ Executor 1
    ├──▶ Executor 2
    └──▶ Executor 3
           │
       Reviewer
  One agent assigns, others execute, one validates.

  Pattern B: Pipeline (Assembly Line)

  Agent A → Agent B → Agent C → Agent D
  Each agent does one step and passes to the next.

  Pattern C: Debate (Adversarial)

  Debater 1 ──┐
  Debater 2 ──┼──▶ Referee ──▶ Output
  Debater 3 ──┘
  Multiple agents argue, one judges.

  Pattern D: Blackboard (Shared Memory)

  Agent 1 ──┐              ┌── Agent 1
  Agent 2 ──┼──▶ Blackboard ◀──┼── Agent 2
  Agent 3 ──┘              └── Agent 3
  All agents read/write to a shared state. Any agent can act when it sees relevant information.

  Pattern E: Auction / Bidding

  Coordinator broadcasts task
    ├──▶ Specialist 1 bids 0.3
    ├──▶ Specialist 2 bids 0.9  ← wins
    └──▶ Specialist 3 bids 0.1
  Agents compete for tasks based on confidence.

  ---
  How These Map to Your 4 Models

  ┌────────────┬──────────────────────┬─────────────────────┬────────────────────┬───────────────────────────────────────────────┐
  │ Agent Role │    M1 (Baseline)     │   M2 (Sequential)   │ M3 (Heterogeneous) │                  M4 (Debate)                  │
  ├────────────┼──────────────────────┼─────────────────────┼────────────────────┼───────────────────────────────────────────────┤
  │ Planner    │ SRE Agent (does all) │ Triage Planner      │ Strategy Planner   │ —                                             │
  ├────────────┼──────────────────────┼─────────────────────┼────────────────────┼───────────────────────────────────────────────┤
  │ Executor   │ SRE Agent (does all) │ Diagnostic Executor │ Signal Executor    │ Evidence Analyst, Pattern Matcher, Contrarian │
  ├────────────┼──────────────────────┼─────────────────────┼────────────────────┼───────────────────────────────────────────────┤
  │ Reviewer   │ —                    │ Evidence Reviewer   │ Safety Reviewer    │ Debate Referee, Risk Assessor                 │
  ├────────────┼──────────────────────┼─────────────────────┼────────────────────┼───────────────────────────────────────────────┤
  │ Retrieval  │ —                    │ —                   │ Context Retriever  │ (debaters query memory)                       │
  ├────────────┼──────────────────────┼─────────────────────┼────────────────────┼───────────────────────────────────────────────┤
  │ Merger     │ —                    │ —                   │ Consensus Merger   │ Debate Referee (picks winner)                 │
  ├────────────┼──────────────────────┼─────────────────────┼────────────────────┼───────────────────────────────────────────────┤
  │ Safety     │ —                    │ —                   │ Safety Reviewer    │ Risk Assessor                                 │
  ├────────────┼──────────────────────┼─────────────────────┼────────────────────┼───────────────────────────────────────────────┤
  │ Memory     │ —                    │ —                   │ FAISS index        │ FAISS index                                   │
  ├────────────┼──────────────────────┼─────────────────────┼────────────────────┼───────────────────────────────────────────────┤
  │ Specialist │ —                    │ —                   │ —                  │ Each debater specializes in a reasoning style │
  └────────────┴──────────────────────┴─────────────────────┴────────────────────┴───────────────────────────────────────────────┘

  ---
  What Makes a Good CoD (Collaboration) System

  From the project description, the key requirements are:

  ┌────────────────────────────┬───────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │        Requirement         │                                          Which Agents Address It                                          │
  ├────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Message-passing protocols  │ Coordinator + all agents communicate via structured messages (JSON payloads with role, content, metadata) │
  ├────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Task assignment            │ Planner assigns sub-tasks; Coordinator broadcasts in auction pattern                                      │
  ├────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Conflict resolution        │ Merger resolves parallel disagreements; Referee resolves debate disagreements                             │
  ├────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Shared memory / blackboard │ Memory Agent + FAISS index; all agents can read/write shared state                                        │
  ├────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Role negotiation           │ Auction pattern (agents bid for tasks); Debate pattern (agents argue for their hypothesis)                │
  ├────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Evaluation & benchmarking  │ Run all 4 models on same test set from Gold parquet, compare accuracy/cost/latency                        │
  └────────────────────────────┴───────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  ---
  The 10 Agent Roles You Should Implement

  For a complete CoD system, build these:

  ┌─────┬────────────────────────┬───────────────┐
  │  #  │         Agent          │ Required For  │
  ├─────┼────────────────────────┼───────────────┤
  │ 1   │ Planner                │ M2, M3        │
  ├─────┼────────────────────────┼───────────────┤
  │ 2   │ Executor               │ M2, M3        │
  ├─────┼────────────────────────┼───────────────┤
  │ 3   │ Reviewer               │ M2, M3        │
  ├─────┼────────────────────────┼───────────────┤
  │ 4   │ Retrieval              │ M3, M4        │
  ├─────┼────────────────────────┼───────────────┤
  │ 5   │ Merger                 │ M3            │
  ├─────┼────────────────────────┼───────────────┤
  │ 6   │ Safety                 │ M3, M4        │
  ├─────┼────────────────────────┼───────────────┤
  │ 7   │ Debater (x3 instances) │ M4            │
  ├─────┼────────────────────────┼───────────────┤
  │ 8   │ Referee                │ M4            │
  ├─────┼────────────────────────┼───────────────┤
  │ 9   │ Memory                 │ M3, M4        │
  ├─────┼────────────────────────┼───────────────┤
  │ 10  │ Single SRE             │ M1 (baseline) │
  └─────┴────────────────────────┴───────────────┘

  These 10 roles (15 agent instances across 4 models) cover all the collaboration patterns: hierarchical, pipeline, parallel+merge, and adversarial debate.
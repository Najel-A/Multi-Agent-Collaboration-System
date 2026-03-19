"""Orchestrator — coordinates all agents through the RCA pipeline."""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from agents.base_agent import AgentResult
from agents.triage_agent import TriageAgent
from agents.log_analyst_agent import LogAnalystAgent
from agents.state_analyst_agent import StateAnalystAgent
from agents.diagnostic_agent import DiagnosticAgent
from agents.remediation_agent import RemediationAgent


@dataclass
class RCAReport:
    """Final Root Cause Analysis report."""
    incident_id: str
    category: str
    severity: str
    root_cause: str
    evidence: dict[str, Any]
    fix_plan: list[str]
    commands: list[str]
    verification: list[str]
    rollback: list[str]
    agent_results: dict[str, AgentResult] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "category": self.category,
            "severity": self.severity,
            "root_cause": self.root_cause,
            "evidence": self.evidence,
            "fix_plan": self.fix_plan,
            "commands": self.commands,
            "verification": self.verification,
            "rollback": self.rollback,
            "duration_ms": self.duration_ms,
        }

    def summary(self) -> str:
        lines = [
            f"=== RCA Report: {self.incident_id} ===",
            f"Category:   {self.category}",
            f"Severity:   {self.severity}",
            f"Root Cause: {self.root_cause}",
            "",
            "Evidence:",
        ]
        for source, detail in self.evidence.items():
            lines.append(f"  [{source}] {detail}")
        lines.append("")
        lines.append("Fix Plan:")
        for i, step in enumerate(self.fix_plan, 1):
            lines.append(f"  {i}. {step}")
        lines.append("")
        lines.append("Commands:")
        for cmd in self.commands:
            lines.append(f"  $ {cmd}")
        lines.append("")
        lines.append("Verification:")
        for v in self.verification:
            lines.append(f"  - {v}")
        lines.append("")
        lines.append("Rollback:")
        for r in self.rollback:
            lines.append(f"  - {r}")
        lines.append(f"\nCompleted in {self.duration_ms:.0f}ms")
        return "\n".join(lines)


class Orchestrator:
    """Coordinates the multi-agent RCA pipeline.

    Workflow:
      1. Triage Agent    → classify category + severity
      2. Log Analyst     → extract log findings      } run in parallel
         State Analyst   → extract state findings     }
      3. Diagnostic Agent → synthesize root cause
      4. Remediation Agent → generate fix plan

    Each agent can operate in rule-based mode (no model) or
    model-based mode (fine-tuned LLM injected at init).
    """

    def __init__(
        self,
        triage_model: Any = None,
        log_model: Any = None,
        state_model: Any = None,
        diagnostic_model: Any = None,
        remediation_model: Any = None,
        parallel: bool = True,
    ):
        self.triage = TriageAgent(model=triage_model)
        self.log_analyst = LogAnalystAgent(model=log_model)
        self.state_analyst = StateAnalystAgent(model=state_model)
        self.diagnostic = DiagnosticAgent(model=diagnostic_model)
        self.remediation = RemediationAgent(model=remediation_model)
        self.parallel = parallel

    def analyze(self, incident: dict[str, Any]) -> RCAReport:
        """Run the full RCA pipeline on a single incident."""
        start = time.time()
        incident_id = incident.get("id", "unknown")

        # --- Step 1: Triage ---
        triage_result = self.triage.run(incident)
        triage_findings = triage_result.findings

        # --- Step 2: Parallel investigation ---
        if self.parallel:
            log_result, state_result = self._run_parallel(incident)
        else:
            log_result = self.log_analyst.run(incident)
            state_result = self.state_analyst.run(incident)

        log_findings = log_result.findings
        state_findings = state_result.findings

        # --- Step 3: Diagnosis ---
        # Inject intermediate results into incident for the diagnostic agent
        incident["_triage_result"] = triage_findings
        incident["_log_result"] = log_findings
        incident["_state_result"] = state_findings

        diag_result = self.diagnostic.run(incident)
        diag_findings = diag_result.findings

        # --- Step 4: Remediation ---
        incident["_diagnostic_result"] = diag_findings

        remed_result = self.remediation.run(incident)
        remed_findings = remed_result.findings

        # --- Clean up injected keys ---
        for key in ("_triage_result", "_log_result", "_state_result", "_diagnostic_result"):
            incident.pop(key, None)

        duration_ms = (time.time() - start) * 1000

        return RCAReport(
            incident_id=incident_id,
            category=triage_findings.get("category", "Unknown"),
            severity=triage_findings.get("severity", "medium"),
            root_cause=diag_findings.get("diagnosis", ""),
            evidence={
                "log_analysis": log_findings.get("summary", ""),
                "state_analysis": state_findings.get("summary", ""),
            },
            fix_plan=remed_findings.get("fix_plan", []),
            commands=remed_findings.get("commands", []),
            verification=remed_findings.get("verification", []),
            rollback=remed_findings.get("rollback", []),
            agent_results={
                "triage": triage_result,
                "log_analyst": log_result,
                "state_analyst": state_result,
                "diagnostic": diag_result,
                "remediation": remed_result,
            },
            duration_ms=duration_ms,
        )

    def _run_parallel(self, incident: dict[str, Any]) -> tuple[AgentResult, AgentResult]:
        """Run log and state analysts in parallel."""
        results: dict[str, AgentResult] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(self.log_analyst.run, incident): "log",
                pool.submit(self.state_analyst.run, incident): "state",
            }
            for future in as_completed(futures):
                name = futures[future]
                results[name] = future.result()
        return results["log"], results["state"]

    def analyze_batch(
        self, incidents: list[dict[str, Any]], max_workers: int = 4
    ) -> list[RCAReport]:
        """Analyze multiple incidents in parallel."""
        reports: list[RCAReport] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self.analyze, inc): i for i, inc in enumerate(incidents)}
            indexed: list[tuple[int, RCAReport]] = []
            for future in as_completed(futures):
                idx = futures[future]
                indexed.append((idx, future.result()))
            indexed.sort(key=lambda x: x[0])
            reports = [r for _, r in indexed]
        return reports

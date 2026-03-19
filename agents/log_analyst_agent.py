"""Log Analyst Agent — extracts error patterns and root cause signals from container logs."""

import re
from typing import Any

from agents.base_agent import AgentResult, BaseAgent

# Patterns to extract from logs, ordered by priority
LOG_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, finding_key, description)
    (r"ERROR\s+missing required env var\s+(\S+)",  "missing_env_var",      "Missing environment variable"),
    (r"ERROR\s+Forbidden:.*cannot\s+(\w+)\s+resource\s+\"(\w+)\"", "rbac_error", "RBAC permission denied"),
    (r"ERROR\s+dial tcp:.*lookup\s+(\S+).*no such host", "dns_failure",    "DNS resolution failure"),
    (r"ERROR\s+dial tcp\s+(\S+):\s*connect:\s*connection refused", "connection_refused", "Connection refused"),
    (r"ERROR\s+invalid argument:\s*(.+)",           "invalid_args",        "Invalid command-line argument"),
    (r"ERROR\s+process killed.*OOM",                "oom_signal",          "Process killed (likely OOM)"),
    (r"WARN\s+readiness returning 503",             "readiness_503",       "Readiness probe returning 503"),
    (r"WARN\s+health endpoint slow",                "liveness_slow",       "Health endpoint slow (liveness risk)"),
    (r"WARN\s+dependency not ready:\s*(.+)",        "dependency_issue",    "Dependency not ready"),
    (r"ERROR\s+required config key missing:\s*(\S+)", "config_key_missing", "Config key missing"),
    (r"ERROR.*secret.*not found",                   "secret_missing",      "Secret not found"),
    (r"ERROR.*unauthorized",                        "auth_failure",        "Authentication failure"),
]


class LogAnalystAgent(BaseAgent):
    """Extracts structured findings from container logs.

    Rule-based mode uses regex patterns to identify error signals.
    Model-based mode sends logs to a fine-tuned LLM for deeper analysis.
    """

    def __init__(self, model: Any = None):
        super().__init__(name="log_analyst", model=model)

    def run(self, incident: dict[str, Any]) -> AgentResult:
        obs = incident.get("observations", {})
        logs = obs.get("container_logs", "")

        if not logs.strip():
            return AgentResult(
                agent_name=self.name,
                status="success",
                findings={"errors": [], "warnings": [], "summary": "No container logs available."},
                confidence=0.0,
                raw_output="",
            )

        if self.model is not None:
            prompt = self._build_prompt(incident)
            raw = self._call_model(prompt)
            findings = self._parse_model_output(raw)
        else:
            findings = self._analyze_rule_based(logs)

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            confidence=0.85 if self.model else 0.7,
            raw_output=logs[:500],
        )

    def _build_prompt(self, incident: dict[str, Any]) -> str:
        obs = incident.get("observations", {})
        return (
            "Analyze these Kubernetes container logs. Extract:\n"
            "1. ERROR lines and what they indicate\n"
            "2. WARNING lines and their significance\n"
            "3. A one-sentence summary of the root cause signal\n\n"
            f"=== container logs ===\n{obs.get('container_logs', '')}\n\n"
            "Respond in format:\n"
            "ERRORS: [list of error descriptions]\n"
            "WARNINGS: [list of warning descriptions]\n"
            "SUMMARY: <one sentence root cause signal>"
        )

    @staticmethod
    def _analyze_rule_based(logs: str) -> dict[str, Any]:
        errors: list[dict[str, str]] = []
        warnings: list[str] = []
        matched_keys: set[str] = set()

        for pattern, key, description in LOG_PATTERNS:
            match = re.search(pattern, logs, re.IGNORECASE)
            if match and key not in matched_keys:
                matched_keys.add(key)
                errors.append({
                    "type": key,
                    "description": description,
                    "detail": match.group(0).strip(),
                })

        # Extract all WARN lines
        for line in logs.splitlines():
            if "WARN" in line:
                warnings.append(line.strip().split("WARN", 1)[-1].strip())

        # Build summary from top finding
        if errors:
            summary = errors[0]["description"] + ": " + errors[0]["detail"]
        elif warnings:
            summary = f"Warnings detected: {warnings[0]}"
        else:
            summary = "No clear error pattern found in logs."

        return {
            "errors": errors,
            "warnings": warnings,
            "summary": summary,
        }

    @staticmethod
    def _parse_model_output(raw: str) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        summary = ""
        for line in raw.splitlines():
            upper = line.strip().upper()
            if upper.startswith("ERRORS:"):
                errors = [e.strip() for e in line.split(":", 1)[1].split(",")]
            elif upper.startswith("WARNINGS:"):
                warnings = [w.strip() for w in line.split(":", 1)[1].split(",")]
            elif upper.startswith("SUMMARY:"):
                summary = line.split(":", 1)[1].strip()
        return {"errors": errors, "warnings": warnings, "summary": summary}

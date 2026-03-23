import pandas as pd

# -------------------------
# Load data
# -------------------------
df = pd.read_parquet("/home/akash/repos/Multi-Agent-Collaboration-System/akstuff/master_incident_dataset.parquet")

# Take 1 row per scenario
one_per_scenario = (
    df.groupby("scenario_id", group_keys=False)
      .head(1)
      .reset_index(drop=True)
)

# -------------------------
# Prompt template
# -------------------------
SYSTEM_PROMPT = (
    "You are a Kubernetes Site Reliability Engineering (SRE) agent. "
    "Given raw observability evidence from a Kubernetes incident, provide:\n"
    "1. A root cause diagnosis explaining what went wrong and why.\n"
    "2. A step-by-step fix plan to resolve the incident.\n"
    "3. Concrete actions or commands to apply the fix.\n"
    "4. Verification steps to confirm the fix worked.\n"
    "5. Rollback guidance if the fix causes issues."
)

# -------------------------
# Build evidence text from a row
# -------------------------
def build_evidence_text(row):
    return f"""namespace: {row.get('namespace', '')}
workload: {row.get('workload_name', '')}
container: {row.get('container_name', '')}
image: {row.get('image', '')}
=== kubectl get pods ===
NAME\tREADY\tSTATUS\tRESTARTS\tAGE
{row.get('workload_name', '')}-pod\t0/1\t{row.get('pod_status', '')}\t{row.get('restart_count', '')}\tunknown

=== kubectl describe pod ===
Name:           {row.get('workload_name', '')}-pod
Namespace:      {row.get('namespace', '')}
Status:         {row.get('pod_status', '')}
Containers:
  {row.get('container_name', '')}:
    Image:      {row.get('image', '')}
    State:      Waiting
      Reason:   {row.get('waiting_reason', '')}
      Message:  {row.get('error_message', '')}
    Ready:      False
    Restart Count: {row.get('restart_count', '')}

Events:
LAST SEEN\tTYPE\tREASON\tOBJECT\tMESSAGE
unknown\tWarning\t{row.get('event_reason', '')}\tpod/{row.get('workload_name', '')}-pod\t{row.get('event_message', '')}

=== kubectl get events ===
LAST SEEN\tTYPE\tREASON\tOBJECT\tMESSAGE
unknown\tWarning\t{row.get('event_reason', '')}\tpod/{row.get('workload_name', '')}-pod\t{row.get('event_message', '')}

=== container logs ===
{row.get('error_message', '')}

=== metrics_snapshot ===
metrics_snapshot: restarts={row.get('restart_count_metrics', row.get('restart_count', ''))}, oom_killed={row.get('oom_killed', False)}"""

# -------------------------
# Build payload from evidence
# -------------------------
def build_payload(evidence_text):
    return (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n"
        f"Analyze this Kubernetes incident and provide diagnosis, remediation, verification, and rollback guidance.\n\n"
        f"Incident Evidence:\n{evidence_text}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )

# -------------------------
# Generate payloads
# -------------------------
one_per_scenario["evidence_text"] = one_per_scenario.apply(build_evidence_text, axis=1)
one_per_scenario["payload"] = one_per_scenario["evidence_text"].apply(build_payload)

# Show scenario_id + payload
for _, row in one_per_scenario[["scenario_id", "payload"]].iterrows():
    print("=" * 100)
    print("SCENARIO:", row["scenario_id"])
    print(row["payload"])
    print()

one_per_scenario[["scenario_id", "payload"]].to_csv("scenario_payloads.csv", index=False)
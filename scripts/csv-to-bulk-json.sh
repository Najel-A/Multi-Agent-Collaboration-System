#!/bin/bash
# Script to convert audit-logs.csv to Elasticsearch bulk JSON format for Kibana upload
# Usage: ./scripts/csv-to-bulk-json.sh > bulk-audit-logs.json

CSV_FILE="${1:-test_data/audit-logs.csv}"
INDEX_NAME="${2:-audit-logs}"

if [ ! -f "$CSV_FILE" ]; then
  echo "Error: CSV file not found: $CSV_FILE" >&2
  exit 1
fi

# CSV fields (no header row):
# 0: RequestStage JSON
# 1: Timestamp
# 2: User/Principal
# 3: Verb
# 4: Resource Type
# 5: Namespace (can be empty)
# 6: Resource Name (can be empty)
# 7: API Path
# 8: Request ID
# 9: Stage
# 10: HTTP Status Code
# 11-13: Empty fields

# Read CSV and convert to bulk format
awk -F',' '
BEGIN {
  OFS=""
}
{
  # Parse fields (handling quoted fields)
  gsub(/^"/, "", $1); gsub(/"$/, "", $1);  # Remove outer quotes from first field
  gsub(/""/, "\"", $1);  # Unescape double quotes
  
  # Extract cluster name from JSON if present
  cluster_name = ""
  if (match($1, /"name":"([^"]*)"/, arr)) {
    cluster_name = arr[1]
  }
  
  # Create JSON document
  printf "{\"index\":{\"_index\":\"%s\"}}\n", "'"$INDEX_NAME"'"
  printf "{\"@timestamp\":\"%s\",", $2
  printf "\"cluster_name\":\"%s\",", cluster_name
  printf "\"user\":\"%s\",", $3
  printf "\"verb\":\"%s\",", $4
  printf "\"resource_type\":\"%s\",", $5
  printf "\"namespace\":\"%s\",", ($6 == "" ? "null" : "\""$6"\"")
  printf "\"resource_name\":\"%s\",", ($7 == "" ? "null" : "\""$7"\"")
  printf "\"api_path\":\"%s\",", $8
  printf "\"request_id\":\"%s\",", $9
  printf "\"stage\":\"%s\",", $10
  printf "\"http_status\":%s", ($11 == "" ? "null" : $11)
  printf "}\n"
}
' "$CSV_FILE"


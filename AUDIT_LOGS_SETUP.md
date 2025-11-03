# Audit Logs CSV Processing Setup Guide

This guide explains how to process `audit-logs.csv` through the ELK stack using your preferred workflow.

## Workflow Steps

### Step 1: Upload Dataset Directly to Kibana (Manual Upload)

This step lets you manually upload the CSV data to Kibana for initial viewing and testing.

#### Option A: Using Kibana Dev Tools (Recommended)

1. **Convert CSV to Bulk JSON format:**
   ```bash
   python3 scripts/csv-to-bulk-json.py test_data/audit-logs.csv audit-logs > bulk-audit-logs.json
   ```

2. **Port-forward Kibana:**
   ```bash
   kubectl port-forward svc/kibana-sample-kb-http 5601:5601 -n elastic-system
   ```

3. **Get Elasticsearch password:**
   ```bash
   kubectl -n elastic-system get secret elasticsearch-sample-es-elastic-user \
     -o go-template='{{.data.elastic | base64decode}}{{"\n"}}'
   ```

4. **Open Kibana Dev Tools:**
   - Navigate to `https://localhost:5601`
   - Login with username: `elastic` and the password from step 3
   - Go to Dev Tools (hamburger menu → Dev Tools)

5. **Upload bulk data:**
   ```bash
   curl -k -X POST "https://localhost:5601/api/console/proxy?path=_bulk&method=POST" \
     -H 'Content-Type: application/json' \
     -u elastic:<password> \
     --data-binary "@bulk-audit-logs.json"
   ```

   OR use Kibana Dev Tools console:
   ```json
   POST _bulk
   <paste content from bulk-audit-logs.json>
   ```

#### Option B: Using Kibana Data Visualizer

1. Navigate to Kibana → Machine Learning → Data Visualizer
2. Upload `test_data/audit-logs.csv` directly
3. Configure field mappings and import

### Step 2: Copy CSV File into KinD Cluster (For Filebeat)

Filebeat needs access to the CSV file inside the Kubernetes cluster. Copy it into KinD:

# 1) Create the dir inside the Kind node
docker exec dev-control-plane mkdir -p /tmp/audit-logs

# 2) Copy the file into the node
docker cp test_data/audit-logs.csv dev-control-plane:/tmp/audit-logs/

# 3) Verify
docker exec dev-control-plane ls -l /tmp/audit-logs


```bash
# Copy CSV into KinD cluster
kind cp test_data/audit-logs.csv dev:/tmp/audit-logs/audit-logs.csv
```

**Note:** Ensure the directory exists:
```bash
# Create directory in KinD cluster if needed
docker exec -it dev-control-plane mkdir -p /tmp/audit-logs
```

### Step 3: Deploy Updated Configurations

The following files have been updated:
- `clusters/elk/elastic/filebeat.yaml` - Added CSV input
- `clusters/elk/elastic/logstash.yaml` - Added CSV parsing and transformations

Apply the changes:
```bash
cd clusters/elk
make apply
```

Or manually apply:
```bash
kubectl apply -k clusters/elk/elastic/
```

### Step 4: Verify Processing

Check Filebeat logs:
```bash
kubectl logs -f -l name=filebeat-sample -n elastic-system
```

Check Logstash logs:
```bash
kubectl logs -f logstash-sample-0 -n elastic-system
```

### Step 5: View Data in Kibana

1. Port-forward Kibana (if not already done):
   ```bash
   kubectl port-forward svc/kibana-sample-kb-http 5601:5601 -n elastic-system
   ```

2. Create index pattern:
   - Go to Kibana → Management → Stack Management → Index Patterns
   - Create index pattern: `audit-logs-*`
   - Select `@timestamp` as the time field

3. View data:
   - Go to Discover
   - Select `audit-logs-*` index pattern
   - Explore the parsed audit logs with all transformations applied

## Data Flow

```
CSV File → Filebeat (reads from /tmp/audit-logs/audit-logs.csv)
    ↓
Logstash (parses CSV, extracts JSON, adds severity)
    ↓
Elasticsearch (indexed as audit-logs-YYYY.MM.DD)
    ↓
Kibana (visualization and analysis)
```

## Transformations Applied

Logstash performs the following transformations on CSV audit logs:

1. **CSV Parsing**: Splits CSV into 14 fields
2. **JSON Extraction**: Extracts cluster name from first column JSON
3. **Timestamp Parsing**: Converts ISO8601 timestamp to `@timestamp`
4. **Severity Calculation**: Adds severity field based on HTTP status:
   - `success`: 200-299
   - `redirect`: 300-399
   - `client_error`: 400-499
   - `server_error`: 500+
   - `unknown`: Other
5. **Field Cleanup**: Removes empty/temporary fields
6. **Index Routing**: Routes to `audit-logs-*` index (separate from container logs)

## Troubleshooting

### Filebeat can't find CSV file
- Ensure you copied the file: `kind cp test_data/audit-logs.csv dev:/tmp/audit-logs/audit-logs.csv`
- Check if file exists: `docker exec -it dev-control-plane ls -la /tmp/audit-logs/`

### Logstash parsing errors
- Check Logstash logs for CSV parsing issues
- Verify CSV format matches expected structure
- Check if JSON in first column is properly escaped

### No data in Kibana
- Verify index pattern is created: `audit-logs-*`
- Check Elasticsearch indices: `kubectl exec -it elasticsearch-sample-es-default-0 -n elastic-system -- curl -u elastic:<password> https://localhost:9200/_cat/indices`

## Files Modified

- `clusters/elk/elastic/filebeat.yaml` - Added CSV input and volume mount
- `clusters/elk/elastic/logstash.yaml` - Added CSV filter and transformations
- `scripts/csv-to-bulk-json.py` - Python script for Kibana bulk upload
- `scripts/csv-to-bulk-json.sh` - Bash script alternative (may need fixes for complex CSV)


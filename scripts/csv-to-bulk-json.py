#!/usr/bin/env python3
"""
Convert audit-logs.csv to Elasticsearch bulk JSON format for Kibana upload.
Usage: python3 scripts/csv-to-bulk-json.py [input.csv] [index-name] > bulk-output.json
"""
import csv
import json
import sys
from datetime import datetime

def parse_json_column(value):
    """Parse JSON from first column and extract cluster name."""
    try:
        # Remove outer quotes and unescape double quotes
        cleaned = value.strip('"').replace('""', '"')
        data = json.loads(cleaned)
        return data.get('name', '')
    except:
        return ''

def parse_timestamp(ts_str):
    """Parse ISO8601 timestamp."""
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.isoformat()
    except:
        return ts_str

def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else 'test_data/audit-logs.csv'
    index_name = sys.argv[2] if len(sys.argv) > 2 else 'audit-logs'
    
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            
            for row in reader:
                if len(row) < 11:
                    continue
                
                # Parse fields
                request_stage_json = row[0] if len(row) > 0 else ''
                timestamp = row[1] if len(row) > 1 else ''
                user = row[2] if len(row) > 2 else ''
                verb = row[3] if len(row) > 3 else ''
                resource_type = row[4] if len(row) > 4 else ''
                namespace = row[5] if len(row) > 5 else ''
                resource_name = row[6] if len(row) > 6 else ''
                api_path = row[7] if len(row) > 7 else ''
                request_id = row[8] if len(row) > 8 else ''
                stage = row[9] if len(row) > 9 else ''
                http_status = row[10] if len(row) > 10 else ''
                
                # Extract cluster name from JSON
                cluster_name = parse_json_column(request_stage_json)
                
                # Build document
                doc = {
                    '@timestamp': parse_timestamp(timestamp),
                    'cluster_name': cluster_name,
                    'user': user,
                    'verb': verb,
                    'resource_type': resource_type,
                    'namespace': namespace if namespace else None,
                    'resource_name': resource_name if resource_name else None,
                    'api_path': api_path,
                    'request_id': request_id,
                    'stage': stage,
                    'http_status': int(http_status) if http_status.isdigit() else None
                }
                
                # Add severity based on HTTP status
                if doc['http_status']:
                    if 200 <= doc['http_status'] < 300:
                        doc['severity'] = 'success'
                    elif 300 <= doc['http_status'] < 400:
                        doc['severity'] = 'redirect'
                    elif 400 <= doc['http_status'] < 500:
                        doc['severity'] = 'client_error'
                    elif doc['http_status'] >= 500:
                        doc['severity'] = 'server_error'
                    else:
                        doc['severity'] = 'unknown'
                
                # Output bulk format
                action = {"index": {"_index": index_name}}
                print(json.dumps(action))
                print(json.dumps(doc))
    
    except FileNotFoundError:
        print(f"Error: File not found: {input_file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()


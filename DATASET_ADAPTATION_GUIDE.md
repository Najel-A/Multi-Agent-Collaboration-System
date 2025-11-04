# Guide: Adapting Setup for Different Datasets

This guide explains how to adapt the audit logs processing setup for different CSV datasets. The key is understanding which parts are dataset-specific vs. generic.

## Overview: What Needs to Change

When using a different dataset, you'll need to modify:

1. **CSV Column Definitions** - Match your CSV structure
2. **Field Mappings** - Map CSV columns to meaningful field names
3. **Transformations** - Adjust parsing logic for your data types
4. **Index Names** - Use dataset-specific index names
5. **File Paths** - Point to your new dataset file
6. **Bulk Upload Script** - Adapt field mappings if uploading directly

---

## Step-by-Step Adaptation Process

### Step 1: Analyze Your CSV Structure

First, examine your CSV file to understand its structure:

```bash
# View first few lines to understand structure
head -n 5 your-dataset.csv

# Count columns
head -n 1 your-dataset.csv | awk -F',' '{print NF}'

# Check if it has headers
head -n 1 your-dataset.csv
```

**Key questions:**
- Does it have a header row? (affects CSV parsing)
- How many columns?
- What data types (timestamps, numbers, JSON, strings)?
- What separator? (comma, semicolon, tab)
- Are fields quoted? (affects parsing)

### Step 2: Update Filebeat Configuration

**File:** `clusters/elk/elastic/filebeat.yaml`

**What to change:**
1. **Add new input** for your dataset (or modify existing)
2. **Set unique `data_source` field** to route to correct Logstash pipeline
3. **Update file paths** if storing in different location

**Example for new dataset:**

```yaml
# Add this as a new input in filebeat.yaml
- type: log
  enabled: true
  paths:
    - /data/my-dataset/*.csv  # Your dataset path
  fields:
    data_source: my-dataset-csv  # UNIQUE identifier for routing
  fields_under_root: false
  close_inactive: 5m
  scan_frequency: 10s
```

**Also update volume mount if needed:**
```yaml
# In volumes section, you might want separate volume or reuse existing
- name: my-dataset-data
  hostPath:
    path: /tmp/my-dataset  # Where you'll copy the CSV
    type: DirectoryOrCreate
```

### Step 3: Update Logstash Pipeline

**File:** `clusters/elk/elastic/logstash.yaml`

This is the **most important** file to modify. You need to:

#### 3a. Add CSV Column Definitions

Replace the `columns` array in the CSV filter with your column names:

```ruby
csv {
  columns => ["col1", "col2", "col3", "col4"]  # Your column names
  separator => ","  # Change if using different separator (e.g., ";" or "\t")
  quote_char => '"'  # Change if different quote character
  escape_char => '"'  # Change if different escape character
  skip_empty_columns => false  # Set true if you want to skip empty columns
}
```

**Examples:**

**With Header Row:**
If your CSV has headers, you can use them directly:
```ruby
csv {
  columns => ["timestamp", "user_id", "action", "result", "status_code"]
  separator => ","
}
```

**Without Header Row (like audit logs):**
```ruby
csv {
  columns => ["field1", "field2", "field3"]  # Descriptive names
  separator => ","
}
```

#### 3b. Add Data Type Conversions

After CSV parsing, convert fields to appropriate types:

```ruby
# Convert numeric fields
mutate {
  convert => { "status_code" => "integer" }
  convert => { "amount" => "float" }
}

# Convert boolean fields
mutate {
  convert => { "is_active" => "boolean" }
}
```

#### 3c. Parse Timestamps

Adjust timestamp parsing to match your format:

```ruby
# ISO8601 format (like audit logs)
date {
  match => [ "timestamp", "ISO8601" ]
  target => "@timestamp"
}

# Other common formats:
# Unix epoch (seconds)
date {
  match => [ "timestamp", "UNIX" ]
  target => "@timestamp"
}

# Custom format
date {
  match => [ "timestamp", "yyyy-MM-dd HH:mm:ss" ]
  target => "@timestamp"
}

# Multiple format attempts
date {
  match => [ "timestamp", "ISO8601", "yyyy-MM-dd", "MM/dd/yyyy HH:mm:ss" ]
  target => "@timestamp"
}
```

#### 3d. Parse JSON Fields (if applicable)

If any column contains JSON:

```ruby
# Parse JSON from a field
if [json_field] {
  mutate {
    gsub => [ "json_field", '""', '"' ]  # Unescape quotes if needed
    gsub => [ "json_field", '^"', '' ]
    gsub => [ "json_field", '"$', '' ]
  }
  
  json {
    source => "json_field"
    target => "parsed_json"
  }
  
  # Extract specific values
  if [parsed_json][name] {
    mutate {
      add_field => { "extracted_name" => "%{[parsed_json][name]}" }
    }
  }
}
```

#### 3e. Add Computed Fields

Add fields based on your business logic:

```ruby
# Example: Severity based on status code
if [status_code] {
  if [status_code] >= 200 and [status_code] < 300 {
    mutate { add_field => { "severity" => "success" } }
  } else if [status_code] >= 400 {
    mutate { add_field => { "severity" => "error" } }
  }
}

# Example: Category based on action
if [action] =~ /create|update|delete/ {
  mutate { add_field => { "category" => "data_modification" } }
} else {
  mutate { add_field => { "category" => "data_read" } }
}
```

#### 3f. Set Index Name

```ruby
mutate {
  add_field => { "[@metadata][index]" => "my-dataset-%{+YYYY.MM.dd}" }
}
```

#### 3g. Clean Up Temporary Fields

```ruby
mutate {
  remove_field => [ "parsed_json", "temporary_field1", "temporary_field2" ]
}
```

### Step 4: Complete Logstash Example

Here's a complete example filter block for a new dataset:

```ruby
filter {
  # Route to your dataset pipeline
  if [fields][data_source] == "my-dataset-csv" {
    # Parse CSV
    csv {
      columns => ["timestamp", "user_id", "action", "status_code", "message"]
      separator => ","
    }
    
    # Convert types
    mutate {
      convert => { "status_code" => "integer" }
      convert => { "user_id" => "integer" }
    }
    
    # Parse timestamp
    date {
      match => [ "timestamp", "ISO8601" ]
      target => "@timestamp"
    }
    
    # Add computed fields
    if [status_code] >= 400 {
      mutate { add_field => { "severity" => "error" } }
    } else {
      mutate { add_field => { "severity" => "success" } }
    }
    
    # Set index
    mutate {
      add_field => { "[@metadata][index]" => "my-dataset-%{+YYYY.MM.dd}" }
    }
  }
  
  # Keep existing pipeline for other datasets
  else if [fields][data_source] == "audit-logs-csv" {
    # ... existing audit logs processing ...
  }
  
  # Default for container logs
  else {
    mutate {
      add_field => { "[@metadata][index]" => "filebeat-%{+YYYY.MM.dd}" }
    }
  }
}
```

### Step 5: Update Bulk Upload Script (if using)

**File:** `scripts/csv-to-bulk-json.py`

Modify the script to match your CSV structure:

```python
# Update column parsing
timestamp = row[0] if len(row) > 0 else ''
user_id = row[1] if len(row) > 1 else ''
action = row[2] if len(row) > 2 else ''
# ... etc

# Build document with your fields
doc = {
    '@timestamp': parse_timestamp(timestamp),
    'user_id': int(user_id) if user_id.isdigit() else None,
    'action': action,
    # ... etc
}

# Update index name
index_name = "my-dataset"  # Your index name
```

### Step 6: Copy Dataset to Cluster

```bash
# Create directory
docker exec dev-control-plane mkdir -p /tmp/my-dataset

# Copy file
docker cp test_data/my-dataset.csv dev-control-plane:/tmp/my-dataset/

# Verify
docker exec dev-control-plane ls -l /tmp/my-dataset
```

### Step 7: Deploy and Test

```bash
# Apply changes
cd clusters/elk
make apply

# Watch logs
kubectl logs -f -l name=filebeat-sample -n elastic-system
kubectl logs -f logstash-sample-0 -n elastic-system

# Check Elasticsearch indices
kubectl exec -it elasticsearch-sample-es-default-0 -n elastic-system -- \
  curl -u elastic:<password> https://localhost:9200/_cat/indices
```

---

## Common Dataset Patterns

### Pattern 1: Simple CSV with Headers

**CSV:**
```csv
timestamp,user,action,result
2023-01-01T10:00:00Z,user123,login,success
```

**Logstash:**
```ruby
csv {
  columns => ["timestamp", "user", "action", "result"]
}
```

### Pattern 2: CSV with JSON Column

**CSV:**
```csv
timestamp,metadata,value
2023-01-01T10:00:00Z,"{""key"":""value""}",100
```

**Logstash:**
```ruby
csv {
  columns => ["timestamp", "metadata", "value"]
}

json {
  source => "metadata"
  target => "parsed_metadata"
}
```

### Pattern 3: CSV with Different Separator

**CSV (semicolon-separated):**
```csv
timestamp;user;action
2023-01-01T10:00:00Z;user123;login
```

**Logstash:**
```ruby
csv {
  columns => ["timestamp", "user", "action"]
  separator => ";"
}
```

### Pattern 4: Multiple Timestamp Formats

**Logstash:**
```ruby
date {
  match => [ "timestamp", "ISO8601", "yyyy-MM-dd HH:mm:ss", "MM/dd/yyyy" ]
  target => "@timestamp"
}
```

---

## Quick Reference: Key Changes Checklist

- [ ] **Filebeat:** Add/modify input with unique `data_source` field
- [ ] **Filebeat:** Update volume mount if different path
- [ ] **Logstash:** Update CSV `columns` array to match your CSV
- [ ] **Logstash:** Adjust `separator`, `quote_char`, `escape_char` if needed
- [ ] **Logstash:** Add data type conversions (`convert`)
- [ ] **Logstash:** Update timestamp parsing format
- [ ] **Logstash:** Add JSON parsing if needed
- [ ] **Logstash:** Add computed fields (severity, categories, etc.)
- [ ] **Logstash:** Update index name in `[@metadata][index]`
- [ ] **Logstash:** Clean up temporary fields
- [ ] **Script:** Update bulk JSON script if using direct upload
- [ ] **Deployment:** Copy CSV file to cluster
- [ ] **Testing:** Verify logs and data flow

---

## Multi-Dataset Support

You can support **multiple datasets simultaneously** by:

1. **Adding multiple Filebeat inputs**, each with unique `data_source`
2. **Adding multiple `if` blocks in Logstash** to handle each dataset
3. **Using different index names** for each dataset
4. **Organizing files in different directories**

Example Logstash structure:
```ruby
filter {
  if [fields][data_source] == "dataset-1-csv" {
    # Dataset 1 processing
  }
  else if [fields][data_source] == "dataset-2-csv" {
    # Dataset 2 processing
  }
  else if [fields][data_source] == "audit-logs-csv" {
    # Audit logs processing
  }
  else {
    # Default (container logs)
  }
}
```

---

## Troubleshooting Different Datasets

### CSV Parsing Issues

**Problem:** Fields are merged or split incorrectly
**Solution:** Check `separator`, `quote_char`, `escape_char` settings

**Problem:** JSON parsing fails
**Solution:** Clean up escaped quotes before JSON parsing

### Timestamp Issues

**Problem:** Timestamps not parsing
**Solution:** Check format string matches your data exactly

**Problem:** Wrong timezone
**Solution:** Add timezone to date filter: `timezone => "UTC"`

### Type Conversion Issues

**Problem:** Numbers treated as strings
**Solution:** Use `mutate { convert => ... }` before operations

### Missing Data

**Problem:** Some rows not appearing
**Solution:** Check CSV has consistent number of columns per row

---

## Example: Complete Adaptation

Let's say you have a **sales transactions** CSV:

```csv
2023-01-01,123,PRODUCT_A,99.99,SUCCESS,USD
2023-01-01,456,PRODUCT_B,149.50,FAILED,USD
```

**Step 1: Update Filebeat**
- Add input with `data_source: sales-transactions-csv`
- Point to `/data/sales/*.csv`

**Step 2: Update Logstash**
```ruby
if [fields][data_source] == "sales-transactions-csv" {
  csv {
    columns => ["date", "customer_id", "product", "amount", "status", "currency"]
  }
  
  mutate {
    convert => { "customer_id" => "integer" }
    convert => { "amount" => "float" }
  }
  
  date {
    match => [ "date", "yyyy-MM-dd" ]
    target => "@timestamp"
  }
  
  mutate {
    add_field => { "[@metadata][index]" => "sales-%{+YYYY.MM.dd}" }
  }
}
```

**Step 3: Copy file**
```bash
docker cp sales.csv dev-control-plane:/tmp/sales/
```

Done! Your sales data will be processed and indexed separately.

---

This guide should help you adapt the setup for any CSV dataset. The key is understanding your CSV structure and mapping it correctly through Filebeat → Logstash → Elasticsearch.

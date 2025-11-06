### AUDITS CSV INGESTION ###
# 1. Copy CSV to cluster
docker exec dev-control-plane mkdir -p /tmp/audit-logs
cd test_data
cat test_data/audit-logs.csv | docker exec -i dev-control-plane sh -c 'cat > /tmp/audit-logs/audit-logs.csv'
# 2. Apply configs
cd clusters/elk && make apply
# 3. Verify (wait 30 seconds)
docker exec dev-control-plane find /tmp/audit-logs -name "*.csv" | wc -l


### SPARK LOG INGESTION ### 
# 1. Extract and copy
docker exec dev-control-plane mkdir -p /tmp/spark-logs
cd test_data/spark-logs
tar czf - . | docker exec -i dev-control-plane tar xzf - -C /tmp/spark-logs/
cd ../..
# 2. Apply configs
cd clusters/elk && make apply
# 3. Verify (wait 30-60 seconds)
docker exec dev-control-plane find /tmp/spark-logs -name "*.log" | wc -l
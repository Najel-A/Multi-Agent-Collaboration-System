Windows users use WSL
Use Kind on Docker
Install kubectl
Install kind
Install k9s (ez for look at clusters)
Install make

Cluster create
kind create cluster --name dev
create .env

Flux setup
cd /clusters/elk

make
make apply
make reconcile

Kibana access: IN ROOT DIRECTORY
kubectl apply -f clusters/elk/helm-repo/helm-repo.yaml
kubectl apply -f clusters/elk/elastic/eck-operator.yaml
kubectl port-forward svc/kibana-sample-kb-http 5601:5601 -n elastic-system

website: https://localhost:5601

Username : elastic

Elastic password run the command below
kubectl -n elastic-system get secret elasticsearch-sample-es-elastic-user \
 -o go-template='{{.data.elastic | base64decode}}{{"\n"}}'

Add data from root folder
docker exec dev-control-plane mkdir -p /var/log/spark
docker cp spark/Spark_logs_enriched.csv dev-control-plane:/var/log/spark/Spark_logs_enriched.csv


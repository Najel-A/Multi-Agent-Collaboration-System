Windows users use WSL
Use Kind on Docker
Install kubectl
Install kind
Install k9s (ez for look at clusters)
Install make

Cluster create
kind create cluster --name monitoring-cluster-qa
create .env

Flux setup
cd /clusters/monitoring-cluster-qa

make
make apply
make reconcile

Kibana access:
kubectl apply -f helm-repo/helm-repo.yaml
kubectl apply -f elastic/eck-operator.yaml
kubectl port-forward svc/kibana-sample-kb-http 5601:5601 -n elastic-system


website: https://localhost:5601

Username : elastic

Elastic password run the command below
kubectl -n elastic-system get secret elasticsearch-sample-es-elastic-user \
 -o go-template='{{.data.elastic | base64decode}}{{"\n"}}'

kubectl -n elastic-system get secret elasticsearch-sample-es-elastic-user -o go-template='{{.data.elastic | base64decode}}'
echo



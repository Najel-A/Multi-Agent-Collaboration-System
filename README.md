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
run makefile in elastic folder

make
make apply
make reconcile

Kibana access:
kubectl apply -f clusters/elk/helm-repo/helm-repo.yaml
kubectl apply -f clusters/elk/elastic/eck-operator.yaml
kubectl port-forward svc/kibana-sample-kb-http 5601:5601 -n elastic-system

website: https://localhost:5601

Username : elastic

Elastic password run the command below
kubectl -n elastic-system get secret elasticsearch-sample-es-elastic-user \
 -o go-template='{{.data.elastic | base64decode}}{{"\n"}}'

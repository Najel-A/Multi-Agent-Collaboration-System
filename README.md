Windows users use WSL
Use Kind on Docker
Install kubectl
Install kind
Install k9s (ez for look at clusters)

Flux setup
curl -s https://fluxcd.io/install.sh | sudo bash
flux install --namespace flux-system

kubectl apply -f flux-namespace.yaml

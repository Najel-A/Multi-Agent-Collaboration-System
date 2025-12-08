SHELL := /bin/bash
ENV_FILE ?= .env

.PHONY: flux-install
flux-install:
	flux install --namespace flux-system

.PHONY: ns
ns:
	kubectl apply -f namespace/flux-namespace.yaml

.PHONY: render
render:
	@test -f "$(ENV_FILE)" || { echo "ERROR: $(ENV_FILE) not found in $$(pwd)"; exit 1; }
	set -a && source ./$(ENV_FILE) && set +a ;\
	envsubst < flux-system/gitrepository.tmpl.yaml > flux-system/gitrepository.yaml ;\
	echo "Rendered flux-system/gitrepository.yaml with BRANCH=$${BRANCH} REPO_URL=$${REPO_URL}"

.PHONY: apply
apply: ns render
	kubectl apply -f flux-system/gitrepository.yaml
	kubectl apply -f flux-system/app-sync.yaml

.PHONY: reconcile
reconcile:
	flux reconcile source git app-source -n flux-system

